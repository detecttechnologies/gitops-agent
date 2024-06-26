# GitOps Agent

This is a Python-based tool that continuously monitors remote Git repositories for changes and performs actions based on those changes. It started as a simple git-repo watcher. However, it has evolved into a Gitops-enablement tool to help manage applications and their configurations on on-premise servers / classical & conventional infrastructure / VMs / etc without the need for Kubernetes.

This tool focuses largely on application and configuration management, and not on infrastructure setup as with other Gitops tools linked with IaaC providers. This tool is called as an "agent", and not as an "operator" as is traditional k8s speak because in conventional infrastructure like physical servers, any client in a client-server architecture is generally called an "agent".

## Features

- Designed for GitOps for non-k8s server environments for multiple simultaneous applications
- Handle CD for a server for a 1. Software applications 2. Configuration for said applications
- Execute custom commands when the deployment artifacts or the configuration changes
- Observability with no extra dependencies within the same config git repo

## Installation

If the device doesn't already have an SSH key, you can create one with `ssh-keygen`. Ensure that this device SSH key is registered as a Deploy Key in the

1. Application Repository
2. Application Configuration Repository

You can now install GitOps Agent by running the below command.

```bash
sudo apt-get update
sudo apt-get install -y curl
curl -sL https://bit.ly/gitops-agent-installer | sudo bash
```

## Configuration

After installation, the agent is automatically running as a systemd service on your system all the time. You can configure the run settings by running the below commands

```sh
gitops-agent --configure         ## <Make the changes you want in the editor>
sudo systemctl restart gitops-agent.service
```

## Online Repository Configuration

Do ensure that your app-configuration git repository allows pushes from unverified users. On Gitlab, this option might be enabled by default, and you have to disable it manually for your app-configuration repository. To do so on Gitlab, you can go to Repository Settings --> Repository --> Push rules --> Disable `Reject unverified users`

## To-Do

- Add app-deletion functionality, if section is removed in configuration
- Push the status back only after all apps have been updated, rather than doing it for all individual apps
- Sample Gitlab/Github pipelines for validating updates to the config repo
- Evaluate if we should force-reset local changes...currently, if the config repo is already up to date, but someone has manually made a change to a file, it doesn't overwrite the file (as it's a change that's not staged for commit, but there's no need to commit as there's no incoming change)
- Even if the config repo hasn't changed, every loop we should check if the commit of the app has changed (e.g. through manual operations) - i.e. enforce consistency with the gitops-agent
- Add diagrams showing data flow
- Please note that this has only been tested on Ubuntu. It is known to not run properly on WSL due to an [issue](https://github.com/gitpython-developers/GitPython/issues/1902) with how GitPython handles WSL paths

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

### Near Alternatives

There are some near alternatives to this that you should consider before utilizing this. This solution is largely meant to work with independent isolated systems without depending on any other systems (like Mender/CFEngine) being installed.

1. FluxCD/ArgoCD (if you have a k8s cluster)
2. Integrating a tool like Mender and Ansible with your git hosting solution (github/gitlab) - [Blog by Siemens detailing the same](https://opensource.siemens.com/events/2023/slides/Matthias_Luescher_Automating_and_managing_an_IoT_Fleet_Using_Git.pdf)
3. A configuration management tool like CFEngine
