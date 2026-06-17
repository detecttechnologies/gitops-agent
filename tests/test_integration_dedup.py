"""End-to-end integration tests for the dedup deployment-config-clone refactor.

These tests exercise the *whole* reconcile pass (GitOpsAgent.run_once) against REAL local git
repos created under tmp_path -- no network, no glab, no /opt, no root. The deployment-config repo
and every app-code repo are local bare repos addressed via file:// urls; the origin-match guard
that update_git_repo uses to refuse fetching/resetting a mismatched existing clone compares those
local paths, which is exactly how it behaves in production with real urls.
"""

import os
import subprocess as sp
from pathlib import Path

import pytest
import toml
from git import Repo

import gitops_agent.git_operations as gops
from gitops_agent.agent import GitOpsAgent, shared_clone_path


# --------------------------------------------------------------------------------------
# Local-git helpers
# --------------------------------------------------------------------------------------

def _git_env():
    env = dict(os.environ)
    env.setdefault("GIT_AUTHOR_NAME", "test")
    env.setdefault("GIT_AUTHOR_EMAIL", "test@example.com")
    env.setdefault("GIT_COMMITTER_NAME", "test")
    env.setdefault("GIT_COMMITTER_EMAIL", "test@example.com")
    return env


def _run(args, cwd):
    return sp.run(args, cwd=str(cwd), env=_git_env(), check=True, capture_output=True, text=True)


def make_bare_repo(path):
    """Create a bare repo at path and return its file:// url."""
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    _run(["git", "init", "--bare", "-b", "main", str(path)], cwd=path.parent)
    return f"file://{path}"


def working_clone(bare_url, dest):
    dest = Path(dest)
    dest.parent.mkdir(parents=True, exist_ok=True)
    _run(["git", "clone", bare_url, str(dest)], cwd=dest.parent)
    repo = Repo(str(dest))
    repo.git.config("user.name", "test")
    repo.git.config("user.email", "test@example.com")
    return repo


def commit_all(repo, message="commit"):
    repo.git.add(all=True)
    repo.git.commit("-m", message)
    return str(repo.head.commit)


def push(repo, branch="main"):
    repo.git.push("origin", f"HEAD:{branch}")


def make_app_code_repo(tmp_path, name):
    """Create a bare app-code repo with one commit; return (file_url, commit_hash)."""
    bare = tmp_path / "remotes" / f"{name}.git"
    url = make_bare_repo(bare)
    wc = working_clone(url, tmp_path / "work" / name)
    (Path(wc.working_tree_dir) / "README.md").write_text(f"# {name}\n")
    commit = commit_all(wc, f"init {name}")
    push(wc, "main")
    return url, commit


def make_deploy_repo(tmp_path, slug, apps, infra_name="testsite", branch="main", config_files=None):
    """Create a deployment-config bare repo containing <infra>/infra_meta.toml.

    apps: dict app_name -> {"code_url", "code_commit_hash", "code_local_path", ...extra meta...}
    config_files: optional dict of relative_path -> content written into the repo.
    Returns the file:// url of the bare deploy repo.
    """
    bare = tmp_path / "remotes" / f"{slug}.git"
    url = make_bare_repo(bare)
    wc = working_clone(url, tmp_path / "work" / slug)
    wtd = Path(wc.working_tree_dir)

    infra_dir = wtd / infra_name
    infra_dir.mkdir(parents=True, exist_ok=True)
    (infra_dir / "infra_meta.toml").write_text(toml.dumps(apps))

    for rel, content in (config_files or {}).items():
        f = wtd / rel
        f.parent.mkdir(parents=True, exist_ok=True)
        f.write_text(content)

    commit_all(wc, f"init deploy {slug}")
    push(wc, "main")  # always seed a real default branch
    if branch != "main":
        wc.git.checkout("-b", branch)
        push(wc, branch)
    return url


