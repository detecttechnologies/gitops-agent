"""Health-highlight tests for the monitoring feedback (roadmap item 1).

Covers the new per-app `status` field, the top-of-file `overall_status`, and the health-reflecting
single per-group commit message. These run the WHOLE reconcile pass (GitOpsAgent.run_once) against
REAL local bare git repos -- no network, no /opt, no root -- reusing the local-git harness from
tests/test_integration_monitoring.py. Pure-function unit tests for compute_app_status live at the
bottom (no repos needed).

Run with:  python -m pytest tests/test_integration_health.py -q
"""

import subprocess as sp
from pathlib import Path

from gitops_agent.agent import compute_app_status, summarize_group_health, shared_clone_path

# Reuse the integration harness verbatim. The `env` fixture is provided by tests/conftest.py and
# discovered by name (no import needed). These are plain helper functions, imported normally.
from tests.test_integration_monitoring import (
    _is_status_commit,
    app_meta_entry,
    build_agent,
    make_app_code_repo,
    make_deploy_repo,
    remote_branch_commits,
    remote_branch_file,
    rewrite_deploy_meta,
    status_commits,
)


def _last_status_commit_msg(bare, branch="main-monitoring"):
    """Return the subject of the newest health-status commit on the monitoring branch."""
    for h in remote_branch_commits(bare, branch):
        msg = sp.run(["git", "log", "-1", "--pretty=%s", h], cwd=str(bare),
                     capture_output=True, text=True).stdout.strip()
        if _is_status_commit(msg):
            return msg
    return None


# --------------------------------------------------------------------------------------
# Scenario 1: all apps healthy
# --------------------------------------------------------------------------------------

def test_all_apps_healthy(env, tmp_path):
    apps_meta = {}
    for name in ("app1", "app2", "app3"):
        url, commit = make_app_code_repo(tmp_path, name)
        apps_meta[name] = app_meta_entry(url, commit, tmp_path / "deployed" / name)
    deploy_url = make_deploy_repo(tmp_path, "deploy", apps_meta)
    applications = {name: f"{deploy_url}@main" for name in apps_meta}
    agent = build_agent(tmp_path, applications)

    bare = tmp_path / "remotes" / "deploy.git"
    agent.run_once()

    feedback = remote_branch_file(bare, "main-monitoring", "testsite.toml", tmp_path, "h1")
    for name in apps_meta:
        assert feedback[name]["status"] == "✅ healthy", feedback[name]
    assert feedback["overall_status"] == "✅ all 3 apps healthy", feedback["overall_status"]

    msg = _last_status_commit_msg(bare)
    assert msg == "✅ Status: all 3 apps healthy", msg


# --------------------------------------------------------------------------------------
# Scenario 2: one app fails to update (bad commit hash) -> that app flagged, others healthy
# --------------------------------------------------------------------------------------

def test_one_app_update_fails(env, tmp_path):
    apps_meta = {}
    for name in ("app1", "app2", "app3"):
        url, commit = make_app_code_repo(tmp_path, name)
        apps_meta[name] = app_meta_entry(url, commit, tmp_path / "deployed" / name)
    # Point app2 at a commit hash that does not exist -> update_git_repo's reset/checkout fails,
    # so app-updation return-value is False.
    apps_meta["app2"]["code_commit_hash"] = "deadbeef" * 5
    deploy_url = make_deploy_repo(tmp_path, "deploy", apps_meta)
    applications = {name: f"{deploy_url}@main" for name in apps_meta}
    agent = build_agent(tmp_path, applications)

    bare = tmp_path / "remotes" / "deploy.git"
    agent.run_once()

    feedback = remote_branch_file(bare, "main-monitoring", "testsite.toml", tmp_path, "h2")
    assert feedback["app1"]["status"] == "✅ healthy"
    assert feedback["app3"]["status"] == "✅ healthy"
    assert feedback["app2"]["status"] == "❌ app update failed", feedback["app2"]
    assert feedback["overall_status"] == "⚠️ 1 of 3 apps need attention: app2", feedback["overall_status"]

    msg = _last_status_commit_msg(bare)
    assert msg == "⚠️ Status: 1 of 3 issues (app2)", msg


