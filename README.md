# GitOps Agent

This is a Python-based tool that continuously monitors remote Git repositories for changes and performs actions based on those changes. It started as a simple git-repo watcher. However, it has evolved into a Gitops-enablement tool to help manage applications and their configurations on on-premise servers / classical & conventional infrastructure / VMs / etc without the need for Kubernetes.

This tool focuses largely on application and configuration management, and not on infrastructure setup as with other Gitops tools linked with IaaC providers. This tool is called as an "agent", and not as an "operator" as is traditional k8s speak because in conventional infrastructure like physical servers, any client in a client-server architecture is generally called an "agent".

## Features

- Clone and pull 1. Software updates 2. Configuration Updates from multiple Git repositories
- Execute custom commands when changes are detected
- Designed for GitOps for non-k8s server environments

## Installation

You can install GitOps Agent by running the below command. Please note that this has only been tested on Ubuntu.

```bash
sudo apt-get update
sudo apt-get install -y curl
curl -sL https://raw.githubusercontent.com/rsnk96/gitops-agent/main/install.sh | sudo bash
```

The content of this repository just acts as an agent installed locally on the intended deployment-server. You will additionally require one or many git repositories that you host (can be private git repos), where you will maintain the actual configuration of applications and configurations you'd like deployed on the destination device.

## Usage

Ensure thatyour SSH key is registered so that you can clone both the configuration and code repositories

After installation, the agent is automatically running as a systemd service on your system all the time. You can configure the run settings by running the below commands

```sh
gitops-agent --configure         ## <Make the changes you want in the editor>
sudo systemctl restart gitops-agent.service
```

## Contributing

This project is an initial PoC, and contributions are more than welcome!

## References

Here are some other related references:

1. <https://www.gitops.tech/>
2. <https://github.com/kitplummer/goa>
3. <https://github.com/kolbasa/git-repo-watcher>
4. <https://endjin.com/blog/2020/10/gitops-not-just-for-kubernetes>
5. <https://samiyaakhtar.medium.com/gitops-observability-visualizing-the-journey-of-a-container-5f6ef1f3c9d2>
6. <https://github.com/weaveworks/awesome-gitops>
