import hashlib
import os
import subprocess as sp
import toml
from pathlib import Path
from git import Repo, GitCommandError

# Root under which all per-app config checkouts live. Resolved from the
# GITOPS_AGENT_HOME env var so tests can point it at a tmp dir; defaults to the
# production location so on-prod behavior is unchanged.
APP_CONFIGS_DIR = Path(os.environ.get("GITOPS_AGENT_HOME", "/opt/gitops-agent")) / "app-configs"


# The single-file keys from the old schema. They are no longer supported: if either is present
# in an app's section, the agent refuses to run and asks the user to migrate to ``config_files``.
LEGACY_CONFIG_KEYS = ("config_src_path_rel_in_this_repo", "config_dst_path_abs")


def resolve_config_file_pairs(app_meta, repo_root):
    """Normalize an app's config-file definitions into a list of resolved src/dst path pairs.

    Pure path-resolution helper (no I/O). The only supported schema is an array of inline tables
    under ``config_files``, each with ``src`` (relative to the deployment-config repo root) and
    ``dst`` (absolute path).

    The old single-file keys ``config_src_path_rel_in_this_repo`` / ``config_dst_path_abs`` are no
    longer supported. If EITHER is present a ValueError is raised naming the offending key(s) and
    telling the user to migrate to ``config_files`` -- the agent fails loud rather than silently
    ignoring them. If ``config_files`` is absent (and no legacy keys are present), an empty list is
    returned (no config files to copy).

    Args:
        app_meta (dict): The app's section parsed from infra_meta.toml.
        repo_root (Path): The shared deployment-config checkout directory for this (url, branch)
            ({GITOPS_AGENT_HOME}/app-configs/<slug>@<branch>-<hash>/, by default under
            /opt/gitops-agent/app-configs/), against which relative ``src`` paths are resolved
            (hence the leading infra-name segment in ``src`` examples).

    Returns:
        list[dict]: Each dict has ``src_abs`` (Path) and ``dst_abs`` (Path).

    Raises:
        ValueError: If any of the removed legacy single-file keys are present.
    """
    offending = [key for key in LEGACY_CONFIG_KEYS if key in app_meta]
    if offending:
        raise ValueError(
            "The legacy config keys "
            + ", ".join(offending)
            + " are no longer supported. Migrate them to the `config_files` array, e.g. "
            + 'config_files = [{ src = "infra_name/config.toml", dst = "/abs/path/config.toml" }]'
        )

    repo_root = Path(repo_root)
    pairs = []
    for entry in app_meta.get("config_files", []):
        pairs.append(
            {
                "src_abs": Path(repo_root, entry["src"]),
                "dst_abs": Path(entry["dst"]),
            }
        )

    return pairs


def normalize_url(git_url):
    """Return a canonical form of a git url for equality/identity comparisons.

    Strips a trailing ``.git``, a trailing slash, and surrounding whitespace so that
    equivalent urls compare equal. For ``scheme://`` urls it also drops any embedded
    credentials (``user:token@host`` -> ``host``), since the same repo is sometimes
    cloned with and without a token. Does NOT attempt to reconcile ssh scp-form with
    https form -- that could make two genuinely distinct urls compare equal, which would
    be unsafe for the deletion guard. Does NOT strip any ``@branch`` suffix -- callers
    that may pass one (see parse_config) must strip it first.
    """
    url = git_url.strip()
    # Drop credentials embedded in scheme urls (https://user:token@host/... -> https://host/...).
    # Only touch the authority that follows "scheme://" so scp-style "git@host:path" is untouched.
    if "://" in url:
        scheme, _, rest = url.partition("://")
        if "@" in rest:
            rest = rest.split("@", 1)[1]
        url = f"{scheme}://{rest}"
    if url.endswith("/"):
        url = url[:-1]
    if url.endswith(".git"):
        url = url[: -len(".git")]
    return url


def url_hash(git_url, length=8):
    """Return a short stable hex digest of the normalized url.

    Used to disambiguate on-disk clone paths for two distinct repos that happen to
    share a basename (e.g. OrgA/deploy vs OrgB/deploy) so they never collapse onto
    the same directory.
    """
    digest = hashlib.sha256(normalize_url(git_url).encode("utf-8")).hexdigest()
    return digest[:length]


def repo_slug(git_url):
    """Return the repo basename without the trailing .git for a git url.

    Works for ssh (git@host:Org/Sub/repo.git), https (https://host/Org/repo.git),
    nested namespaces, and urls with or without a trailing .git. Any trailing
    @branch suffix should be stripped by the caller (see parse_config) before
    calling this.

    Examples:
        git@gitlab.com:Org/Sub/tricon-2025-12.git -> "tricon-2025-12"
        https://github.com/username/repo1_config.git -> "repo1_config"
    """
    url = git_url.strip()
    if url.endswith("/"):
        url = url[:-1]
    if url.endswith(".git"):
        url = url[: -len(".git")]
    # The repo name is whatever follows the last path or scp-style separator
    for sep in ("/", ":"):
        if sep in url:
            url = url.rsplit(sep, 1)[1]
    return url


def check_deployment_config(dep_cfg_local_path, app_name, infra_name):
    infra_meta_file = Path(f"{dep_cfg_local_path}/{infra_name}/infra_meta.toml")
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

    # Relative ``src`` paths are resolved against the shared deployment-config clone for this
    # (url, branch), i.e. dep_cfg_local_path -- not a per-app dir, since the dedup change clones
    # each deploy-config repo once and shares it across all apps that reference it.
    curr_app_config["config_file_pairs"] = resolve_config_file_pairs(app_meta, Path(dep_cfg_local_path))

    return curr_app_config


def update_git_repo(
    app_name, git_url, git_branch, committer_name, local_path, checkout_hash=None, create_branch=False
):
    if git_url.endswith(f"@{git_branch}"):
        git_url = git_url[: -len(f"@{git_branch}")]

    print(f"Updating repository {app_name}...")
    if Path(local_path).exists():
        # Guard against a slug/path collision: an existing clone at this path MUST be the same
        # repo we are about to fetch/reset-hard. Otherwise we would silently operate on (and
        # deploy from) the wrong repo's contents. Error loudly instead.
        if not is_repo_with_origin(local_path, git_url):
            raise RuntimeError(
                f"Refusing to update {app_name}: existing clone at {local_path} has an origin that does "
                f"not match the expected url {git_url!r}. This indicates a path collision between two "
                f"distinct repos. Remove or relocate the stale clone and retry."
            )
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


def is_repo_with_origin(local_path, expected_url):
    """Return True only if local_path is a git repo whose origin remote matches expected_url.

    Used as an origin-guard inside update_git_repo so it never fetches/resets an existing clone
    whose origin doesn't match the expected deploy-config URL (a path collision between two
    distinct repos). The comparison ignores a trailing .git and any @branch suffix so equivalent
    urls still match.
    """

    try:
        repo = Repo(local_path)
        origins = [r for r in repo.remotes if r.name == "origin"]
        if not origins:
            return False
        actual_urls = list(origins[0].urls)
    except Exception as err:  # not a git repo, or bare/corrupt -- treat as non-matching
        print(f"Could not inspect {local_path} as a git repo: {err}")
        return False
    return any(normalize_url(u) == normalize_url(expected_url) for u in actual_urls)


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