# --------------------------------------------------------------------------------------
# Scenario 3: a post_updation_command that exits non-zero -> app flagged as issue
# --------------------------------------------------------------------------------------

def test_post_command_nonzero_flags_issue(env, tmp_path):
    url1, commit1 = make_app_code_repo(tmp_path, "app1")
    url2, commit2 = make_app_code_repo(tmp_path, "app2")
    cp1 = tmp_path / "deployed" / "app1"
    cp2 = tmp_path / "deployed" / "app2"
    apps_meta = {
        "app1": {
            "code_url": url1,
            "code_commit_hash": commit1,
            "code_local_path": str(cp1),
            "post_updation_command": "exit 3",
        },
        "app2": app_meta_entry(url2, commit2, cp2),
    }
    deploy_url = make_deploy_repo(tmp_path, "deploy", apps_meta)
    applications = {"app1": f"{deploy_url}@main", "app2": f"{deploy_url}@main"}
    agent = build_agent(tmp_path, applications)

    bare = tmp_path / "remotes" / "deploy.git"
    agent.run_once()

    feedback = remote_branch_file(bare, "main-monitoring", "testsite.toml", tmp_path, "h3")
    assert feedback["app1"]["status"] == "❌ post-command exited non-zero", feedback["app1"]
    assert feedback["app2"]["status"] == "✅ healthy"
    assert feedback["overall_status"] == "⚠️ 1 of 2 apps need attention: app1"

    msg = _last_status_commit_msg(bare)
    assert msg == "⚠️ Status: 1 of 2 issues (app1)", msg


# --------------------------------------------------------------------------------------
# Scenario 4: no-op idempotency WITH health fields present -> no new monitoring commit, file
# byte-identical across two identical runs.
# --------------------------------------------------------------------------------------

def test_idempotent_with_health_fields_byte_identical(env, tmp_path):
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

    # The on-disk monitoring clone's feedback file after run 1.
    mon = Path(shared_clone_path(deploy_url, "main") + "-monitoring")
    feedback_file = mon / "testsite.toml"
    bytes_after_first = feedback_file.read_bytes()
    # Health fields are actually present.
    text = feedback_file.read_text()
    assert "overall_status" in text
    assert "status =" in text

    # Second identical pass: NO new commit, and the merged file must be byte-identical (status is a
    # deterministic function of the unchanged feedback, so nothing rewrites).
    agent.run_once()
    commits_after_second = remote_branch_commits(bare, "main-monitoring")
    assert commits_after_second == commits_after_first, "second run must not add a monitoring commit"
    assert feedback_file.read_bytes() == bytes_after_first, "no-op run must leave the file byte-identical"


# --------------------------------------------------------------------------------------
# Scenario 4b: first_run flake regression. A FRESH agent (first_run=True) whose reconcile produces
# a byte-identical file (no git commit, e.g. same-second last-updated) must still CLEAR first_run, so
# a subsequent unchanged pass does NOT push a spurious status commit. Before the fix, first_run was
# only cleared inside the push branch, so a no-commit first_run pass stranded the flag.
# --------------------------------------------------------------------------------------

