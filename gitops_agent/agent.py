import argparse
import json
import os
import re
import shutil
import subprocess as sp
import time
from pathlib import Path

import toml
from git import Repo

from gitops_agent import git_operations as gops


class GitOpsAgent:
    def __init__(self, config_mode):
        self.config_file = Path(os.environ.get("GITOPS_AGENT_CONFIG", "/etc/gitops-agent/config.toml"))
        self.config = toml.load(self.config_file)
        self.apps = self.config.get("applications", [])
        self.interval = self.config.get("interval", 300)
        self.infra_name = self.config.get("infra_name")
        self.config_mode = config_mode
        self.first_run = True

    def run(self):
        if self.config_mode is True:
            default_editor = os.environ.get("EDITOR", "/usr/bin/nano")
            sp.call([default_editor, self.config_file])
            return
        while True:
            self.run_once()
            print(f"Sleeping for {self.interval} seconds...")
            time.sleep(self.interval)

    def run_once(self):
        # All apps share a single deployment-config repo per (url, branch), so clone each unique
        # (url, branch) exactly once into a shared dir, and let every app that references it read from there
        grouped = group_apps_by_repo(self.apps)
        for (app_config_url, app_config_branch), app_names in grouped.items():
            slug = gops.repo_slug(app_config_url)
            dep_cfg_local_path = shared_clone_path(app_config_url, app_config_branch)

            # Snapshot each app's config before the clone/fetch (empty dict if not yet cloned),
            # so we can still detect a first-time clone the way the per-app flow used to
            initial_configs = {
                name: gops.check_deployment_config(dep_cfg_local_path, name, self.infra_name)
                for name in app_names
            }

            # Clone/fetch the shared deployment-config repo ONCE for this (url, branch) group
            cfg_git_stats = gops.update_git_repo(
                f"{slug}@{app_config_branch}-config",
                app_config_url,
                app_config_branch,
                self.infra_name,
                dep_cfg_local_path,
            )

            # Then process every app that resolves to this shared clone
            for app_name in app_names:
                to_update, updated_cfg = self.evaluate_app(
                    app_name, dep_cfg_local_path, initial_configs[app_name]
                )
                if to_update:
                    app_git_stats, cmd_stats = self.pull_app(app_name, updated_cfg)
                else:
                    app_git_stats, cmd_stats = self.check_app(updated_cfg)
                self.push_status(
                    app_name, app_config_url, app_config_branch, cfg_git_stats, app_git_stats, cmd_stats
                )

    def evaluate_app(self, app_name, dep_cfg_local_path, initial_config):
        final_config = gops.check_deployment_config(dep_cfg_local_path, app_name, self.infra_name)
        gops.claim_ownership(final_config["code_local_path"])

        config_changed_at_repo = set(initial_config) - set(final_config)
        code_not_cloned = not final_config["code_local_path"].exists()
        code_not_at_desired_hash = not compare_git_hashes(
            final_config["code_local_path"], final_config["code_commit_hash"]
        )
        # Only consider pairs whose source exists. A missing source is skipped at copy time
        # (see pull_app), so flagging it as drift here would cause a perpetual update loop.
        config_contents_dont_match = any(
            not compare_file_contents(pair["dst_abs"], pair["src_abs"])
            for pair in final_config["config_file_pairs"]
            if pair["src_abs"].exists()
        )
        app_to_be_updated = any(
            (config_changed_at_repo, code_not_cloned, config_contents_dont_match, code_not_at_desired_hash)
        )
        return app_to_be_updated, final_config

    def pull_app(self, app_name, app_config):
        pre_updation_command = app_config["pre_updation_command"]
        post_updation_command = app_config["post_updation_command"]
        target_path = Path(app_config["code_local_path"])

        cmd_ret, cmd_logs = {}, {}

        if pre_updation_command and target_path.exists():
            print(f"Executing pre-update command for {app_name}: {pre_updation_command}...")
            cmd_ret["pre"], cmd_logs["pre"] = run_command_with_tee(pre_updation_command, target_path)

        ret, status, commit = gops.update_git_repo(
            app_name,
            app_config["code_url"],
            "",
            self.infra_name,
            target_path,
            checkout_hash=app_config["code_commit_hash"],
        )
        # copy each config file to its destination
        for pair in app_config["config_file_pairs"]:
            src_abs, dst_abs = pair["src_abs"], pair["dst_abs"]
            if not src_abs.exists():
                print(f"Skipping config copy for {app_name}: source {src_abs} does not exist")
                continue
            Path(dst_abs).parent.mkdir(parents=True, exist_ok=True)
            shutil.copy2(src_abs, dst_abs)

        if post_updation_command:
            print(f"Executing post-update command for {app_name}: {post_updation_command}...")
            cmd_ret["post"], cmd_logs["post"] = run_command_with_tee(post_updation_command, target_path)
        return (ret, status, commit), (cmd_ret, cmd_logs)

    def check_app(self, app_config):
        target_path = Path(app_config["code_local_path"])
        status, commit = gops.check_git_status(target_path)
        cmd_stats = (True, "Nothing was run")
        return (True, status, commit), cmd_stats

    def push_status(self, app_name, app_config_url, app_config_branch, cfg_git_stats, app_git_stats, cmd_stats):
        app_ret, app_status, app_commit = app_git_stats
        cfg_ret, cfg_status, cfg_commit = cfg_git_stats
        cmd_ret, cmd_logs = cmd_stats

        slug = gops.repo_slug(app_config_url)
        monitoring_branch = f"{app_config_branch}-monitoring"
        # Shared monitoring clone per (url, branch); the feedback file merges every app keyed by app_name
        dep_feedback_local_path = shared_clone_path(app_config_url, app_config_branch) + "-monitoring"
        repo_label = f"{slug}@{app_config_branch}-monitoring"

        gops.update_git_repo(
            repo_label,
            app_config_url,
            monitoring_branch,
            self.infra_name,
            dep_feedback_local_path,
            create_branch=True,
        )

        current_feedback = {
            app_name: {
                "config-updation": {
                    "updation-return-value": cfg_ret,
                    "git-status": cfg_status,
                    "git-repo-latest-commit": cfg_commit,
                },
                "app-updation": {
                    "updation-return-value": app_ret,
                    "git-status": app_status,
                    "git-repo-latest-commit": app_commit,
                },
                "extra-command-output": {"command-return-val": str(cmd_ret), "command-run-logs": str(cmd_logs)},
            },
            "last-updated": time.strftime("%Y-%m-%d %H:%M:%S", time.localtime()),
        }

        feedback_file = Path(f"{dep_feedback_local_path}/{self.infra_name}.toml")
        if feedback_file.exists():
            with open(feedback_file) as f:
                feedback = toml.load(f)
        else:
            feedback = {app_name: {"config-updation": {}, "app-updation": {}, "extra-command-output": {}}}

        # app_name will not be in feedback if it's the 1st time running for the current app (while it has run for other apps)
        if cmd_logs == "Nothing was run" and app_name in feedback:
            current_feedback[app_name]["extra-command-output"] = feedback[app_name]["extra-command-output"]

        if (
            app_name in feedback
            and app_name in current_feedback
            and json.dumps(feedback[app_name], sort_keys=True)
            == json.dumps(current_feedback[app_name], sort_keys=True)
            and not self.first_run
        ):
            print(f"Nothing to update for {app_name}...")
            return

        feedback.update(current_feedback)

        # Dump `feedback` as a toml file at feedback_file path
        with open(feedback_file, "w") as f:
            toml.dump(feedback, f)
            f.write("\n# You can render the escaped text with https://onlinetexttools.com/unescape-text\n")

        # Add, commit and push the changes
        repo = Repo(dep_feedback_local_path)
        if repo.is_dirty() or repo.untracked_files:
            repo.git.add(all=True)
            repo.git.config("user.name", self.infra_name)
            repo.git.config("user.email", "<>")
            repo.git.commit("-m", "Updated status")

        try:
            repo_remote_commit = str(repo.remotes.origin.refs[repo.active_branch.name].commit)
        except IndexError:
            repo_remote_commit = None
        repo_commit_mismatching = str(repo.active_branch.commit) != repo_remote_commit
        if repo_commit_mismatching:
            repo.git.push("--set-upstream", "origin", monitoring_branch)
            self.first_run = False
            print(f"Pushed status for {app_name} to file {feedback_file.stem} at branch {monitoring_branch}")