def write_agent_config(tmp_path, applications, infra_name="testsite", interval=300):
    cfg = {"applications": applications, "infra_name": infra_name, "interval": interval}
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(toml.dumps(cfg))
    return cfg_path


@pytest.fixture
def env(tmp_path, monkeypatch):
    """Point the agent's home + config at tmp, and the app-configs dir likewise."""
    home = tmp_path / "gitops-home"
    app_configs = home / "app-configs"
    app_configs.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("GITOPS_AGENT_HOME", str(home))
    # The module-level APP_CONFIGS_DIR was resolved at import; repoint it for the test.
    monkeypatch.setattr(gops, "APP_CONFIGS_DIR", app_configs)
    return {"home": home, "app_configs": app_configs, "tmp": tmp_path}


def build_agent(tmp_path, applications, infra_name="testsite"):
    cfg_path = write_agent_config(tmp_path, applications, infra_name=infra_name)
    os.environ["GITOPS_AGENT_CONFIG"] = str(cfg_path)
    try:
        agent = GitOpsAgent(config_mode=False)
    finally:
        os.environ.pop("GITOPS_AGENT_CONFIG", None)
    return agent


def app_meta_entry(code_url, code_commit, code_local_path):
    return {
        "code_url": code_url,
        "code_commit_hash": code_commit,
        "code_local_path": str(code_local_path),
    }


def shared_dirs(app_configs):
    """Return sorted names of non-monitoring shared clone dirs."""
    return sorted(p.name for p in app_configs.iterdir() if p.is_dir() and "-monitoring" not in p.name)


def monitoring_dirs(app_configs):
    return sorted(p.name for p in app_configs.iterdir() if p.is_dir() and p.name.endswith("-monitoring"))


# --------------------------------------------------------------------------------------
# Scenario 1: dedup happy path -- 3 apps share one (repo, branch)
# --------------------------------------------------------------------------------------

def test_dedup_happy_path_single_shared_clone(env, tmp_path):
    ac = env["app_configs"]
    # 3 app-code repos
    apps_meta = {}
    code_paths = {}
    for name in ("app1", "app2", "app3"):
        url, commit = make_app_code_repo(tmp_path, name)
        cp = tmp_path / "deployed" / name
        code_paths[name] = cp
        apps_meta[name] = app_meta_entry(url, commit, cp)

    deploy_url = make_deploy_repo(tmp_path, "deploy", apps_meta)
    applications = {name: f"{deploy_url}@main" for name in apps_meta}
    agent = build_agent(tmp_path, applications)

    agent.run_once()

    # Exactly ONE shared config clone and ONE shared monitoring clone
    assert len(shared_dirs(ac)) == 1, shared_dirs(ac)
    assert len(monitoring_dirs(ac)) == 1, monitoring_dirs(ac)

    expected = Path(shared_clone_path(deploy_url, "main"))
    assert expected.exists()
    assert (expected / "testsite" / "infra_meta.toml").exists()

    # All 3 apps reconciled: code checked out at the right commit
    for name, cp in code_paths.items():
        assert cp.exists(), f"{name} code not cloned"
        assert str(Repo(str(cp)).head.commit) == apps_meta[name]["code_commit_hash"]


# --------------------------------------------------------------------------------------
# Scenario 2: same repo, different branches -> separate shared clones
# --------------------------------------------------------------------------------------