def test_fresh_agent_no_commit_pass_clears_first_run(env, tmp_path):
    apps_meta = {}
    for name in ("app1", "app2"):
        url, commit = make_app_code_repo(tmp_path, name)
        apps_meta[name] = app_meta_entry(url, commit, tmp_path / "deployed" / name)
    deploy_url = make_deploy_repo(tmp_path, "deploy", apps_meta)
    applications = {name: f"{deploy_url}@main" for name in apps_meta}

    bare = tmp_path / "remotes" / "deploy.git"

    # Agent 1 establishes the monitoring file + status commit, then first_run clears.
    agent1 = build_agent(tmp_path, applications)
    agent1.run_once()
    baseline = status_commits(bare)
    assert baseline == 1

    # Agent 2 is FRESH (first_run=True) over the already-current state. Its reconcile may or may not
    # land in a new second; either way, after one run_once first_run MUST be cleared regardless of
    # whether a commit was produced -- that is the fix.
    agent2 = build_agent(tmp_path, applications)
    assert agent2.first_run is True
    agent2.run_once()
    assert agent2.first_run is False, "first_run must clear after a completed pass, commit or not"

    # A further unchanged pass on the now-settled agent must add NO commit.
    commits_before = status_commits(bare)
    agent2.run_once()
    assert status_commits(bare) == commits_before, "settled agent unchanged pass must no-op"


# --------------------------------------------------------------------------------------
# Scenario 4c: stale/removed app behaviour is documented (carry-forward preserves prior apps). An app
# removed from the deploy config is intentionally retained in the monitoring file, so overall_status
# keeps reflecting it -- this pins the deliberate preserve-not-prune decision.
# --------------------------------------------------------------------------------------

def test_removed_app_is_preserved_in_overall_status(env, tmp_path):
    url1, commit1 = make_app_code_repo(tmp_path, "app1")
    url2, commit2 = make_app_code_repo(tmp_path, "app2")
    cp1 = tmp_path / "deployed" / "app1"
    cp2 = tmp_path / "deployed" / "app2"
    apps_meta = {
        "app1": app_meta_entry(url1, commit1, cp1),
        "app2": app_meta_entry(url2, commit2, cp2),
    }
    deploy_url = make_deploy_repo(tmp_path, "deploy", apps_meta)
    applications = {"app1": f"{deploy_url}@main", "app2": f"{deploy_url}@main"}
    agent = build_agent(tmp_path, applications)

    bare = tmp_path / "remotes" / "deploy.git"
    agent.run_once()
    fb1 = remote_branch_file(bare, "main-monitoring", "testsite.toml", tmp_path, "h4c1")
    assert fb1["overall_status"] == "✅ all 2 apps healthy"

    # Remove app2 from the deploy config AND from the agent's app list, then re-run.
    del apps_meta["app2"]
    rewrite_deploy_meta(tmp_path, "deploy", apps_meta)
    applications2 = {"app1": f"{deploy_url}@main"}
    agent2 = build_agent(tmp_path, applications2)
    agent2.run_once()

    fb2 = remote_branch_file(bare, "main-monitoring", "testsite.toml", tmp_path, "h4c2")
    # app2 is preserved (carry-forward), so the file still records both apps.
    assert "app1" in fb2 and "app2" in fb2
    assert fb2["app1"]["status"] == "✅ healthy"


# --------------------------------------------------------------------------------------
# Scenario 5: compute_app_status unit tests (pure, no repos)
# --------------------------------------------------------------------------------------

def _body(cfg_ret=True, app_ret=True, cmd_ret="{'post': 0}"):
    return {
        "config-updation": {"updation-return-value": cfg_ret, "git-status": "", "git-repo-latest-commit": ""},
        "app-updation": {"updation-return-value": app_ret, "git-status": "", "git-repo-latest-commit": ""},
        "extra-command-output": {"command-return-val": cmd_ret, "command-run-logs": ""},
    }


def test_compute_app_status_healthy():
    ok, label = compute_app_status(_body())
    assert ok is True
    assert label == "✅ healthy"


def test_compute_app_status_healthy_nothing_run():
    ok, label = compute_app_status(_body(cmd_ret="True"))
    assert ok is True and label == "✅ healthy"
    ok2, label2 = compute_app_status(_body(cmd_ret=""))
    assert ok2 is True and label2 == "✅ healthy"


