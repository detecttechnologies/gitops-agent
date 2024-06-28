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
        self.config_file = Path("/etc", "gitops-agent", "config.toml")
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
            for app_name, app_config_url in self.apps.items():
                app_config_url, app_config_branch = parse_config(app_config_url)
                to_update, updated_cfg, cfg_git_stats = self.pull_config(app_name, app_config_url, app_config_branch)
                if to_update:
                    app_git_stats, cmd_stats = self.pull_app(app_name, updated_cfg)
                else:
                    app_git_stats, cmd_stats = self.check_app(updated_cfg)
                self.push_status(app_name, app_config_url, app_config_branch, cfg_git_stats, app_git_stats, cmd_stats)
            print(f"Sleeping for {self.interval} seconds...")
            time.sleep(self.interval)

    def pull_config(self, app_name, app_config_url, app_config_branch):
        initial_config = gops.check_commit_of_this_infra(app_name, self.infra_name)
        ret, status, comm = gops.update_git_repo(
            f"{app_name}-config",
            app_config_url,
            app_config_branch,
            self.infra_name,
            f"/opt/gitops-agent/app-configs/{app_name}",
        )
        final_config = gops.check_commit_of_this_infra(app_name, self.infra_name)

        config_changed_at_repo = set(initial_config) - set(final_config)
        code_not_cloned = not final_config["code_local_path"].exists()
        code_not_at_desired_hash = not compare_git_hashes(
            final_config["code_local_path"], final_config["code_commit_hash"]
        )
        config_contents_dont_match = not compare_file_contents(
            final_config["config_dst_path_abs"], final_config["config_src_path_abs"]
        )
        app_to_be_updated = any(
            (config_changed_at_repo, code_not_cloned, config_contents_dont_match, code_not_at_desired_hash)
        )
        return app_to_be_updated, final_config, (ret, status, comm)

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
        # copy config file to code folder
        if app_config["config_src_path_abs"] and app_config["config_dst_path_abs"]:
            shutil.copy2(app_config["config_src_path_abs"], app_config["config_dst_path_abs"])

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
        app_name2 = f"{app_name}-monitoring"
        app_config_branch = f"{app_config_branch}-monitoring"
        gops.update_git_repo(
            app_name2,
            app_config_url,
            app_config_branch,
            self.infra_name,
            f"/opt/gitops-agent/app-configs/{app_name2}",
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

        feedback_file = Path(f"/opt/gitops-agent/app-configs/{app_name2}/{self.infra_name}.toml")
        if feedback_file.exists():
            with open(feedback_file) as f:
                feedback = toml.load(f)
        else:
            feedback = {app_name: {"config-updation": {}, "app-updation": {}, "extra-command-output": {}}}

        if cmd_logs == "Nothing was run":
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
            f.write("\n# You can render the escaped text with https://onlinetexttools.com/unescape-text")

        # Add, commit and push the changes
        repo = Repo(f"/opt/gitops-agent/app-configs/{app_name2}")
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
            repo.git.push("--set-upstream", "origin", app_config_branch)
            self.first_run = False
            print(f"Pushed status for {app_name} to file {feedback_file.stem} at branch {app_config_branch}")


def compare_git_hashes(repo_path, git_hash):
    repo = Repo(repo_path)
    return str(repo.head.commit) == git_hash


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


def parse_config(git_url):
    if "@" in git_url.replace("git@", "", 1):
        _, git_branch = git_url.rsplit("@", 1)
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