def test_same_repo_different_branches_separate_clones(env, tmp_path):
    ac = env["app_configs"]
    urlA, cA = make_app_code_repo(tmp_path, "appA")
    urlB, cB = make_app_code_repo(tmp_path, "appB")
    cpA = tmp_path / "deployed" / "appA"
    cpB = tmp_path / "deployed" / "appB"

    # One deploy repo, two branches each carrying its own app entry
    bare = tmp_path / "remotes" / "deploy.git"
    deploy_url = make_bare_repo(bare)
    wc = working_clone(deploy_url, tmp_path / "work" / "deploy")
    wtd = Path(wc.working_tree_dir)
    infra = wtd / "testsite"
    infra.mkdir(parents=True)
    # Seed a real main first (a real deploy repo always has its default branch), then branch off
    (infra / "infra_meta.toml").write_text(toml.dumps({}))
    commit_all(wc, "main")
    push(wc, "main")
    wc.git.checkout("-b", "branchA")
    (infra / "infra_meta.toml").write_text(toml.dumps({"appA": app_meta_entry(urlA, cA, cpA)}))
    commit_all(wc, "branchA")
    push(wc, "branchA")
    wc.git.checkout("main")
    wc.git.checkout("-b", "branchB")
    (infra / "infra_meta.toml").write_text(toml.dumps({"appB": app_meta_entry(urlB, cB, cpB)}))
    commit_all(wc, "branchB")
    push(wc, "branchB")

    applications = {"appA": f"{deploy_url}@branchA", "appB": f"{deploy_url}@branchB"}
    agent = build_agent(tmp_path, applications)
    agent.run_once()

    assert len(shared_dirs(ac)) == 2, shared_dirs(ac)
    assert Path(shared_clone_path(deploy_url, "branchA")).exists()
    assert Path(shared_clone_path(deploy_url, "branchB")).exists()


# --------------------------------------------------------------------------------------
# Scenario 3: same basename, different namespace -> url-hash disambiguation
# --------------------------------------------------------------------------------------

def test_same_basename_distinct_repos_no_clobber(env, tmp_path):
    ac = env["app_configs"]
    urlX, cX = make_app_code_repo(tmp_path, "appX")
    urlY, cY = make_app_code_repo(tmp_path, "appY")
    cpX = tmp_path / "deployed" / "appX"
    cpY = tmp_path / "deployed" / "appY"

    # Two distinct deploy repos that share the basename "deploy"
    deployA = make_deploy_repo(
        tmp_path, "orgA/deploy", {"appX": app_meta_entry(urlX, cX, cpX)}
    )
    deployB = make_deploy_repo(
        tmp_path, "orgB/deploy", {"appY": app_meta_entry(urlY, cY, cpY)}
    )
    assert gops.repo_slug(deployA) == gops.repo_slug(deployB) == "deploy"

    applications = {"appX": f"{deployA}@main", "appY": f"{deployB}@main"}
    agent = build_agent(tmp_path, applications)
    agent.run_once()

    pA = Path(shared_clone_path(deployA, "main"))
    pB = Path(shared_clone_path(deployB, "main"))
    assert pA != pB
    assert pA.exists() and pB.exists()
    assert len(shared_dirs(ac)) == 2, shared_dirs(ac)
    # Each shared clone resolves to its own deploy repo (no clobber)
    assert cpX.exists() and str(Repo(str(cpX)).head.commit) == cX
    assert cpY.exists() and str(Repo(str(cpY)).head.commit) == cY


# --------------------------------------------------------------------------------------
# Scenario 4: monitoring -- one shared monitoring clone, feedback merges all apps, pushed
# --------------------------------------------------------------------------------------

def test_monitoring_feedback_merges_all_apps_and_pushed(env, tmp_path):
    ac = env["app_configs"]
    apps_meta = {}
    for name in ("app1", "app2", "app3"):
        url, commit = make_app_code_repo(tmp_path, name)
        apps_meta[name] = app_meta_entry(url, commit, tmp_path / "deployed" / name)
    deploy_url = make_deploy_repo(tmp_path, "deploy", apps_meta)
    applications = {name: f"{deploy_url}@main" for name in apps_meta}
    agent = build_agent(tmp_path, applications)
    agent.run_once()

    # Exactly one shared monitoring clone
    assert len(monitoring_dirs(ac)) == 1, monitoring_dirs(ac)
    mon = Path(shared_clone_path(deploy_url, "main") + "-monitoring")
    feedback_file = mon / "testsite.toml"
    assert feedback_file.exists()
    feedback = toml.load(feedback_file)
    for name in apps_meta:
        assert name in feedback, f"{name} missing from merged feedback"

    # The feedback was pushed to the main-monitoring branch on the bare remote
    bare = tmp_path / "remotes" / "deploy.git"
    branches = sp.run(
        ["git", "branch", "--list", "main-monitoring"], cwd=str(bare),
        capture_output=True, text=True,
    ).stdout
    assert "main-monitoring" in branches