def compare_git_hashes(repo_path, git_hash):
    if Path(repo_path).exists():
        repo = Repo(repo_path)
        return str(repo.head.commit) == git_hash
    return False


def compare_file_contents(f1, f2):
    if f1 is None or f2 is None:
        # If they aren't supposed to exist (Ex because user hasn't defined them)
        return True
    elif not (f1.exists() and f2.exists()):
        # If they are supposed to exist, but either of them doesn't
        return False

    def strip_and_compare(file1, file2):
        with open(file1, "r") as f1, open(file2, "r") as f2:
            return f1.read().replace(" ", "").replace("\n", "") == f2.read().replace(" ", "").replace("\n", "")

    return strip_and_compare(f1, f2)


def shared_clone_path(url, branch):
    """Return the on-disk path for the shared deployment-config clone of a (repo url, branch).

    The path is keyed on the FULL repo url (via a short hash), not just the basename, so two
    distinct repos that share a basename but live under different namespaces/orgs/hosts (e.g.
    OrgA/deploy vs OrgB/deploy) never collapse onto the same directory. The human-readable slug
    is kept as a prefix for legibility; the hash provides the disambiguation.
    """
    slug = gops.repo_slug(url)
    return str(gops.APP_CONFIGS_DIR / f"{slug}@{branch}-{gops.url_hash(url)}")


