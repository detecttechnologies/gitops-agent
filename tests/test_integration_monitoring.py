"""End-to-end integration tests for the "push monitoring status once per group" refactor.

These tests exercise the WHOLE reconcile pass (GitOpsAgent.run_once) against REAL local git repos
created under tmp_path -- no network, no glab, no /opt, no root. They assert the central guarantee of
this change: per (deploy-config url, branch) group, the merged monitoring feedback is committed +
pushed to the {branch}-monitoring branch on the bare remote EXACTLY ONCE per run_once pass, no matter
how many apps share that group (instead of once per app).

We reuse the local-git helpers and fixture style from tests/test_integration_dedup.py.

Run with:  python -m pytest tests/test_integration_monitoring.py -q
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
# Local-git helpers (mirrors tests/test_integration_dedup.py)
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


def make_app_code_repo_two_commits(tmp_path, name):
    """Create a bare app-code repo with TWO commits; return (file_url, first, second)."""
    bare = tmp_path / "remotes" / f"{name}.git"
    url = make_bare_repo(bare)
    wc = working_clone(url, tmp_path / "work" / name)
    wtd = Path(wc.working_tree_dir)
    (wtd / "app.txt").write_text("v1\n")
    first = commit_all(wc, f"first {name}")
    (wtd / "app.txt").write_text("v2\n")
    second = commit_all(wc, f"second {name}")
    push(wc, "main")
    return url, first, second


def status_commits(bare, branch="main-monitoring"):
    """Return count of 'Updated status' commits on the monitoring branch of the bare remote."""
    return sum(
        1 for h in remote_branch_commits(bare, branch)
        if sp.run(["git", "log", "-1", "--pretty=%s", h], cwd=str(bare),
                  capture_output=True, text=True).stdout.strip() == "Updated status"
    )


def make_deploy_repo(tmp_path, slug, apps, infra_name="testsite"):
    """Create a deployment-config bare repo containing <infra>/infra_meta.toml. Returns file:// url."""
    bare = tmp_path / "remotes" / f"{slug}.git"
    url = make_bare_repo(bare)
    wc = working_clone(url, tmp_path / "work" / slug)
    wtd = Path(wc.working_tree_dir)
    infra_dir = wtd / infra_name
    infra_dir.mkdir(parents=True, exist_ok=True)
    (infra_dir / "infra_meta.toml").write_text(toml.dumps(apps))
    commit_all(wc, f"init deploy {slug}")
    push(wc, "main")
    return url


def rewrite_deploy_meta(tmp_path, slug, apps, infra_name="testsite"):
    """Re-push <infra>/infra_meta.toml on the existing deploy repo's main branch with new contents."""
    bare = tmp_path / "remotes" / f"{slug}.git"
    url = f"file://{bare}"
    wcdir = tmp_path / "rewrite" / slug
    wc = working_clone(url, wcdir)
    infra_dir = Path(wc.working_tree_dir) / infra_name
    infra_dir.mkdir(parents=True, exist_ok=True)
    (infra_dir / "infra_meta.toml").write_text(toml.dumps(apps))
    commit_all(wc, "update deploy meta")
    push(wc, "main")


def write_agent_config(tmp_path, applications, infra_name="testsite", interval=300):
    cfg = {"applications": applications, "infra_name": infra_name, "interval": interval}
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(toml.dumps(cfg))
    return cfg_path


@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / "gitops-home"
    app_configs = home / "app-configs"
    app_configs.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("GITOPS_AGENT_HOME", str(home))
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


def remote_branch_commits(bare_path, branch):
    """Return list of commit hashes (newest first) on `branch` of the bare remote, or [] if absent."""
    res = sp.run(
        ["git", "log", "--pretty=format:%H", branch],
        cwd=str(bare_path), capture_output=True, text=True,
    )
    if res.returncode != 0:
        return []
    return [h for h in res.stdout.splitlines() if h]


def remote_branch_file(bare_path, branch, rel_path, tmp_path, name="checkout"):
    """Clone `branch` of the bare repo and return the parsed-toml contents of rel_path (or None)."""
    checkout = tmp_path / name
    if checkout.exists():
        import shutil
        shutil.rmtree(checkout)
    repo = Repo.clone_from(str(bare_path), checkout, branch=branch)
    f = Path(repo.working_tree_dir) / rel_path
    return toml.loads(f.read_text()) if f.exists() else None