# --------------------------------------------------------------------------------------
# Scenario 5: origin-guard on update -> refuses to operate on mismatched existing clone
# --------------------------------------------------------------------------------------

def test_origin_guard_refuses_mismatched_existing_clone(env, tmp_path):
    ac = env["app_configs"]
    url1, c1 = make_app_code_repo(tmp_path, "app1")
    apps_meta = {"app1": app_meta_entry(url1, c1, tmp_path / "deployed" / "app1")}
    deploy_url = make_deploy_repo(tmp_path, "deploy", apps_meta)

    # Place an UNRELATED git repo exactly at the shared clone path the agent will want to use
    unrelated_url, _ = make_app_code_repo(tmp_path, "unrelated")
    shared = Path(shared_clone_path(deploy_url, "main"))
    working_clone(unrelated_url, shared)

    applications = {"app1": f"{deploy_url}@main"}
    agent = build_agent(tmp_path, applications)

    # The agent must REFUSE to operate on the wrong repo rather than fetch/reset-hard it.
    # run_once raises (either the origin-guard RuntimeError, or a FileNotFoundError from
    # check_deployment_config noticing the wrong repo lacks the expected infra_meta.toml),
    # and critically the unrelated clone is left at its original commit -- never reset to ours.
    before = str(Repo(str(shared)).head.commit)
    with pytest.raises(Exception):  # noqa: B017 -- any refusal is acceptable; corruption is not
        agent.run_once()
    after = str(Repo(str(shared)).head.commit)
    assert before == after, "unrelated clone must not be touched by the agent"
    # The unrelated repo's origin is still its own -- we never repointed/fetched ours into it
    assert gops.is_repo_with_origin(shared, unrelated_url) is True
    assert gops.is_repo_with_origin(shared, deploy_url) is False


def test_update_git_repo_guard_direct(env, tmp_path):
    url1, _ = make_app_code_repo(tmp_path, "repo1")
    url2, _ = make_app_code_repo(tmp_path, "repo2")
    local = tmp_path / "clone"
    working_clone(url1, local)
    # Existing clone is repo1; ask update_git_repo to update it as if it were repo2
    with pytest.raises(RuntimeError, match="origin"):
        gops.update_git_repo("x", url2, "main", "infra", str(local))


# --------------------------------------------------------------------------------------
# Scenario 6: idempotency -- run_once twice is stable
# --------------------------------------------------------------------------------------

def test_idempotent_second_run_stable(env, tmp_path):
    ac = env["app_configs"]
    apps_meta = {}
    for name in ("app1", "app2"):
        url, commit = make_app_code_repo(tmp_path, name)
        apps_meta[name] = app_meta_entry(url, commit, tmp_path / "deployed" / name)
    deploy_url = make_deploy_repo(tmp_path, "deploy", apps_meta)
    applications = {name: f"{deploy_url}@main" for name in apps_meta}
    agent = build_agent(tmp_path, applications)

    agent.run_once()
    assert len(shared_dirs(ac)) == 1
    snapshot = {p.name: str(Repo(str(env["app_configs"] / p.name)).head.commit) for p in
                env["app_configs"].iterdir() if (env["app_configs"] / p.name / ".git").exists()}

    agent.run_once()
    assert len(shared_dirs(ac)) == 1
    after = {p.name: str(Repo(str(env["app_configs"] / p.name)).head.commit) for p in
             env["app_configs"].iterdir() if (env["app_configs"] / p.name / ".git").exists()}
    # Config clone commit unchanged across runs
    cfg_name = Path(shared_clone_path(deploy_url, "main")).name
    assert snapshot[cfg_name] == after[cfg_name]