def group_apps_by_repo(apps):
    """Group app entries by their (deploy-config-url, branch) so each repo is cloned only once.

    Args:
        apps: mapping of app_name -> "git_url@branch" (the [applications] table from config.toml).

    Returns:
        An ordered mapping of (git_url, branch) -> list of app_names that reference it. Order of
        first appearance is preserved so behaviour stays deterministic.
    """
    grouped = {}
    for app_name, app_config_url in apps.items():
        url, branch = parse_config(app_config_url)
        grouped.setdefault((url, branch), []).append(app_name)
    return grouped


def parse_config(git_url):
    # The only "@" that is NOT a branch separator is the scp-style userinfo prefix, which appears
    # at the very start of the url (git@host:path). Strip just that leading prefix before scanning
    # for a branch "@", so urls whose path ends in ".git@branch" (https or file://) are handled
    # correctly -- a blanket str.replace("git@", "") would also eat the ".git@" in such urls.
    scan = git_url[len("git@"):] if git_url.startswith("git@") else git_url
    if "@" in scan:
        _, git_branch = scan.rsplit("@", 1)
    else:
        git_branch = "main"

    if git_url.endswith(f"@{git_branch}"):
        git_url = git_url[: -len(f"@{git_branch}")]

    return git_url, git_branch


def run_command_with_tee(command, target_path):
    process = sp.Popen(command, stdout=sp.PIPE, stderr=sp.STDOUT, text=True, shell=True, cwd=target_path)
    output = ""

    while True:
        line = process.stdout.readline()
        if not line:
            break
        print("\t" + line, end="")
        output += line

    process.wait()
    output = remove_ansi_escape_sequences(output)
    return process.returncode, output


def remove_ansi_escape_sequences(text):
    # Define the regular expression pattern for ANSI escape sequences
    ansi_escape_pattern = re.compile(r"\x1b\[([0-9;]*[mGKF])")
    # Use sub() method to replace all occurrences of the pattern with an empty string
    cleaned_text = ansi_escape_pattern.sub("", text)
    return cleaned_text


def main():
    # Use argparse to check if the user wants to set configuration
    parser = argparse.ArgumentParser()
    parser.add_argument("--configure", action="store_true", help="Configure the gitops agent")
    args = parser.parse_args()

    agent = GitOpsAgent(args.configure)
    agent.run()


if __name__ == "__main__":
    main()
