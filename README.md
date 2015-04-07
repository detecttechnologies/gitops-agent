# Git Watcher Agent

Git Watcher Agent is a Python-based tool that continuously monitors remote Git repositories for changes and performs actions based on those changes. This tool is designed to help manage applications and their configurations on on-premise servers / classical & conventional infrastructure / VMs / etc without the need for Kubernetes.

## Features

- Clone and pull 1. Software updates 2. Configuration Updates from multiple Git repositories
- Execute custom commands when changes are detected

- Designed for Git for non-k8s server environments

## Installation

You can install Git Agent by running the below command. Please note that this has only been tested on Ubuntu.

```bash
sudo apt-get update
sudo apt-get install -y curl
curl -sL <hosted endpoint for install.sh> | sudo bash
```

## Usage

After installation, the agent is automatically running as a systemd service on your system all the time. You can configure the run settings by running the below commands

```sh
git-agent --configure         ## <Make the changes you want in the editor>
sudo systemctl restart git-agent.service
```

## Contributing

This project is an initial PoC, and contributions are more than welcome!
