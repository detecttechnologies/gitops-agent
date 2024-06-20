import argparse
import os
import shutil
import subprocess
import time
from pathlib import Path

import toml
from git import Repo


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
                updated_config = self.update_app_config_source(app_name, app_config)
                if updated_config:
                    self.update_app(app_name, updated_config)
                time.sleep(app_config.get("interval", 300))

    def update_app_config_source(self, app_name, app_config):
        app_config_url = app_config["config_url"]
        initial_config = self.__check_commit_of_this_infra(app_name)
        self.__update_git_repo(app_name, app_config_url, f"/opt/gitops-agent/app-configs/{app_name}")
        final_config = self.__check_commit_of_this_infra(app_name)
        config_changed = set(initial_config) - set(final_config)
        code_not_cloned = not final_config["code_local_path"].exists()
        config_not_copied = not final_config["config_file_dst_path_in_local"].exists()
        # TODO: also check if the hash of the config file matches, or there's a git diff, so that we enforce
        # consistency with final git state
        app_to_be_updated = config_changed or code_not_cloned or config_not_copied
        return final_config if app_to_be_updated else None

    def __check_commit_of_this_infra(self, app_name):
        infra_meta_file = Path(f"/opt/gitops-agent/app-configs/{app_name}/{self.infra_name}/infra_meta.toml")
        if not infra_meta_file.parent.parent.exists():
            print(infra_meta_file.parent.parent, " does not exist")
            return  # The config directory hasn't been cloned yet
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

    def __update_git_repo(self, app_name, app_url, local_path, checkout_hash=None):
        if "@" in app_url.lstrip("git@"):
            _, app_branch = app_url.rsplit("@", 1)
        else:
            app_branch = "main"
        app_url = app_url.rstrip(f"@{app_branch}")

        if not Path(local_path).exists():
            print(f"Cloning repository {app_name}...")
            repo = Repo.clone_from(app_url, local_path)
        else:
            print(f"Pulling latest changes for {app_name}...")
            # FIXME: This assumes right now that there will be no conflict, there's no `git diff` content, and that we're on the same branch already
            repo = Repo(local_path)

        if checkout_hash:
            repo.git.checkout(checkout_hash)
        else:
            # Pull the latest version of app_branch in place
            repo.git.pull(app_url, app_branch)

    def update_app(self, app_name, app_config):
        self.__update_git_repo(
            app_name,
            app_config["code_url"],
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


def main():
    # Use argparse to check if the user wants to set configuration
    parser = argparse.ArgumentParser()
    parser.add_argument("--configure", action="store_true", help="Configure the gitops agent")
    args = parser.parse_args()

    agent = GitOpsAgent(args.configure)
    agent.run()


if __name__ == "__main__":
    main()