# --------------------------------------------------------------------------------------
# Scenario 1 + 2: one commit per pass (not per app); a single push for the group
# --------------------------------------------------------------------------------------

def test_one_commit_per_pass_not_per_app(env, tmp_path):
    apps_meta = {}
    for name in ("app1", "app2", "app3"):
        url, commit = make_app_code_repo(tmp_path, name)
        apps_meta[name] = app_meta_entry(url, commit, tmp_path / "deployed" / name)
    deploy_url = make_deploy_repo(tmp_path, "deploy", apps_meta)
    applications = {name: f"{deploy_url}@main" for name in apps_meta}
    agent = build_agent(tmp_path, applications)

    bare = tmp_path / "remotes" / "deploy.git"
    assert remote_branch_commits(bare, "main-monitoring") == []  # branch doesn't exist yet

    agent.run_once()

    commits = remote_branch_commits(bare, "main-monitoring")
    # Orphan branch creation pushes an "Initial commit" + exactly ONE "Updated status" commit
    # (not one per app). So with 3 apps we still see a single status commit on top of init.
    assert len(commits) >= 1
    msgs = [
        sp.run(["git", "log", "-1", "--pretty=%s", h], cwd=str(bare),
               capture_output=True, text=True).stdout.strip()
        for h in commits
    ]
    assert msgs.count("Updated status") == 1, msgs

    # The merged feedback file holds ALL apps.
    feedback = remote_branch_file(bare, "main-monitoring", "testsite.toml", tmp_path, "mon1")
    for name in apps_meta:
        assert name in feedback, f"{name} missing from merged feedback"
    assert "last-updated" in feedback


def test_push_once_single_new_commit(env, tmp_path):
    apps_meta = {}
    for name in ("app1", "app2"):
        url, commit = make_app_code_repo(tmp_path, name)
        apps_meta[name] = app_meta_entry(url, commit, tmp_path / "deployed" / name)
    deploy_url = make_deploy_repo(tmp_path, "deploy", apps_meta)
    applications = {name: f"{deploy_url}@main" for name in apps_meta}
    agent = build_agent(tmp_path, applications)

    bare = tmp_path / "remotes" / "deploy.git"
    before = len(remote_branch_commits(bare, "main-monitoring"))  # 0
    agent.run_once()
    after = len(remote_branch_commits(bare, "main-monitoring"))
    # init (orphan) + 1 status = 2 total; the point is there is ONE status commit for 2 apps.
    msgs = [
        sp.run(["git", "log", "-1", "--pretty=%s", h], cwd=str(bare),
               capture_output=True, text=True).stdout.strip()
        for h in remote_branch_commits(bare, "main-monitoring")
    ]
    assert msgs.count("Updated status") == 1
    assert after > before


# --------------------------------------------------------------------------------------
# Scenario 3: idempotency / no-op -- second run adds NO new monitoring commit
# --------------------------------------------------------------------------------------

def test_idempotent_no_extra_commit(env, tmp_path):
    apps_meta = {}
    for name in ("app1", "app2"):
        url, commit = make_app_code_repo(tmp_path, name)
        apps_meta[name] = app_meta_entry(url, commit, tmp_path / "deployed" / name)
    deploy_url = make_deploy_repo(tmp_path, "deploy", apps_meta)
    applications = {name: f"{deploy_url}@main" for name in apps_meta}
    agent = build_agent(tmp_path, applications)

    bare = tmp_path / "remotes" / "deploy.git"
    agent.run_once()
    commits_after_first = remote_branch_commits(bare, "main-monitoring")

    # Second pass: nothing changed -> NO new commit, NO push.
    agent.run_once()
    commits_after_second = remote_branch_commits(bare, "main-monitoring")
    assert commits_after_second == commits_after_first, "second run must not add a monitoring commit"


# --------------------------------------------------------------------------------------
# Scenario 4: incremental -- changing one app's desired state -> exactly one new commit,
# merged file still has all apps, changed app updated.
# --------------------------------------------------------------------------------------

