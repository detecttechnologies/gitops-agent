import argparse
import os
import shutil
import subprocess
import time
from pathlib import Path

import toml
from git import Repo, GitCommandError


class GitOpsAgent:
    def __init__(self, config_mode):
        self.config_file = Path("/etc", "gitops-agent", "config.toml")
        self.config = toml.load(self.config_file)
        self.apps = self.config.get("applications", [])
        self.infra_name = self.config.get("infra_name")
        self.config_mode = config_mode

    def run(self):
        if self.config_mode is True:
            default_editor = os.environ.get("EDITOR", "/usr/bin/nano")
            subprocess.call([default_editor, self.config_file])
            return
        while True:
            for app_name, app_config in self.apps.items():
                app_config_url, app_config_branch = self.__parse_config(app_config)
                updated_config, cfg_ret, cfg_status = self.pull_config(app_name, app_config_url, app_config_branch)
                if updated_config:
                    app_ret, app_status = self.pull_app(app_name, updated_config)
                else:
                    app_ret, app_status = True, "Not checked for updates"
                self.push_status(app_name, app_config_url, app_config_branch, cfg_ret, cfg_status, app_ret, app_status)
                time.sleep(app_config.get("interval", 300))

    def pull_config(self, app_name, app_config_url, app_config_branch):
        initial_config = self.__check_commit_of_this_infra(app_name)
        ret, status = self.__update_git_repo(
            f"{app_name}-config", app_config_url, app_config_branch, f"/opt/gitops-agent/app-configs/{app_name}"
        )
        final_config = self.__check_commit_of_this_infra(app_name)

        config_changed_at_repo = set(initial_config) - set(final_config)
        code_not_cloned = not final_config["code_local_path"].exists()
        if final_config["config_file_dst_path_in_local"].exists():
            config_contents_dont_match = (
                final_config["config_file_dst_path_in_local"].read_bytes()
                != final_config["config_file_path"].read_bytes()
            )
        else:
            config_contents_dont_match = False
        app_to_be_updated = config_changed_at_repo or code_not_cloned or config_contents_dont_match
        final_config = False if not app_to_be_updated else final_config
        return final_config, ret, status

    def push_status(self, app_name, app_config_url, app_config_branch, cfg_ret, cfg_status, app_ret, app_status):
        app_name2 = f"{app_name}-monitoring"
        app_config_branch = f"{app_config_branch}-monitoring"
        self.__update_git_repo(
            app_name2,
            app_config_url,
            app_config_branch,
            f"/opt/gitops-agent/app-configs/{app_name2}",
            create_branch=True,
        )

        feedback_file = Path(f"/opt/gitops-agent/app-configs/{app_name2}/{self.infra_name}.toml")

        if feedback_file.exists():
            with open(feedback_file) as f:
                feedback = toml.load(f)
        else:
            feedback = {}
        feedback.update(
            {
                app_name: {
                    "config-updation": cfg_ret,
                    "config-updation-status": cfg_status,
                    "app-updation": app_ret,
                    "app-updation-status": app_status,
                }
            }
        )

        # Dump `feedback` as a toml file at feedback_file path
        with open(feedback_file, "w") as f:
            toml.dump(feedback, f)

        # Add, commit and push the changes
        repo = Repo(f"/opt/gitops-agent/app-configs/{app_name2}")
        if repo.is_dirty():
            repo.git.add(all=True)
            repo.git.commit("-m", "Updated status")

        try:
            repo_remote_commit = str(repo.remotes.origin.refs[repo.active_branch.name].commit)
        except IndexError:
            repo_remote_commit = None
        repo_commit_mismatching = str(repo.active_branch.commit) != repo_remote_commit
        if repo_commit_mismatching:
            repo.config_writer().set_value("user", "name", self.infra_name).release()
            repo.git.push("--set-upstream", "origin", app_config_branch)
            print(f"Pushed status for {app_name} to file {feedback_file.stem} at branch {app_config_branch}")

    def pull_app(self, app_name, app_config):
        ret, status = self.__update_git_repo(
            app_name,
            app_config["code_url"],
            "",
            app_config["code_local_path"],
            checkout_hash=app_config["code_hash"],
        )
        # copy config file to code folder
        shutil.copy(app_config["config_file_path"], app_config["config_file_dst_path_in_local"])
        post_updation_command = app_config["post_updation_command"]
        if post_updation_command:
            target_path = Path(app_config["code_local_path"])
            print(f"Executing post-update command for {app_name}...")
            subprocess.run(post_updation_command, shell=True, cwd=target_path)
        return ret, status

    def __parse_config(self, app_config):
        git_url = app_config["config_url"]
        if "@" in git_url.replace("git@", "", 1):
            _, git_branch = git_url.rsplit("@", 1)
        else:
            git_branch = "main"

        if git_url.endswith(f"@{git_branch}"):
            git_url = git_url[: -len(f"@{git_branch}")]

        return git_url, git_branch

    def __check_commit_of_this_infra(self, app_name):
        infra_meta_file = Path(f"/opt/gitops-agent/app-configs/{app_name}/{self.infra_name}/infra_meta.toml")
        if not infra_meta_file.parent.parent.exists():
            print(infra_meta_file.parent.parent, " does not yet exist")
            return {}  # The config directory hasn't been cloned yet, so let the config be cloned
        elif not infra_meta_file.exists():
            raise FileNotFoundError(f"Infra meta file not found: {infra_meta_file}")

        with open(infra_meta_file) as f:
            infra_meta = toml.load(f)
            app_meta = infra_meta[app_name]

        curr_app_config = {}
        curr_app_config["config_file_path"] = Path(
            f"/opt/gitops-agent/app-configs/{app_name}/{self.infra_name}/",
            app_meta["config_relative_path_in_this_folder"],
        )
        curr_app_config["code_url"] = app_meta["code_url"]
        curr_app_config["code_hash"] = app_meta["code_hash"]
        curr_app_config["code_local_path"] = Path(app_meta["code_local_path"])
        curr_app_config["config_file_dst_path_in_local"] = (
            Path(app_meta["code_local_path"]) / app_meta["config_relative_path_in_code"]
        )
        curr_app_config["post_updation_command"] = app_meta.get("post_updation_command", None)
        curr_app_config["interval"] = app_meta.get("interval", 300)

        return curr_app_config

    def __update_git_repo(self, app_name, app_url, app_branch, local_path, checkout_hash=None, create_branch=False):
        if app_url.endswith(f"@{app_branch}"):
            app_url = app_url[: -len(f"@{app_branch}")]

        print(f"Updating repository {app_name}...")
        if not Path(local_path).exists():
            repo = Repo.clone_from(app_url, local_path)
        else:
            repo = Repo(local_path)

        # Update the local with changes from remote
        repo.git.fetch("-p")

        try:
            if checkout_hash:
                repo.git.checkout(checkout_hash)
            else:
                if create_branch:
                    # Check if the branch exists
                    all_branches = repo.refs
                    branch_present = [e for e in all_branches if app_branch in str(e)]
                    if branch_present:
                        repo.git.checkout(app_branch)
                        repo.git.pull(app_url, app_branch)
                    else:
                        repo.git.checkout("--orphan", app_branch)
                        files = repo.git.ls_files()
                        if files:
                            repo.git.rm("-rf", ".")
                else:
                    repo.git.checkout(app_branch)
                    repo.git.pull(app_url, app_branch)
            update_status = True
        except GitCommandError as err:
            print(f"Error occurred while updating repository {app_name}: {err}")
            update_status = False
        git_status = repo.git.status()
        return update_status, git_status


def main():
    # Use argparse to check if the user wants to set configuration
    parser = argparse.ArgumentParser()
    parser.add_argument("--configure", action="store_true", help="Configure the gitops agent")
    args = parser.parse_args()

    agent = GitOpsAgent(args.configure)
    agent.run()


if __name__ == "__main__":
    main()
