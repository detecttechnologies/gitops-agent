import argparse
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
        # Create config file if it doesn't already exist
        if not self.config_file.exists():
            Path(self.config_file.parent).mkdir(parents=True, exist_ok=True)
            config_template = Path(__file__).parent / "templates" / "config.toml"
            shutil.copy(config_template, self.config_file)
        self.config = toml.load(self.config_file)
        self.apps = self.config.get("applications", [])
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
        app_code_url = app_config["code_url"]
        app_code_branch = app_code_url.rsplit("@", 1) if "@" in app_code_url else "main"
        app_config_url = app_config["config_url"]
        app_config_branch = "main"
        ssh_token = app_config["ssh_token"]
        code_local_path = app_config.get("local_path", str(Path.home(), app_name))

        self.__update_single_repo(app_name, app_code_url, app_code_branch, ssh_token, code_local_path)
        self.__update_single_repo(app_name, app_config_url, app_config_branch, ssh_token, "/opt/git-agent/app-configs")

    def __update_single_repo(self, app_name, app_url, branch, ssh_token, local_path):
        if not Path(local_path).exists():
            print(f"Cloning repository {app_name}...")
            Repo.clone_from(app_url, local_path, branch=branch, env={"GIT_SSH_COMMAND": f"ssh -i {ssh_token}"})
        else:
            print(f"Pulling latest changes for {app_name}...")
            # FIXME: This assumes right now that there will be no conflict, there's no `git diff` content, and that we're on the same branch already
            repo = Repo(local_path)
            repo.remotes.origin.pull(branch)

    def run_custom_commands(self, app_config):
        command = app_config["command"]
        target_path = str(Path.cwd() / app_config["name"])
        print(f"Executing post-update command for {app_config['name']}...")
        subprocess.run(command, shell=True, cwd=target_path)


def main():
    # Use argparse to check if the user wants to set configuration
    parser = argparse.ArgumentParser()
    parser.add_argument("--configure", action="store_true", help="Configure the Git agent")
    args = parser.parse_args()

    agent = GitAgent(args.configure)
    agent.run()