def test_incremental_one_new_commit_changed_app_updated(env, tmp_path):
    # app1 has two commits; deploy initially points it at the FIRST.
    url1, first1, second1 = make_app_code_repo_two_commits(tmp_path, "app1")
    url2, commit2 = make_app_code_repo(tmp_path, "app2")
    cp1 = tmp_path / "deployed" / "app1"
    cp2 = tmp_path / "deployed" / "app2"
    apps_meta = {
        "app1": app_meta_entry(url1, first1, cp1),
        "app2": app_meta_entry(url2, commit2, cp2),
    }
    deploy_url = make_deploy_repo(tmp_path, "deploy", apps_meta)
    applications = {"app1": f"{deploy_url}@main", "app2": f"{deploy_url}@main"}
    agent = build_agent(tmp_path, applications)

    bare = tmp_path / "remotes" / "deploy.git"
    agent.run_once()
    assert str(Repo(str(cp1)).head.commit) == first1
    commits_1 = remote_branch_commits(bare, "main-monitoring")
    status_count_1 = sum(
        1 for h in commits_1
        if sp.run(["git", "log", "-1", "--pretty=%s", h], cwd=str(bare),
                  capture_output=True, text=True).stdout.strip() == "Updated status"
    )
    assert status_count_1 == 1

    # Change app1's desired hash to the SECOND commit in the deploy repo.
    apps_meta["app1"]["code_commit_hash"] = second1
    rewrite_deploy_meta(tmp_path, "deploy", apps_meta)

    agent.run_once()
    # app1 now checked out at the new hash.
    assert str(Repo(str(cp1)).head.commit) == second1

    commits_2 = remote_branch_commits(bare, "main-monitoring")
    status_count_2 = sum(
        1 for h in commits_2
        if sp.run(["git", "log", "-1", "--pretty=%s", h], cwd=str(bare),
                  capture_output=True, text=True).stdout.strip() == "Updated status"
    )
    # Exactly ONE additional status commit from the second pass.
    assert status_count_2 == status_count_1 + 1, (status_count_1, status_count_2)

    # Merged file still has BOTH apps; app1's recorded commit reflects the new hash.
    feedback = remote_branch_file(bare, "main-monitoring", "testsite.toml", tmp_path, "mon4")
    assert "app1" in feedback and "app2" in feedback
    assert second1[:7] in feedback["app1"]["app-updation"]["git-repo-latest-commit"]


# --------------------------------------------------------------------------------------
# Scenario 5: multiple groups -- two deploy repos -> each monitoring repo gets exactly one commit
# --------------------------------------------------------------------------------------

def test_multiple_groups_independent_single_commit_each(env, tmp_path):
    urlA, cA = make_app_code_repo(tmp_path, "appA")
    urlB, cB = make_app_code_repo(tmp_path, "appB")
    cpA = tmp_path / "deployed" / "appA"
    cpB = tmp_path / "deployed" / "appB"

    deployA = make_deploy_repo(tmp_path, "orgA/deploy", {"appA": app_meta_entry(urlA, cA, cpA)})
    deployB = make_deploy_repo(tmp_path, "orgB/deploy", {"appB": app_meta_entry(urlB, cB, cpB)})

    applications = {"appA": f"{deployA}@main", "appB": f"{deployB}@main"}
    agent = build_agent(tmp_path, applications)
    agent.run_once()

    bareA = tmp_path / "remotes" / "orgA/deploy.git"
    bareB = tmp_path / "remotes" / "orgB/deploy.git"
    for bare, app in ((bareA, "appA"), (bareB, "appB")):
        assert status_commits(bare) == 1, (bare, status_commits(bare))
        feedback = remote_branch_file(bare, "main-monitoring", "testsite.toml", tmp_path, f"mon-{app}")
        assert app in feedback


# --------------------------------------------------------------------------------------
# Scenario 6: mixed group -- one app changes, the other doesn't -> ONE commit, both apps
# present, the unchanged app's recorded state is preserved.
# --------------------------------------------------------------------------------------

