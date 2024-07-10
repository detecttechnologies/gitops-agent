import subprocess as sp
import toml
from pathlib import Path
from git import Repo, GitCommandError


def check_deployment_config(app_name, infra_name):
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
    curr_app_config["code_url"] = app_meta["code_url"]
    curr_app_config["code_commit_hash"] = app_meta["code_commit_hash"]
    curr_app_config["code_local_path"] = Path(app_meta["code_local_path"])
    curr_app_config["pre_updation_command"] = app_meta.get("pre_updation_command", None)
    curr_app_config["post_updation_command"] = app_meta.get("post_updation_command", None)

    if "config_src_path_rel_in_this_repo" in app_meta and "config_dst_path_abs" in app_meta:
        curr_app_config["config_dst_path_abs"] = Path(app_meta["config_dst_path_abs"])
        curr_app_config["config_src_path_abs"] = Path(
            f"/opt/gitops-agent/app-configs/{app_name}/",
            app_meta["config_src_path_rel_in_this_repo"],
        )
    else:
        curr_app_config["config_dst_path_abs"] = curr_app_config["config_src_path_abs"] = None

    return curr_app_config


def update_git_repo(
    app_name, git_url, git_branch, committer_name, local_path, checkout_hash=None, create_branch=False
):
    if git_url.endswith(f"@{git_branch}"):
        git_url = git_url[: -len(f"@{git_branch}")]

    print(f"Updating repository {app_name}...")
    if Path(local_path).exists():
        repo = Repo(local_path)
        claim_ownership(local_path)
        # Find if any partial rebase is in progress in dep_feedback repo, and abort it if so
        # Partial rebases can occur in case of force-quitting the process mid-execution in a previous run, or
        # e.g. there being a merge conflict when updating in a previous run
        if "rebas" in repo.git.status():  # Pick up both "rebase" and  "rebasing" in git status
            repo.git.rebase("--abort")
    else:
        repo = Repo.clone_from(git_url, local_path)

    # Update the local with changes from remote
    repo.git.fetch("--all", "--prune")
    repo.git.reset("--hard", "HEAD")

    try:
        # Check if the branch exists, create an empty branch if not
        all_branches = repo.refs
        branch_present = [e for e in all_branches if git_branch in str(e)]

        if git_branch and create_branch and not branch_present:
            repo.git.checkout("--orphan", git_branch)
            files = repo.git.ls_files()
            if files:
                repo.git.rm("-rf", ".")
                # Create an empty commit
                repo.git.config("user.name", committer_name)
                repo.git.config("user.email", "<>")
                repo.git.commit("--allow-empty", "-m", "Initial commit")
        else:
            if git_branch and not checkout_hash:
                checkout_hash = f"origin/{git_branch}"
            repo.git.reset("--hard", checkout_hash)
            repo.git.checkout(checkout_hash.replace("origin/", ""))  # To avoid detaching HEAD from branch
        update_status = True
    except GitCommandError as err:
        print(f"Error occurred while updating repository {app_name}: {err}")
        update_status = False

    git_status, latest_commit = check_git_status(local_path)
    return update_status, git_status, latest_commit


def check_git_status(local_path):
    repo = Repo(local_path)
    git_status = repo.git.status()
    latest_commit = repo.git.log("-1", "--pretty=format:'%h - %s (%an, %ad)'")
    return git_status, latest_commit


def claim_ownership(dir_path):
    if Path(dir_path).exists():
        curr_user = sp.run(["whoami"], capture_output=True, text=True).stdout.strip()
        curr_owner = Path(dir_path).owner()
        if curr_owner != curr_user:
            print(f"Directory {dir_path} exists under {curr_owner}, claiming ownership of it to be under {curr_user}")
            # Get the current user's username, and then claim ownership recursively of the repo to avoid dubious ownership
            sp.run(["chown", "-R", curr_user, dir_path], check=True)