def test_compute_app_status_failed_app():
    ok, label = compute_app_status(_body(app_ret=False))
    assert ok is False
    assert label == "❌ app update failed"


def test_compute_app_status_failed_cfg():
    ok, label = compute_app_status(_body(cfg_ret=False))
    assert ok is False
    assert label == "❌ config update failed"


def test_compute_app_status_nonzero_command():
    ok, label = compute_app_status(_body(cmd_ret="{'pre': 0, 'post': 2}"))
    assert ok is False
    assert label == "❌ post-command exited non-zero"


def test_compute_app_status_none_command_codes_ok():
    ok, label = compute_app_status(_body(cmd_ret="{'pre': None, 'post': None}"))
    assert ok is True and label == "✅ healthy"


def test_compute_app_status_malformed_does_not_crash():
    for bad in (None, "not a dict", 42, {}, {"config-updation": "x"}, {"app-updation": {}}):
        ok, label = compute_app_status(bad)
        assert ok is False
        assert isinstance(label, str) and label


def test_compute_app_status_unparseable_command_val_treated_ok():
    # A legacy/hand-edited command-return-val that isn't a dict literal must not flag a failure
    # solely on its own; the app/cfg returns still decide health.
    ok, label = compute_app_status(_body(cmd_ret="garbage not a literal"))
    assert ok is True and label == "✅ healthy"


def test_compute_app_status_both_fail_reports_app_first():
    # When BOTH app and config updates fail, the documented precedence reports the app failure.
    ok, label = compute_app_status(_body(cfg_ret=False, app_ret=False))
    assert ok is False
    assert label == "❌ app update failed"


def test_compute_app_status_non_numeric_command_code_flags_failure():
    # A command-return-val that parses to a dict but holds a non-numeric code is flagged (fail-safe).
    ok, label = compute_app_status(_body(cmd_ret="{'post': 'boom'}"))
    assert ok is False
    assert label == "❌ post-command exited non-zero"


def test_compute_app_status_command_val_parses_to_non_dict_ok():
    # ast.literal_eval succeeding on a non-dict (e.g. a list / int) must be treated as no failure.
    for cmd_ret in ("[1, 2]", "0"):
        ok, label = compute_app_status(_body(cmd_ret=cmd_ret))
        assert ok is True and label == "✅ healthy", cmd_ret


def test_summarize_group_health_no_apps():
    overall, commit = summarize_group_health({"last-updated": "t"})
    assert overall == "⚠️ no apps reported"
    assert commit == "⚠️ Status: no apps reported"


def test_summarize_group_health_multiple_unhealthy_sorted():
    # Two unhealthy apps -> both named, alphabetically sorted (the order is a contract).
    feedback = {
        "last-updated": "t",
        "zzz": _body(app_ret=False),
        "aaa": _body(cfg_ret=False),
        "mmm": _body(),
    }
    overall, commit = summarize_group_health(feedback)
    assert overall == "⚠️ 2 of 3 apps need attention: aaa, zzz", overall
    assert commit == "⚠️ Status: 2 of 3 issues (aaa, zzz)", commit


def test_summarize_group_health_skips_meta_keys():
    feedback = {
        "last-updated": "2026-06-16 00:00:00",
        "overall_status": "stale",
        "app1": _body(),
        "app2": _body(app_ret=False),
    }
    overall, commit = summarize_group_health(feedback)
    # app1 healthy, app2 failed -> exactly one unhealthy; meta keys ignored.
    assert overall == "⚠️ 1 of 2 apps need attention: app2", overall
    assert commit == "⚠️ Status: 1 of 2 issues (app2)", commit


def test_summarize_group_health_all_healthy():
    feedback = {"last-updated": "t", "a": _body(), "b": _body()}
    overall, commit = summarize_group_health(feedback)
    assert overall == "✅ all 2 apps healthy"
    assert commit == "✅ Status: all 2 apps healthy"