def test_mixed_group_one_changes_other_preserved(env, tmp_path):
    url1, first1, second1 = make_app_code_repo_two_commits(tmp_path, "app1")
    url2, commit2 = make_app_code_repo(tmp_path, "app2")
    cp1 = tmp_path / "deployed" / "app1"
    cp2 = tmp_path / "deployed" / "app2"
    apps_meta = {
        "app1": app_meta_entry(url1, first1, cp1),
        "app2": app_meta_entry(url2, commit2, cp2),
    }
    deploy_url = make_deploy_repo(tmp_path, "deploy", apps_meta)
    applications = {"app1": f"{deploy_url}@main", "app2": f"{deploy_url}@main"}
    agent = build_agent(tmp_path, applications)

    bare = tmp_path / "remotes" / "deploy.git"
    agent.run_once()
    assert status_commits(bare) == 1
    fb1 = remote_branch_file(bare, "main-monitoring", "testsite.toml", tmp_path, "mon6a")
    app2_app_updation_before = fb1["app2"]["app-updation"]

    # Change ONLY app1's desired hash.
    apps_meta["app1"]["code_commit_hash"] = second1
    rewrite_deploy_meta(tmp_path, "deploy", apps_meta)

    agent.run_once()
    # Exactly ONE additional status commit even though only one of two apps changed.
    assert status_commits(bare) == 2

    fb2 = remote_branch_file(bare, "main-monitoring", "testsite.toml", tmp_path, "mon6b")
    assert "app1" in fb2 and "app2" in fb2
    # app1 reflects the new hash; app2's OWN app-updation state is preserved (its code didn't move).
    # Note: config-updation legitimately advances for BOTH apps because they share the deploy-config
    # repo, which moved when we re-pushed infra_meta.toml -- that drift is accurate, not corruption.
    assert second1[:7] in fb2["app1"]["app-updation"]["git-repo-latest-commit"]
    assert fb2["app2"]["app-updation"] == app2_app_updation_before


# --------------------------------------------------------------------------------------
# Scenario 7: first_run handling. first_run bypasses the in-memory "nothing changed" skip so a
# FRESH agent re-evaluates and re-pushes the feedback (matching the original per-app push_status,
# which always re-wrote the top-level last-updated timestamp on first_run). The key guarantees that
# must hold regardless of timestamp-resolution timing: the fresh agent does NOT crash, every app is
# still present in the merged file, and -- once first_run is cleared -- a subsequent same-agent pass
# with nothing changed produces NO further commit (the no-op optimization still works).
# --------------------------------------------------------------------------------------

def test_first_run_then_idempotent(env, tmp_path):
    apps_meta = {}
    for name in ("app1", "app2"):
        url, commit = make_app_code_repo(tmp_path, name)
        apps_meta[name] = app_meta_entry(url, commit, tmp_path / "deployed" / name)
    deploy_url = make_deploy_repo(tmp_path, "deploy", apps_meta)
    applications = {name: f"{deploy_url}@main" for name in apps_meta}

    bare = tmp_path / "remotes" / "deploy.git"

    # First agent: produces the initial status commit, then first_run is cleared.
    agent1 = build_agent(tmp_path, applications)
    agent1.run_once()
    assert status_commits(bare) == 1
    assert agent1.first_run is False, "first_run must be cleared after the first successful push"

    # A brand-new agent (first_run=True again) over the SAME, already-current monitoring clone:
    # must not crash and must keep both apps in the merged file.
    agent2 = build_agent(tmp_path, applications)
    agent2.run_once()  # must not raise
    feedback = remote_branch_file(bare, "main-monitoring", "testsite.toml", tmp_path, "mon7")
    assert "app1" in feedback and "app2" in feedback

    # Once first_run is cleared on agent2, a further unchanged pass adds no commit.
    commits_before = status_commits(bare)
    agent2.run_once()
    assert status_commits(bare) == commits_before, "post-first_run unchanged pass must no-op"


# --------------------------------------------------------------------------------------
# Scenario 8: extra-command-output carry-forward -- a real command output captured on the
# updating pass is preserved on a later "Nothing was run" pass.
# --------------------------------------------------------------------------------------

