"""End-to-end integration + adversarial tests for the multi-config-file gitops-agent.

These tests use LOCAL bare git repos created under ``tmp_path`` (no network, no glab). For each
test we:

- point ``gitops_agent.git_operations.APP_CONFIGS_DIR`` at a per-test tmp dir (monkeypatched module
  attribute, so the agent's dynamic ``gops.APP_CONFIGS_DIR`` lookups pick it up),
- write a ``config.toml`` and set ``GITOPS_AGENT_CONFIG`` to it BEFORE instantiating GitOpsAgent,
- build a deployment-config bare repo (holding ``<infra>/infra_meta.toml`` + source config files)
  and one or more app CODE bare repos with two commits,
- drive ``GitOpsAgent.run_once()`` and assert real on-disk outcomes.

Run with:  python -m pytest tests/test_integration_multifile.py -q
"""

import sys
from pathlib import Path

import pytest
import toml
from git import Repo

# Make the package importable when running the file standalone.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gitops_agent import git_operations as gops  # noqa: E402
from gitops_agent.agent import GitOpsAgent  # noqa: E402

INFRA = "test-infra"


# --------------------------------------------------------------------------------------------------
# Git helpers (local bare repos only)
# --------------------------------------------------------------------------------------------------
def _config_repo(repo):
    repo.git.config("user.name", "tester")
    repo.git.config("user.email", "tester@example.com")


def make_bare_repo(path, default_branch="main"):
    """Create a bare repo at ``path`` whose default HEAD is ``default_branch``.

    Without this, git's default HEAD (often ``refs/heads/master``) would not match the branch we
    push, so a fresh clone would check out an empty/non-existent branch -- exactly the state a real
    deployment repo never has.
    """
    path = Path(path)
    path.mkdir(parents=True, exist_ok=True)
    repo = Repo.init(path, bare=True, initial_branch=default_branch)
    return repo


def make_code_repo(bare_path, work_path):
    """Build a code bare repo with TWO commits on ``main``.

    Returns (file_url, first_hash, second_hash). The working file ``app.txt`` holds 'v1' at the
    first commit and 'v2' at the second so a checkout to a specific hash is verifiable.
    """
    make_bare_repo(bare_path, default_branch="main")
    work = Path(work_path)
    repo = Repo.clone_from(bare_path, work)
    _config_repo(repo)

    (work / "app.txt").write_text("v1\n")
    repo.git.add(all=True)
    repo.git.commit("-m", "first")
    first = repo.head.commit.hexsha

    (work / "app.txt").write_text("v2\n")
    repo.git.add(all=True)
    repo.git.commit("-m", "second")
    second = repo.head.commit.hexsha

    # Ensure the branch is named 'main' regardless of git's default.
    repo.git.branch("-M", "main")
    repo.git.push("--set-upstream", "origin", "main")
    return f"file://{Path(bare_path).resolve()}", first, second


def make_deploy_repo(bare_path, work_path, infra_meta, source_files, branch="main"):
    """Build a deployment-config bare repo on ``branch``.

    ``infra_meta`` is a dict written as ``<infra>/infra_meta.toml``. ``source_files`` maps a path
    relative to the repo root -> file contents. Returns the file:// URL.
    """
    make_bare_repo(bare_path, default_branch=branch)
    work = Path(work_path)
    repo = Repo.clone_from(bare_path, work)
    _config_repo(repo)

    meta_path = work / INFRA / "infra_meta.toml"
    meta_path.parent.mkdir(parents=True, exist_ok=True)
    with open(meta_path, "w") as f:
        toml.dump(infra_meta, f)

    for rel, contents in source_files.items():
        p = work / rel
        p.parent.mkdir(parents=True, exist_ok=True)
        p.write_text(contents)

    repo.git.add(all=True)
    repo.git.commit("-m", "deploy config")
    repo.git.branch("-M", branch)
    repo.git.push("--set-upstream", "origin", branch)
    return f"file://{Path(bare_path).resolve()}"


def write_config_toml(path, infra_name, applications, interval=300):
    data = {"infra_name": infra_name, "interval": interval, "applications": applications}
    with open(path, "w") as f:
        toml.dump(data, f)


