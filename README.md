# Git Watcher Agent

Git Watcher Agent is a Python-based tool that continuously monitors remote Git repositories for changes and performs actions based on those changes. This tool is designed to help manage applications and their configurations on on-premise servers / classical & conventional infrastructure / VMs / etc without the need for Kubernetes.

## Features

- Clone and pull 1. Software updates 2. Configuration Updates from multiple Git repositories
- Execute custom commands when changes are detected
- Configurable via `.toml` files
- Designed for watching git repositories for non-k8s server environments

## Installation

You can install Git Watcher Agent via pip:

## Usage

### Configuration

Create a config.toml file with the following structure:

```toml
[[repositories]]
name = "app1"
url = "git@github.com:username/repo1.git"
branch = "main"
ssh_token = "your_ssh_token_here"
command = "deploy.sh"
interval = 120  # in seconds

[[repositories]]
name = "app2"
url = "git@github.com:username/repo2.git"
branch = "develop"
ssh_token = "your_ssh_token_here"
command = "update.sh"
interval = 300  # in seconds
```

## Running the Agent

To start the agent, use the following command:

```sh
git-agent -c config.toml
```

## Contributing

This project is an initial PoC, and contributions are more than welcome!
