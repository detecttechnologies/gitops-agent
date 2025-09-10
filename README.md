# GitOps Agent

This is a Python-based tool that continuously monitors remote Git repositories for changes and performs actions based on those changes. It started as a simple git-repo watcher in [CVG, CFI, IITM](https://github.com/iitmcvg). However, it has evolved into a Gitops-enablement tool to help manage applications and their configurations on on-premise servers / classical & conventional infrastructure / VMs / etc without the need for Kubernetes.

## 1. Why

You might ask yourself why such a solution would be needed?

![Gitops-agent-1](https://github.com/detecttechnologies/gitops-agent/assets/10851575/7307e5ff-4c6c-4de8-b057-b543c6f11f72)

If you find yourself in a situation like the below, where you are manually deploying many software applications onto multiple servers, there can be many issues that you frequently encounter:

1. It takes a lot of time to be ssh-ing into deployment devices (production nodes) and manually configuring it. This can be a very serious concern if your production nodes are edge devices with very sparse internet
2. It's difficult to keep track of the version of applications and configuration present across all production nodes
3. All the issues mentioned in the [Why Gitops](https://www.gitops.tech/#why-should-i-use-gitops) section of the Weaveworks blog

This is exactly the type of situation that Gitops was designed to solve. However, most existing gitops solutions like ArgoCD/FluxCD/Spinnaker/etc require you to embrace an entire device & application management solution like Kubernetes. This repo aims to enable a much simpler implementation of Gitops.

It allows you to run a gitops workflow on classical infrastructure like on-prem servers and edge devices, while consuming very little resources, and having very little dependencies. It even has support for observability/monitoring of the gitops workflow with no dependency except Git!

### 1.1 How does this work?

![Gitops-agent-2](https://github.com/detecttechnologies/gitops-agent/assets/10851575/4c3c806d-2dcf-4040-bc17-1601802d6dc8)

The above is a simplified diagram that explains how this program works. It introduces a `gitops-agent` that runs as a systemd service on your ubuntu/debian machine (in reality, any unix device with systemd+python should be able to run this with a bit of tweaking) that enables Devs and Devops folks to focus on working with systems where they are not constrained by prod-device availability, working across a team. It works by

What's more, using simple branch protection rules that are already available on github/gitlab/etc, you can also implement a workflow with the necessary approvals as you so desire, while ensuring a change management system where all changes in production are accounted for!

Extending the above workflow to multiple applications and prod devices, it would look like the below:

![Gitops-agent-3](https://github.com/detecttechnologies/gitops-agent/assets/10851575/57a19c80-a77e-4402-80a0-f68841db86f6)

### 1.2 Is this for me?

Before you decide to use this as your Gitops solution, you should ask yourself why you'd prefer this and not any other Gitops offerring. If you want a slim (min-dependency and runtime overhead) solution with the below features

- GitOps for non-k8s server environments for multiple simultaneous applications.
- CD (Continuous Delivery, not Continuous Deployments) to production-servers for Software applications and their configurations.
- Detect drift in configuration or in the git repo on the production server.
- Execute custom commands before and after and drift is corrected.
- Observability of the status of gitops execution on-prod-device with no extra dependencies within the same deployment-management git repo.
- Accountability, traceability and transparency for all changes made on prod.
- Change approval workflows implemented within github/gitlab.

and not any of the other features that other Gitops solutions have (covered in the below section), then this approach could be for you :)

### 1.3 Differences from other implementations of gitops

- This tool focuses largely on application and configuration management, and not on infrastructure setup as generally with other Gitops tools.
- It isn't necessary to use docker containers with this tool, as is with most other Gitops tools. This is because a lot of lower-level applications may not run on docker. However, if you would like to run something on a docker-container, you can always make use of the `pre_updation_command` and `post_updation_command` flags available with this tool to run any custom commands, including pulling/building new docker images, etc.
- This tool is called as an "agent", and not as an "operator" as is traditional k8s speak because in conventional infrastructure like physical servers, any client in a client-server architecture is generally called an "agent".

## 2. Usage

### 2.1 Preparation

- Ensure that your prod-device's root user has a passwordless ssh-key.
  - You can evaluate this by checking if `/root/.ssh` has any files present
  - If not present, you can create one by running `sudo ssh-keygen`, and pressing Enter at all the prompts after that
- Ensure that each of the applications you would like to maintain through this gitops-agent have a separate git repository
- Ensure that you have a separate git repository for deployment management.
- If applications are already existing on the prod-device, then ensure you bring the locally cloned app-repos to a state where the `git status` is as clean as possible.

### 2.2 Git Remote Repository Configuration

Configure your online git repo -

- Application Git Repos:
  - Add your debian root user's ssh-public key as a `Deploy Key` on your git repo
- Deployment Configuration Git Repo:
  - Ensure that there is a folder with the exact same name that you are nick-named for your prod-device, and there's a `infra_meta.toml` file within that folder describing the applications that you would like to monitor with this gitops-agent. Additionally, if you have any configuration files, you can also host them here.
  - Add your debian root user's ssh-public key as a `Deploy Key` on your git repo **with write access to this repo** (needs to write-back the feedback)
  - Ensure that your app-configuration git repository allows pushes from unverified users. On Gitlab, this option might be enabled by default, and you have to disable it manually for your app-configuration repository. To do so on Gitlab, you can go to Repository Settings --> Repository --> Push rules --> Disable `Reject unverified users`
  - Employ any approval workflows you would like by restricting who can push/approve changes to the branch that will be used as the branch for the gitops workflow

### 2.3 Agent Installation on Prod-Device

You can now install GitOps Agent by running the below command.

```bash
sudo apt-get update
sudo apt-get install -y curl
curl -sL https://bit.ly/gitops-agent-installer | sudo bash
```

If you use a git provider other than github.com/gitlab.com, you need to register its ssh keys using `sudo ssh-keyscan <custom_git_host_endpoint>`

### 2.4 Prod-Device Configuration

After installation, the agent is automatically running as a systemd service on your system all the time. You can configure the run settings by running the below commands

```sh
sudo gitops-agent --configure         ## <Make the changes you want in the editor>
sudo systemctl restart gitops-agent.service
```

After you are done, you can check the logs of the agent while its running by running

```sh
sudo journalctl -n 100 -fu gitops-agent  # Will keep following the logs of the gitops-agent
```

## Troubleshooting

- Please note that this has only been tested on Ubuntu. It is known to not run properly on WSL due to an [issue](https://github.com/gitpython-developers/GitPython/issues/1902) with how GitPython handles WSL paths

### Installing this solution when you have already installed some apps manually

The main steps to keep in mind when installing the gitops-agent mid-lifecycle for any project is:

1. Ensure that `git status` is as clean as possible when installing

### Forcing a push to the monitoring branch

If during initial installation, you would like to force it to push to the monitoring branch even though there may be nothing to update there, you can easily do the same by deleting your local deployment-configs, so that it appears as if it's a new installation

```sh
sudo rm -rf /opt/gitops-agent/app-configs
sudo systemctl restart gitops-agent
```

## To-Do

- Improve the feedback / monitoring branch to quickly highlight if everything is alright, or if there's any issue
- Add app-deletion functionality, e.g. if section is removed in configuration after it was initially added
- Keep commit history trimmed to a fixed period in the monitoring branch, to avoid bloat
- Push the status back only after all apps have been updated, rather than doing it for all individual apps
- Sample Gitlab/Github pipelines for validating updates to the config repo

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