def make_agent(tmp_path, monkeypatch, applications):
    """Point APP_CONFIGS_DIR + GITOPS_AGENT_CONFIG at tmp dirs and build a GitOpsAgent."""
    app_configs = tmp_path / "app-configs"
    app_configs.mkdir(parents=True, exist_ok=True)
    monkeypatch.setattr(gops, "APP_CONFIGS_DIR", app_configs)

    config_toml = tmp_path / "config.toml"
    write_config_toml(config_toml, INFRA, applications)
    monkeypatch.setenv("GITOPS_AGENT_CONFIG", str(config_toml))

    return GitOpsAgent(config_mode=False)


def remote_branch_file(bare_path, branch, rel_path, tmp_path, name="checkout"):
    """Clone ``branch`` of the bare repo and return the contents of ``rel_path`` (or None)."""
    checkout = tmp_path / name
    if checkout.exists():
        import shutil

        shutil.rmtree(checkout)
    repo = Repo.clone_from(bare_path, checkout, branch=branch)
    f = checkout / rel_path
    return f.read_text() if f.exists() else None


# --------------------------------------------------------------------------------------------------
# STEP 2 / scenario 8 -- full happy-path end-to-end
# --------------------------------------------------------------------------------------------------
def test_end_to_end_multifile(tmp_path, monkeypatch):
    code_bare = tmp_path / "code.git"
    code_url, first_hash, second_hash = make_code_repo(code_bare, tmp_path / "code-work")
    code_local = tmp_path / "deployed-code"

    cfg_dst1 = tmp_path / "out" / "config.toml"
    cfg_dst2 = tmp_path / "out" / "nested" / "deep" / "secrets.env"

    infra_meta = {
        "myapp": {
            "code_url": code_url,
            "code_commit_hash": first_hash,  # deliberately the FIRST commit
            "code_local_path": str(code_local),
            "config_files": [
                {"src": f"{INFRA}/config.toml", "dst": str(cfg_dst1)},
                {"src": f"{INFRA}/secrets.env", "dst": str(cfg_dst2)},
            ],
        }
    }
    source_files = {
        f"{INFRA}/config.toml": "key = 1\n",
        f"{INFRA}/secrets.env": "TOKEN=abc\n",
    }
    deploy_bare = tmp_path / "deploy.git"
    deploy_url = make_deploy_repo(deploy_bare, tmp_path / "deploy-work", infra_meta, source_files)

    agent = make_agent(tmp_path, monkeypatch, {"myapp": f"{deploy_url}@main"})
    agent.run_once()

    # Code checked out at the EXACT specified (first) hash.
    deployed = Repo(code_local)
    assert deployed.head.commit.hexsha == first_hash
    assert (code_local / "app.txt").read_text() == "v1\n"

    # Each config file copied to its dst, with parent dirs created.
    assert cfg_dst1.read_text() == "key = 1\n"
    assert cfg_dst2.read_text() == "TOKEN=abc\n"

    # Feedback pushed to {branch}-monitoring on the bare remote.
    fb = remote_branch_file(deploy_bare, "main-monitoring", f"{INFRA}.toml", tmp_path, name="mon")
    assert fb is not None
    parsed = toml.loads(fb)
    assert "myapp" in parsed


def test_checkout_respects_second_hash(tmp_path, monkeypatch):
    code_bare = tmp_path / "code.git"
    code_url, first_hash, second_hash = make_code_repo(code_bare, tmp_path / "code-work")
    code_local = tmp_path / "deployed-code"

    infra_meta = {
        "myapp": {
            "code_url": code_url,
            "code_commit_hash": second_hash,
            "code_local_path": str(code_local),
            "config_files": [],
        }
    }
    deploy_bare = tmp_path / "deploy.git"
    deploy_url = make_deploy_repo(deploy_bare, tmp_path / "deploy-work", infra_meta, {})
    agent = make_agent(tmp_path, monkeypatch, {"myapp": f"{deploy_url}@main"})
    agent.run_once()

    assert Repo(code_local).head.commit.hexsha == second_hash
    assert (code_local / "app.txt").read_text() == "v2\n"