def test_extra_command_output_carry_forward(env, tmp_path):
    url1, first1, second1 = make_app_code_repo_two_commits(tmp_path, "app1")
    cp1 = tmp_path / "deployed" / "app1"
    apps_meta = {
        "app1": {
            "code_url": url1,
            "code_commit_hash": first1,
            "code_local_path": str(cp1),
            "post_updation_command": "echo CARRY_ME_FORWARD",
        }
    }
    deploy_url = make_deploy_repo(tmp_path, "deploy", apps_meta)
    applications = {"app1": f"{deploy_url}@main"}
    agent = build_agent(tmp_path, applications)

    bare = tmp_path / "remotes" / "deploy.git"

    # Pass 1: app updates (clone + checkout) -> post-updation command runs, output captured.
    agent.run_once()
    fb1 = remote_branch_file(bare, "main-monitoring", "testsite.toml", tmp_path, "mon8a")
    assert "CARRY_ME_FORWARD" in fb1["app1"]["extra-command-output"]["command-run-logs"]

    # Pass 2: nothing changed -> check_app path ("Nothing was run"). The earlier real command
    # output must be CARRIED FORWARD, not overwritten with "Nothing was run".
    agent.run_once()
    fb2 = remote_branch_file(bare, "main-monitoring", "testsite.toml", tmp_path, "mon8b")
    assert "CARRY_ME_FORWARD" in fb2["app1"]["extra-command-output"]["command-run-logs"]
    assert "Nothing was run" not in fb2["app1"]["extra-command-output"]["command-run-logs"]


# --------------------------------------------------------------------------------------
# Scenario 9: the private "_cmd_logs" smuggling key never leaks into the committed feedback file.
# --------------------------------------------------------------------------------------

def test_cmd_logs_private_key_not_in_feedback_file(env, tmp_path):
    apps_meta = {}
    for name in ("app1", "app2"):
        url, commit = make_app_code_repo(tmp_path, name)
        apps_meta[name] = app_meta_entry(url, commit, tmp_path / "deployed" / name)
    deploy_url = make_deploy_repo(tmp_path, "deploy", apps_meta)
    applications = {name: f"{deploy_url}@main" for name in apps_meta}
    agent = build_agent(tmp_path, applications)

    agent.run_once()
    bare = tmp_path / "remotes" / "deploy.git"
    feedback = remote_branch_file(bare, "main-monitoring", "testsite.toml", tmp_path, "mon9")
    for name in apps_meta:
        assert "_cmd_logs" not in feedback[name], f"_cmd_logs leaked into feedback for {name}"


# --------------------------------------------------------------------------------------
# Scenario 10: carry-forward is robust to a pre-existing app entry that lacks
# extra-command-output (older schema / hand-edited) -- one malformed entry must not abort the
# whole group's status reporting.
# --------------------------------------------------------------------------------------

def test_carry_forward_robust_to_missing_key(env, tmp_path):
    url1, first1, second1 = make_app_code_repo_two_commits(tmp_path, "app1")
    url2, commit2 = make_app_code_repo(tmp_path, "app2")
    cp1 = tmp_path / "deployed" / "app1"
    cp2 = tmp_path / "deployed" / "app2"
    apps_meta = {
        "app1": {
            "code_url": url1,
            "code_commit_hash": first1,
            "code_local_path": str(cp1),
            "post_updation_command": "echo HELLO",
        },
        "app2": app_meta_entry(url2, commit2, cp2),
    }
    deploy_url = make_deploy_repo(tmp_path, "deploy", apps_meta)
    applications = {"app1": f"{deploy_url}@main", "app2": f"{deploy_url}@main"}
    agent = build_agent(tmp_path, applications)

    bare = tmp_path / "remotes" / "deploy.git"
    agent.run_once()  # pass 1: both apps reconciled, app1 runs its command

    # Corrupt the monitoring clone's feedback: drop app1's extra-command-output (legacy/hand-edit).
    mon = Path(shared_clone_path(deploy_url, "main") + "-monitoring")
    feedback_file = mon / "testsite.toml"
    parsed = toml.loads(feedback_file.read_text())
    del parsed["app1"]["extra-command-output"]
    feedback_file.write_text(toml.dumps(parsed))
    mon_repo = Repo(str(mon))
    mon_repo.git.add(all=True)
    mon_repo.git.commit("-m", "corrupt feedback")
    mon_repo.git.push("origin", "main-monitoring")

    # Pass 2: app1 now hits "Nothing was run" -> carry-forward would index the missing key.
    # It must NOT raise, and app2 must still be reported.
    agent.run_once()  # must not raise
    fb = remote_branch_file(bare, "main-monitoring", "testsite.toml", tmp_path, "mon10")
    assert "app1" in fb and "app2" in fb
