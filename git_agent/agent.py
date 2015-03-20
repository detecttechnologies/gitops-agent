import os
import subprocess
import time
import toml
from git import Repo


class GitAgent:
    def __init__(self, config_file):
        self.config = toml.load(config_file)
        self.repos = self.config.get("repositories", [])

    def clone_or_pull_repo(self, repo_config):
        repo_name = repo_config["name"]
        repo_url = repo_config["url"]
        branch = repo_config["branch"]
        ssh_token = repo_config["ssh_token"]
        target_path = os.path.join(os.getcwd(), repo_name)

        if not os.path.exists(target_path):
            print(f"Cloning repository {repo_name}...")
            Repo.clone_from(repo_url, target_path, branch=branch, env={"GIT_SSH_COMMAND": f"ssh -i {ssh_token}"})
        else:
            print(f"Pulling latest changes for {repo_name}...")
            repo = Repo(target_path)
            repo.remotes.origin.pull(branch)

    def execute_command(self, repo_config):
        command = repo_config["command"]
        target_path = os.path.join(os.getcwd(), repo_config["name"])
        print(f"Executing command for {repo_config['name']}...")
        subprocess.run(command, shell=True, cwd=target_path)

    def monitor_repositories(self):
        while True:
            for repo_config in self.repos:
                self.clone_or_pull_repo(repo_config)
                # Here, you would add logic to detect changes and call execute_command accordingly
                self.execute_command(repo_config)
                time.sleep(repo_config["interval"])


def main():
    import argparse

    parser = argparse.ArgumentParser(description="Git Agent")
    parser.add_argument("-c", "--config", required=True, help="Path to the configuration file")
    args = parser.parse_args()

    agent = GitAgent(args.config)
    agent.monitor_repositories()