# --------------------------------------------------------------------------------------------------
# Scenario 1 -- multiple entries incl. dst whose parent dirs don't exist
# --------------------------------------------------------------------------------------------------
def test_multiple_entries_create_missing_parent_dirs(tmp_path, monkeypatch):
    code_bare = tmp_path / "code.git"
    code_url, first_hash, _ = make_code_repo(code_bare, tmp_path / "code-work")
    code_local = tmp_path / "deployed-code"

    dst_a = tmp_path / "a" / "config.toml"
    dst_b = tmp_path / "totally" / "absent" / "tree" / "b.env"

    infra_meta = {
        "myapp": {
            "code_url": code_url,
            "code_commit_hash": first_hash,
            "code_local_path": str(code_local),
            "config_files": [
                {"src": f"{INFRA}/a.toml", "dst": str(dst_a)},
                {"src": f"{INFRA}/b.env", "dst": str(dst_b)},
            ],
        }
    }
    source_files = {f"{INFRA}/a.toml": "A\n", f"{INFRA}/b.env": "B\n"}
    deploy_bare = tmp_path / "deploy.git"
    deploy_url = make_deploy_repo(deploy_bare, tmp_path / "deploy-work", infra_meta, source_files)

    agent = make_agent(tmp_path, monkeypatch, {"myapp": f"{deploy_url}@main"})
    agent.run_once()

    assert dst_a.read_text() == "A\n"
    assert dst_b.read_text() == "B\n"


# --------------------------------------------------------------------------------------------------
# Scenario 2 -- one src missing: skipped, others still copy, no exception
# --------------------------------------------------------------------------------------------------
def test_missing_src_is_skipped_others_copy(tmp_path, monkeypatch):
    code_bare = tmp_path / "code.git"
    code_url, first_hash, _ = make_code_repo(code_bare, tmp_path / "code-work")
    code_local = tmp_path / "deployed-code"

    dst_ok = tmp_path / "out" / "ok.toml"
    dst_missing = tmp_path / "out" / "missing.toml"

    infra_meta = {
        "myapp": {
            "code_url": code_url,
            "code_commit_hash": first_hash,
            "code_local_path": str(code_local),
            "config_files": [
                {"src": f"{INFRA}/ok.toml", "dst": str(dst_ok)},
                {"src": f"{INFRA}/does_not_exist.toml", "dst": str(dst_missing)},
            ],
        }
    }
    source_files = {f"{INFRA}/ok.toml": "OK\n"}  # note: no does_not_exist.toml
    deploy_bare = tmp_path / "deploy.git"
    deploy_url = make_deploy_repo(deploy_bare, tmp_path / "deploy-work", infra_meta, source_files)

    agent = make_agent(tmp_path, monkeypatch, {"myapp": f"{deploy_url}@main"})
    agent.run_once()  # must NOT raise

    assert dst_ok.read_text() == "OK\n"
    assert not dst_missing.exists()


# --------------------------------------------------------------------------------------------------
# Scenario 3 -- old format rejected (each legacy key alone, and both together)
# --------------------------------------------------------------------------------------------------
@pytest.mark.parametrize(
    "legacy_section",
    [
        {"config_src_path_rel_in_this_repo": f"{INFRA}/c.toml"},
        {"config_dst_path_abs": "/opt/app/c.toml"},
        {
            "config_src_path_rel_in_this_repo": f"{INFRA}/c.toml",
            "config_dst_path_abs": "/opt/app/c.toml",
        },
    ],
    ids=["src_only", "dst_only", "both"],
)
def test_legacy_keys_rejected_end_to_end(tmp_path, monkeypatch, legacy_section):
    code_bare = tmp_path / "code.git"
    code_url, first_hash, _ = make_code_repo(code_bare, tmp_path / "code-work")
    code_local = tmp_path / "deployed-code"

    app_section = {
        "code_url": code_url,
        "code_commit_hash": first_hash,
        "code_local_path": str(code_local),
    }
    app_section.update(legacy_section)
    infra_meta = {"myapp": app_section}
    deploy_bare = tmp_path / "deploy.git"
    deploy_url = make_deploy_repo(deploy_bare, tmp_path / "deploy-work", infra_meta, {})

    agent = make_agent(tmp_path, monkeypatch, {"myapp": f"{deploy_url}@main"})
    with pytest.raises(ValueError) as exc:
        agent.run_once()
    msg = str(exc.value)
    for key in legacy_section:
        assert key in msg
    assert "config_files" in msg


