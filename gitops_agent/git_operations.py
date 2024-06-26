import toml
from pathlib import Path
from git import Repo, GitCommandError


def check_commit_of_this_infra(app_name, infra_name):
    infra_meta_file = Path(f"/opt/gitops-agent/app-configs/{app_name}/{infra_name}/infra_meta.toml")
    if not infra_meta_file.parent.parent.exists():
        print(infra_meta_file.parent.parent, " does not yet exist")
        return {}  # The config directory hasn't been cloned yet, so let the config be cloned
    elif not infra_meta_file.exists():
        raise FileNotFoundError(f"Infra meta file not found: {infra_meta_file}")

    with open(infra_meta_file) as f:
        infra_meta = toml.load(f)
        app_meta = infra_meta[app_name]

    curr_app_config = {}
    curr_app_config["config_file_path"] = Path(
        f"/opt/gitops-agent/app-configs/{app_name}/",
        app_meta["config_src_path_rel_in_this_repo"],
    )
    curr_app_config["code_url"] = app_meta["code_url"]
    curr_app_config["code_commit_hash"] = app_meta["code_commit_hash"]
    curr_app_config["code_local_path"] = Path(app_meta["code_local_path"])
    curr_app_config["config_dst_path_abs"] = Path(app_meta["config_dst_path_abs"])
    curr_app_config["post_updation_command"] = app_meta.get("post_updation_command", None)

    return curr_app_config


def update_git_repo(
    app_name, git_url, git_branch, committer_name, local_path, checkout_hash=None, create_branch=False
):
    if git_url.endswith(f"@{git_branch}"):
        git_url = git_url[: -len(f"@{git_branch}")]

    print(f"Updating repository {app_name}...")
    if not Path(local_path).exists():
        repo = Repo.clone_from(git_url, local_path)
    else:
        repo = Repo(local_path)

    # Update the local with changes from remote
    repo.git.fetch("-p")

    try:
        if checkout_hash:
            repo.git.checkout(checkout_hash)
        else:
            if create_branch:
                # Check if the branch exists
                all_branches = repo.refs
                branch_present = [e for e in all_branches if git_branch in str(e)]
                if branch_present:
                    repo.git.checkout(git_branch)
                    repo.git.pull(git_url, git_branch)
                else:
                    repo.git.checkout("--orphan", git_branch)
                    files = repo.git.ls_files()
                    if files:
                        repo.git.rm("-rf", ".")
                        # Create an empty commit
                        repo.git.config("user.name", committer_name)
                        repo.git.config("user.email", "<>")
                        repo.git.commit("--allow-empty", "-m", "Initial commit")
            else:
                repo.git.checkout(git_branch)
                repo.git.pull(git_url, git_branch)
        update_status = True
    except GitCommandError as err:
        print(f"Error occurred while updating repository {app_name}: {err}")
        update_status = False
    git_status = repo.git.status()
    # Get the hash and date of the latest commit
    latest_commit = repo.git.log("-1", "--pretty=format:'%h - %s (%an, %ad)'")
    return update_status, git_status, latest_commit
