# GitOps Agent

> Slim, server-side GitOps for non-Kubernetes infrastructure — manage applications and their configs on on-prem servers, VMs and edge devices with nothing but Git.

[![License: MIT](https://img.shields.io/badge/License-MIT-yellow.svg)](LICENSE)
[![Python: 3.6+](https://img.shields.io/badge/Python-3.6%2B-blue.svg)](pyproject.toml)
[![PRs Welcome](https://img.shields.io/badge/PRs-welcome-brightgreen.svg)](#contributing)

GitOps Agent is a Python tool that continuously monitors remote Git repositories and reconciles your production servers to match them. It started as a simple git-repo watcher in [CVG, CFI, IITM](https://github.com/iitmcvg), and has since evolved into a GitOps-enablement tool for managing applications and their configurations on on-premise servers, classical/conventional infrastructure, VMs and edge devices — all without the need for Kubernetes.

## Features

- **GitOps for non-k8s environments** — manage multiple applications simultaneously on classical infrastructure.
- **Continuous Delivery** (not Continuous Deployment) of software applications and their configurations to production servers.
- **Drift detection** — detects drift in configuration or in the application's git repo on the production server and reconciles it.
- **Pre/post hooks** — run custom commands before and after drift is corrected (e.g. stash changes, restart a container).
- **Git-only observability** — status of each reconciliation is pushed back into the same deployment-management git repo, with no extra dependencies.
- **Accountability & traceability** — every change made on production is accounted for, transparent and auditable.
- **Approval workflows** — implemented purely via branch-protection rules on GitHub/GitLab.

## Table of Contents

- [Why](#why)
- [How It Works](#how-it-works)
- [Is This For Me?](#is-this-for-me)
- [Differences From Other GitOps Tools](#differences-from-other-gitops-tools)
- [Quick Start](#quick-start)
- [Configuration](#configuration)
  - [On-disk layout](#on-disk-layout)
  - [Monitoring feedback & health](#monitoring-feedback--health)
- [Troubleshooting](#troubleshooting)
- [Roadmap](#roadmap)
- [Contributing](#contributing)
- [References](#references)
- [Alternatives](#alternatives)
- [License](#license)

## Why

You might ask yourself why such a solution would be needed.

![Gitops-agent-1](https://github.com/detecttechnologies/gitops-agent/assets/10851575/7307e5ff-4c6c-4de8-b057-b543c6f11f72)

If you find yourself manually deploying many software applications onto multiple servers, there are common issues you frequently encounter:

1. It takes a lot of time to keep ssh-ing into deployment devices (production nodes) and manually configuring them. This can be a serious concern if your production nodes are edge devices with very sparse internet.
2. It's difficult to keep track of the version of applications and configuration present across all production nodes.
3. All the issues mentioned in the [Why GitOps](https://www.gitops.tech/#why-should-i-use-gitops) section of the Weaveworks blog.

This is exactly the type of situation that GitOps was designed to solve. However, most existing GitOps solutions like ArgoCD/FluxCD/Spinnaker require you to embrace an entire device & application management solution like Kubernetes. This project aims to enable a much simpler implementation of GitOps.

It lets you run a GitOps workflow on classical infrastructure like on-prem servers and edge devices, while consuming very little resources and having very few dependencies. It even supports observability/monitoring of the GitOps workflow with no dependency except Git.

## How It Works

![Gitops-agent-2](https://github.com/detecttechnologies/gitops-agent/assets/10851575/4c3c806d-2dcf-4040-bc17-1601802d6dc8)

The diagram above is a simplified explanation of how this program works. It introduces a `gitops-agent` that runs as a systemd service on your Ubuntu/Debian machine (in reality, any unix device with systemd + Python should be able to run this with a bit of tweaking). This frees Devs and DevOps folks to work without being constrained by prod-device availability, across a team.

Using simple branch-protection rules already available on GitHub/GitLab, you can implement a workflow with the necessary approvals as you desire, while ensuring a change-management system where all changes in production are accounted for.

Extending the above workflow to multiple applications and prod devices, it looks like this:

![Gitops-agent-3](https://github.com/detecttechnologies/gitops-agent/assets/10851575/57a19c80-a77e-4402-80a0-f68841db86f6)

### Is This For Me?

Before you decide to use this as your GitOps solution, ask yourself why you'd prefer this over any other GitOps offering. If you want a slim (minimal-dependency, low-runtime-overhead) solution with the [features listed above](#features) — and not any of the other features that fuller GitOps solutions offer (covered below) — then this approach could be for you.

### Differences From Other GitOps Tools

- This tool focuses largely on **application and configuration management**, not on infrastructure setup as is generally the case with other GitOps tools.
- It isn't necessary to use Docker containers with this tool, unlike most other GitOps tools — a lot of lower-level applications may not run on Docker. If you do want to run something in a Docker container, you can always use the `pre_updation_command` and `post_updation_command` hooks to run any custom commands, including pulling/building new Docker images, etc.
- This tool is called an "agent", not an "operator" (traditional k8s speak), because in conventional infrastructure like physical servers, any client in a client-server architecture is generally called an "agent".

## Quick Start

### Prerequisites

- Ensure that your prod-device's root user has a passwordless ssh-key.
  - You can check this by looking for files in `/root/.ssh`.
  - If none are present, create one with `sudo ssh-keygen`, pressing Enter at all the prompts.
- Ensure that each application you want to maintain through this agent has a separate git repository.
- Ensure that you have a separate git repository for deployment management.
- If applications already exist on the prod-device, bring the locally cloned app-repos to a state where their `git status` is as clean as possible.

### Git Remote Repository Setup

Configure your online git repos:

- **Application git repos**
  - Add your Debian root user's ssh public key as a `Deploy Key`.
- **Deployment-configuration git repo**
  - Ensure there is a folder with the exact name you nicknamed for your prod-device, containing an `infra_meta.toml` file describing the applications you want this agent to monitor. You can also host any configuration files here.
  - Add your Debian root user's ssh public key as a `Deploy Key` **with write access** (the agent needs to write back the feedback/monitoring branch).
  - Ensure the repo allows pushes from unverified users. On GitLab this may need to be disabled manually: *Repository Settings → Repository → Push rules → disable `Reject unverified users`*.
  - Employ any approval workflows you like by restricting who can push/approve changes to the branch used for the GitOps workflow.

### Installation

Install GitOps Agent on the prod-device by running:

```bash
sudo apt-get update
sudo apt-get install -y curl
curl -sL https://bit.ly/gitops-agent-installer | sudo bash
```

If you use a git provider other than github.com/gitlab.com, register its ssh keys with `sudo ssh-keyscan <custom_git_host_endpoint>`.

After installation, the agent runs automatically as a systemd service. Configure the run settings with:

```sh
sudo gitops-agent --configure         ## <Make the changes you want in the editor>
sudo systemctl restart gitops-agent.service
```

Follow the agent's logs while it runs:

```sh
sudo journalctl -n 100 -fu gitops-agent  # Keeps following the gitops-agent logs
```

## Configuration

There are two configuration files: the agent's own config on the prod-device, and the per-application schema that lives in your deployment-configuration git repo.

### Agent config — `/etc/gitops-agent/config.toml`

Edited via `sudo gitops-agent --configure`. It names this prod-device, sets the polling interval (seconds), and lists the applications to manage. Each entry maps an app name to the SSH URL of its **deployment-config repo**, suffixed with `@branch`:

```toml
infra_name = "xyz"
interval = 300

[applications]
    APP1_NAME_HERE = "git@github.com:username/repo1_config.git@branch_name"

    ## REFERENCE STRUCTURE:
    # APP_NAME_THAT_MATCHES_NAME_IN_CONFIG_REPO_HERE = "git@github.com:username/config_repo.git@branch_name"
```

The agent uses the configured `branch` for reconciliation and writes feedback to a corresponding `{branch}-monitoring` branch.

### Per-app schema — `<infra_name>/infra_meta.toml`

Inside the deployment-config repo, create a folder named exactly like your `infra_name` and add an `infra_meta.toml`. Each app gets a section keyed by the same app name used in the agent config:

```toml
[app_name_here]
    code_url = "INSERT_GIT_REPO_SSH_URL_HERE"
    code_commit_hash = "INSERT_COMMIT_HASH_HERE"
    code_local_path = "Ex: /mnt/abc/def, should be absolute"
    # Optional: copy one or more config files from this repo into place after the code is updated.
    # `src` is relative to this repo's root; `dst` is an absolute path on the prod-device.
    config_files = [
        { src = "infra_name/config.toml", dst = "/mnt/abc/def/config.toml" },
        { src = "infra_name/secrets.env", dst = "/mnt/abc/def/.env" },
    ]
    # Optional: commands run around reconciliation
    pre_updation_command = "OPTIONAL, Ex: git stash"
    post_updation_command = "OPTIONAL, Ex: docker restart xyz; git stash pop"
```

| Field | Required | Description |
|-------|----------|-------------|
| `code_url` | yes | SSH URL of the application's code repo. |
| `code_commit_hash` | yes | The commit the prod-device should be reconciled to. |
| `code_local_path` | yes | Absolute path where the code repo is cloned on the prod-device. |
| `config_files` | no | Array of `{ src, dst }` inline tables — config files to copy into place. `src` is relative to the config repo root; `dst` is an absolute path. Destination parent directories are created automatically, and any entry whose `src` is missing is skipped (with a log) rather than aborting the run. |
| `pre_updation_command` | no | Command run before reconciliation. |
| `post_updation_command` | no | Command run after reconciliation. |

> **Removed legacy keys:** the older single-file keys `config_src_path_rel_in_this_repo` and
> `config_dst_path_abs` are **no longer supported**. If either is present in an app's section, the
> agent raises an error naming the offending key(s) and asking you to migrate to the `config_files`
> array — it does not silently ignore them. Express even a single file as a one-element
> `config_files` list.

### On-disk layout

The agent keeps its working clones under `/opt/gitops-agent/app-configs/`. Multiple applications usually share a single deployment-config repository (one `infra_meta.toml` describes them all), so the agent clones each **unique** `(deploy-config-repo, branch)` exactly once into a shared directory named `<repo-slug>@<branch>-<url-hash>`:

```
/opt/gitops-agent/app-configs/
├── <repo-slug>@<branch>-<url-hash>/             # one shared clone of the deployment-config repo
│   └── <infra_name>/infra_meta.toml             # describes all apps for this infra
└── <repo-slug>@<branch>-<url-hash>-monitoring/  # one shared clone on the <branch>-monitoring branch
    └── <infra_name>.toml                        # merged feedback file, keyed by app_name
```

`<repo-slug>` is the repository basename without the trailing `.git` (e.g. `git@gitlab.com:Org/Sub/tricon-2025-12.git` → `tricon-2025-12`), and `<url-hash>` is a short hash of the full normalized repo URL. The hash disambiguates two distinct repos that share a basename but live under different namespaces, so they never collapse onto the same directory. Every app that references the same `(repo, branch)` reads its section from — and writes its feedback into — these shared clones, so four apps sharing one config repo result in a single clone (plus one monitoring clone) instead of eight.

### Monitoring feedback & health

After each reconcile pass the agent writes a single feedback file — `<infra_name>.toml` on the `<branch>-monitoring` branch — and commits/pushes it **once per deployment-config repo**, after all of that repo's apps have been processed (not once per app). The file is keyed by application name and carries a quick health summary so you can tell at a glance whether everything is alright:

- **`overall_status`** (top of the file) — `✅ all N apps healthy`, or `⚠️ M of N apps need attention: <names>` when one or more apps have an issue (`⚠️ no apps reported` if none).
- **Per-app `status`** — one of:
  - `✅ healthy`
  - `❌ app update failed` — the application's code repo could not be cloned/fetched/checked out to the desired commit.
  - `❌ config update failed` — the deployment-config repo could not be updated.
  - `❌ post-command exited non-zero` — a `pre_updation_command` / `post_updation_command` returned a non-zero exit code.
  - `❓ unknown status (malformed entry)` — the entry could not be interpreted (e.g. a hand-edited or legacy section).
- **Commit message** — the single monitoring commit reflects health too, e.g. `✅ Status: all 3 apps healthy` or `⚠️ Status: 1 of 3 issues (dt-iva-5)`, so the branch's commit list is scannable without opening the file.

An app is reported **healthy** only when *both* its config update and app update succeeded **and** every pre/post command exited `0`; otherwise it is flagged, with the label chosen in that order of precedence (app update → config update → commands). Health is derived from these reconcile outcomes — not from parsing the raw `git status` text — so an app that updated cleanly is `✅ healthy` even if its working tree later drifts without causing an update error.

## Troubleshooting

- This has only been tested on Ubuntu. It is known not to run properly on WSL due to an [issue](https://github.com/gitpython-developers/GitPython/issues/1902) with how GitPython handles WSL paths.

### Installing when apps were already deployed manually

When installing the agent mid-lifecycle for an existing project:

1. Ensure that `git status` is as clean as possible before installing.

### Forcing a push to the monitoring branch

During initial installation, if you want to force a push to the monitoring branch even when there's nothing to update, delete your local deployment-configs so it appears to be a fresh installation:

```sh
sudo rm -rf /opt/gitops-agent/app-configs
sudo systemctl restart gitops-agent
```

## Roadmap

- [x] ~~Improve the feedback/monitoring branch to quickly highlight whether everything is alright or if there's an issue.~~ — see [Monitoring feedback & health](#monitoring-feedback--health).
- [ ] Add app-deletion functionality (e.g. when a section is removed from configuration after being added).
- [x] ~~Keep the monitoring branch's commit history trimmed to a fixed period, to avoid bloat.~~ — trims to a 30-day window (configurable via `monitoring_history_retention_days`).
- [x] ~~Push status back only after all apps have been updated, rather than per individual app.~~
- [ ] Provide sample GitLab/GitHub pipelines for validating updates to the config repo.

## Contributing

This project is an initial PoC, and contributions are more than welcome! Feel free to open an issue or a pull request.

## References

Some related references:

1. <https://www.gitops.tech/>
2. <https://github.com/kitplummer/goa>
3. <https://github.com/kolbasa/git-repo-watcher>
4. <https://endjin.com/blog/2020/10/gitops-not-just-for-kubernetes>
5. <https://samiyaakhtar.medium.com/gitops-observability-visualizing-the-journey-of-a-container-5f6ef1f3c9d2>
6. <https://github.com/weaveworks/awesome-gitops>

## Alternatives

There are some near alternatives worth considering before using this. This solution is largely meant to work with independent, isolated systems without depending on any other system (like Mender/CFEngine) being installed.

1. FluxCD/ArgoCD (if you have a k8s cluster).
2. Integrating a tool like Mender and Ansible with your git hosting solution (GitHub/GitLab) — [blog by Siemens detailing the same](https://opensource.siemens.com/events/2023/slides/Matthias_Luescher_Automating_and_managing_an_IoT_Fleet_Using_Git.pdf).
3. A configuration-management tool like CFEngine.

## License

This project is licensed under the MIT License — see the [LICENSE](LICENSE) file for details.