# --------------------------------------------------------------------------------------------------
# Scenario 4 -- neither config_files nor legacy keys: no copies, no error
# --------------------------------------------------------------------------------------------------
def test_no_config_files_no_error(tmp_path, monkeypatch):
    code_bare = tmp_path / "code.git"
    code_url, first_hash, _ = make_code_repo(code_bare, tmp_path / "code-work")
    code_local = tmp_path / "deployed-code"

    infra_meta = {
        "myapp": {
            "code_url": code_url,
            "code_commit_hash": first_hash,
            "code_local_path": str(code_local),
        }
    }
    deploy_bare = tmp_path / "deploy.git"
    deploy_url = make_deploy_repo(deploy_bare, tmp_path / "deploy-work", infra_meta, {})
    agent = make_agent(tmp_path, monkeypatch, {"myapp": f"{deploy_url}@main"})
    agent.run_once()  # must not raise

    assert Repo(code_local).head.commit.hexsha == first_hash


# --------------------------------------------------------------------------------------------------
# Scenario 5 -- perpetual-loop regression: missing-src entry must not re-trigger forever
# --------------------------------------------------------------------------------------------------
def test_missing_src_does_not_retrigger_update(tmp_path, monkeypatch):
    code_bare = tmp_path / "code.git"
    code_url, first_hash, _ = make_code_repo(code_bare, tmp_path / "code-work")
    code_local = tmp_path / "deployed-code"

    dst_ok = tmp_path / "out" / "ok.toml"
    dst_missing = tmp_path / "out" / "missing.toml"
    infra_meta = {
        "myapp": {
            "code_url": code_url,
            "code_commit_hash": first_hash,
            "code_local_path": str(code_local),
            "config_files": [
                {"src": f"{INFRA}/ok.toml", "dst": str(dst_ok)},
                {"src": f"{INFRA}/missing.toml", "dst": str(dst_missing)},
            ],
        }
    }
    deploy_bare = tmp_path / "deploy.git"
    deploy_url = make_deploy_repo(
        deploy_bare, tmp_path / "deploy-work", infra_meta, {f"{INFRA}/ok.toml": "OK\n"}
    )
    agent = make_agent(tmp_path, monkeypatch, {"myapp": f"{deploy_url}@main"})

    # Drive a full reconcile pass TWICE (as the agent loop would).
    agent.run_once()
    agent.run_once()
    # After both passes, the missing-src entry must NOT be reported as drift; otherwise the app
    # would be flagged for update on every single pass forever.
    update_again, _, _ = agent.pull_dep_cfg("myapp", deploy_url, "main")
    assert update_again is False
    assert dst_ok.read_text() == "OK\n"
    assert not dst_missing.exists()


# --------------------------------------------------------------------------------------------------
# Scenario 6 -- idempotency: normal multi-file run twice -> no spurious update on 2nd run
# --------------------------------------------------------------------------------------------------
def test_idempotent_second_run_no_update(tmp_path, monkeypatch):
    code_bare = tmp_path / "code.git"
    code_url, first_hash, _ = make_code_repo(code_bare, tmp_path / "code-work")
    code_local = tmp_path / "deployed-code"

    dst1 = tmp_path / "out" / "config.toml"
    dst2 = tmp_path / "out" / "secrets.env"
    infra_meta = {
        "myapp": {
            "code_url": code_url,
            "code_commit_hash": first_hash,
            "code_local_path": str(code_local),
            "config_files": [
                {"src": f"{INFRA}/config.toml", "dst": str(dst1)},
                {"src": f"{INFRA}/secrets.env", "dst": str(dst2)},
            ],
        }
    }
    deploy_bare = tmp_path / "deploy.git"
    deploy_url = make_deploy_repo(
        deploy_bare,
        tmp_path / "deploy-work",
        infra_meta,
        {f"{INFRA}/config.toml": "k=1\n", f"{INFRA}/secrets.env": "T=1\n"},
    )
    agent = make_agent(tmp_path, monkeypatch, {"myapp": f"{deploy_url}@main"})

    agent.run_once()
    mtime1 = dst1.stat().st_mtime_ns

    update2, _, _ = agent.pull_dep_cfg("myapp", deploy_url, "main")
    assert update2 is False
    # Files untouched.
    assert dst1.read_text() == "k=1\n"
    assert dst2.read_text() == "T=1\n"
    assert dst1.stat().st_mtime_ns == mtime1


