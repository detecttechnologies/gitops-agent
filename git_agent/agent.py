import argparse
from copy import deepcopy
import os
import shutil
import subprocess
import time
from pathlib import Path

import toml
from git import Repo


class GitAgent:
    def __init__(self, config_mode):
        self.config_file = Path("/etc", "git-agent", "config.toml")
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
                self.update_app_sources(app_name, app_config)
                self.run_custom_commands(app_config)
                time.sleep(app_config["interval"])

    def update_app_sources(self, app_name, app_config):
        app_config_url = app_config["config_url"]

        self.__check_commit_of_this_infra(app_name, app_config)
        initial_config = deepcopy(app_config)
        self.__update_single_repo(app_name, app_config_url, "/opt/git-agent/app-configs")
        self.__check_commit_of_this_infra(app_name, app_config)
        config_changed = self.__compare_states(initial_config, app_config)

        if config_changed:
            self.__update_single_repo(
                app_name, app_config["code_url"], app_config["code_local_path"], checkout_hash=app_config["code_hash"]
            )
            # copy config file to code folder
            shutil.copy(
                (app_config["config_file"]), app_config["code_local_path"] / app_config["config_relative_path_in_code"]
            )

    def __check_commit_of_this_infra(self, app_name, app_config):
        infra_meta_file = Path(f"/opt/git-agent/app-configs/{app_name}/{self.infra_name}/infra_meta.toml")
        if not infra_meta_file.parent.parent.exists():
            return  # The config directory hasn't been cloned yet
        elif not infra_meta_file.exists():
            raise FileNotFoundError(f"Infra meta file not found: {infra_meta_file}")

        with open(infra_meta_file) as f:
            infra_meta = toml.load(f)
            app_meta = infra_meta[app_name]

        app_config["config_file"] = Path(
            f"/opt/git-agent/app-configs/{app_name}/{self.infra_name}/", app_meta["config_file"]
        )
        app_config["config_relative_path_in_code"] = app_meta["config_relative_path_in_code"]
        app_config["code_url"] = app_meta["code_url"]
        app_config["code_hash"] = app_meta["code_hash"]
        app_config["code_local_path"] = Path(app_meta["code_local_path"])
        app_config["post_updation_command"] = app_meta["post_updation_command"]

        return app_config

    def __update_single_repo(self, app_name, app_url, local_path, checkout_hash=None):
        app_branch = app_url.rsplit("@", 1) if "@" in app_url else "main"

        if not Path(local_path).exists():
            print(f"Cloning repository {app_name}...")
            repo = Repo.clone_from(app_url, local_path)
        else:
            print(f"Pulling latest changes for {app_name}...")
            # FIXME: This assumes right now that there will be no conflict, there's no `git diff` content, and that we're on the same branch already
            repo = Repo(local_path)

        # Checkout the relevant branch/hash
        if checkout_hash:
            repo.git.checkout(checkout_hash)
        else:
            repo.remotes.origin.pull(app_branch)

    def run_custom_commands(self, app_config):
        post_updation_command = app_config["post_updation_command"]
        target_path = str(Path.cwd() / app_config["name"])
        print(f"Executing post-update command for {app_config['name']}...")
        subprocess.run(post_updation_command, shell=True, cwd=target_path)


def main():
    # Use argparse to check if the user wants to set configuration
    parser = argparse.ArgumentParser()
    parser.add_argument("--configure", action="store_true", help="Configure the Git agent")
    args = parser.parse_args()

    agent = GitAgent(args.configure)
    agent.run()


if __name__ == "__main__":
    main()