# --------------------------------------------------------------------------------------------------
# Scenario 7 -- drift correction: externally modify a dst -> next run restores it
# --------------------------------------------------------------------------------------------------
def test_drift_correction_restores_dst(tmp_path, monkeypatch):
    code_bare = tmp_path / "code.git"
    code_url, first_hash, _ = make_code_repo(code_bare, tmp_path / "code-work")
    code_local = tmp_path / "deployed-code"

    dst = tmp_path / "out" / "config.toml"
    infra_meta = {
        "myapp": {
            "code_url": code_url,
            "code_commit_hash": first_hash,
            "code_local_path": str(code_local),
            "config_files": [{"src": f"{INFRA}/config.toml", "dst": str(dst)}],
        }
    }
    deploy_bare = tmp_path / "deploy.git"
    deploy_url = make_deploy_repo(
        deploy_bare, tmp_path / "deploy-work", infra_meta, {f"{INFRA}/config.toml": "good=1\n"}
    )
    agent = make_agent(tmp_path, monkeypatch, {"myapp": f"{deploy_url}@main"})

    agent.run_once()
    assert dst.read_text() == "good=1\n"

    # Externally tamper with the deployed config.
    dst.write_text("TAMPERED=999\n")

    update2, _, _ = agent.pull_dep_cfg("myapp", deploy_url, "main")
    assert update2 is True
    agent.run_once()
    assert dst.read_text() == "good=1\n"


# --------------------------------------------------------------------------------------------------
# Multiple apps in one pass + verifying monitoring feedback for each
# --------------------------------------------------------------------------------------------------
def test_two_apps_one_pass(tmp_path, monkeypatch):
    # App A
    a_code_url, a_hash, _ = make_code_repo(tmp_path / "a.git", tmp_path / "a-work")
    a_local = tmp_path / "a-deployed"
    a_dst = tmp_path / "outa" / "a.toml"
    a_meta = {
        "appa": {
            "code_url": a_code_url,
            "code_commit_hash": a_hash,
            "code_local_path": str(a_local),
            "config_files": [{"src": f"{INFRA}/a.toml", "dst": str(a_dst)}],
        }
    }
    a_deploy_bare = tmp_path / "a-deploy.git"
    a_deploy_url = make_deploy_repo(
        a_deploy_bare, tmp_path / "a-deploy-work", a_meta, {f"{INFRA}/a.toml": "AA\n"}
    )

    # App B
    b_code_url, b_hash, _ = make_code_repo(tmp_path / "b.git", tmp_path / "b-work")
    b_local = tmp_path / "b-deployed"
    b_dst = tmp_path / "outb" / "b.toml"
    b_meta = {
        "appb": {
            "code_url": b_code_url,
            "code_commit_hash": b_hash,
            "code_local_path": str(b_local),
            "config_files": [{"src": f"{INFRA}/b.toml", "dst": str(b_dst)}],
        }
    }
    b_deploy_bare = tmp_path / "b-deploy.git"
    b_deploy_url = make_deploy_repo(
        b_deploy_bare, tmp_path / "b-deploy-work", b_meta, {f"{INFRA}/b.toml": "BB\n"}
    )

    agent = make_agent(
        tmp_path,
        monkeypatch,
        {"appa": f"{a_deploy_url}@main", "appb": f"{b_deploy_url}@main"},
    )
    agent.run_once()

    assert a_dst.read_text() == "AA\n"
    assert b_dst.read_text() == "BB\n"
    assert Repo(a_local).head.commit.hexsha == a_hash
    assert Repo(b_local).head.commit.hexsha == b_hash

    fb_a = remote_branch_file(a_deploy_bare, "main-monitoring", f"{INFRA}.toml", tmp_path, "mona")
    fb_b = remote_branch_file(b_deploy_bare, "main-monitoring", f"{INFRA}.toml", tmp_path, "monb")
    assert "appa" in toml.loads(fb_a)
    assert "appb" in toml.loads(fb_b)


if __name__ == "__main__":
    sys.exit(__import__("pytest").main([__file__, "-q"]))
