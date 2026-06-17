"""Trim-monitoring-history tests (roadmap item 2).

The monitoring branch ({branch}-monitoring) is a machine-generated orphan branch of linear,
full-snapshot commits. To stop it growing without bound we keep only the last N days of commits and
squash everything older into ONE synthetic base commit, then force-push the rewritten branch. These
tests exercise both the focused helper (trim_monitoring_history) and the whole reconcile pass
(GitOpsAgent.run_once with a tiny retention set via config) against REAL local bare git repos -- no
network, no /opt, no root -- reusing the harness from tests/test_integration_monitoring.py.

Old commits are simulated WITHOUT waiting: we create real monitoring commits with explicit
backdated GIT_AUTHOR_DATE / GIT_COMMITTER_DATE, so committer-date ordering relative to the cutoff is
deterministic and not timing-flaky.

Invariants covered (see roadmap):
  1. Content integrity   -> test_trim_preserves_head_content_byte_identical
  2. Window kept         -> test_trim_keeps_window_squashes_old (+ remote check)
  3. Remote updated      -> test_trim_force_updates_remote
  4. No trim when ok     -> test_all_recent_no_rewrite_no_force_push
  5. Continuity          -> test_continuity_next_run_appends_normally
  6. Edge cases          -> test_edge_all_old_keeps_latest_state / test_edge_single_commit /
                            test_edge_brand_new_branch_first_push
  + retention override   -> test_retention_override_via_config_respected
  + pure boundary logic  -> test_commits_to_trim_* unit tests

Run with:  python -m pytest tests/test_integration_trim.py -q
"""

import os
import subprocess as sp
import time
from pathlib import Path

from git import Repo

from gitops_agent.agent import (
    _commits_to_trim,
    shared_clone_path,
    trim_monitoring_history,
)

# Reuse the integration harness verbatim. The `env` fixture is provided by tests/conftest.py and
# discovered by name (no import needed). These are plain helper functions, imported normally.
from tests.test_integration_monitoring import (
    app_meta_entry,
    build_agent,
    make_app_code_repo,
    make_app_code_repo_two_commits,
    make_deploy_repo,
    remote_branch_commits,
    remote_branch_file,
    rewrite_deploy_meta,
    status_commits,
)


# --------------------------------------------------------------------------------------
# Low-level helpers for fabricating backdated monitoring history directly on a clone.
# --------------------------------------------------------------------------------------

DAY = 86400


def _git_env_dated(epoch):
    """Return a git env that forces BOTH author and committer dates to a fixed epoch second."""
    env = dict(os.environ)
    iso = f"{int(epoch)} +0000"  # git accepts "<unixtime> <tz>"
    env["GIT_AUTHOR_DATE"] = iso
    env["GIT_COMMITTER_DATE"] = iso
    env.setdefault("GIT_AUTHOR_NAME", "test")
    env.setdefault("GIT_AUTHOR_EMAIL", "test@example.com")
    env.setdefault("GIT_COMMITTER_NAME", "test")
    env.setdefault("GIT_COMMITTER_EMAIL", "test@example.com")
    return env


def _commit_dated(repo_dir, filename, content, message, epoch):
    """Write a file and commit it on the current branch with a backdated author+committer date."""
    (Path(repo_dir) / filename).write_text(content)
    sp.run(["git", "add", "-A"], cwd=str(repo_dir), check=True, capture_output=True)
    # --allow-empty so a deliberately-repeated snapshot (no-op diff) still creates a distinct commit;
    # this lets tests exercise the rebase's --empty=keep behaviour.
    sp.run(
        ["git", "commit", "--allow-empty", "-m", message],
        cwd=str(repo_dir), env=_git_env_dated(epoch), check=True, capture_output=True,
    )
    return sp.run(
        ["git", "rev-parse", "HEAD"], cwd=str(repo_dir), capture_output=True, text=True
    ).stdout.strip()


def _make_monitoring_clone_with_history(tmp_path, name, dated_commits):
    """Create a bare remote + orphan {name}-monitoring branch populated with backdated commits.

    dated_commits is a list of (content, days_ago) tuples, OLDEST first. Returns (bare_path, clone_dir,
    branch). The clone is configured with a user identity so the trim helper can commit-tree.
    """
    bare = tmp_path / "remotes" / f"{name}.git"
    bare.parent.mkdir(parents=True, exist_ok=True)
    sp.run(["git", "init", "--bare", "-b", "main", str(bare)], check=True, capture_output=True)

    clone = tmp_path / "monclone" / name
    clone.parent.mkdir(parents=True, exist_ok=True)
    sp.run(["git", "clone", f"file://{bare}", str(clone)], check=True, capture_output=True)
    sp.run(["git", "config", "user.name", "test"], cwd=str(clone), check=True, capture_output=True)
    sp.run(["git", "config", "user.email", "test@example.com"], cwd=str(clone), check=True, capture_output=True)

    branch = "main-monitoring"
    sp.run(["git", "checkout", "--orphan", branch], cwd=str(clone), check=True, capture_output=True)

    now = time.time()
    for content, days_ago in dated_commits:
        _commit_dated(clone, "testsite.toml", content, f"status {content}", now - days_ago * DAY)
    sp.run(["git", "push", "-u", "origin", branch], cwd=str(clone), check=True, capture_output=True)
    return bare, clone, branch


def _force_push(clone, branch):
    sp.run(["git", "push", "--force", "origin", branch], cwd=str(clone), check=True, capture_output=True)


def _subjects(repo_dir, branch="main-monitoring"):
    out = sp.run(
        ["git", "log", "--pretty=%s", branch], cwd=str(repo_dir), capture_output=True, text=True
    )
    return [s for s in out.stdout.splitlines() if s]


def _head_file(repo_dir, rel="testsite.toml"):
    return (Path(repo_dir) / rel).read_bytes()


# --------------------------------------------------------------------------------------
# Pure boundary logic unit tests (no repos)
# --------------------------------------------------------------------------------------

def test_commits_to_trim_no_old_commits():
    # All commits at/after cutoff -> no trim.
    needs, boundary = _commits_to_trim([100, 200, 300], cutoff_ts=50)
    assert needs is False
    assert boundary is None


def test_commits_to_trim_mixed_boundary_is_newest_old():
    # dates oldest..newest: 10, 20, 30(old) | 60, 70 (kept). cutoff 50 -> boundary index 2.
    needs, boundary = _commits_to_trim([10, 20, 30, 60, 70], cutoff_ts=50)
    assert needs is True
    assert boundary == 2


def test_commits_to_trim_all_old_boundary_is_head():
    needs, boundary = _commits_to_trim([10, 20, 30], cutoff_ts=100)
    assert needs is True
    assert boundary == 2  # last index = HEAD


def test_commits_to_trim_exact_cutoff_is_kept():
    # A commit exactly AT the cutoff is KEPT (>= cutoff), so only the strictly-older one trims.
    needs, boundary = _commits_to_trim([40, 50, 60], cutoff_ts=50)
    assert needs is True
    assert boundary == 0  # only the date-40 commit is older than 50


# --------------------------------------------------------------------------------------
# Invariant 2 + 3: mixed old+recent -> old squashed to one base, recent kept, remote force-updated.
# --------------------------------------------------------------------------------------

def test_trim_keeps_window_squashes_old(env, tmp_path):
    # 3 OLD commits (50/45/40 days ago) + 2 RECENT (5/1 days ago); retention 30 days.
    dated = [("old1", 50), ("old2", 45), ("old3", 40), ("recent1", 5), ("recent2", 1)]
    bare, clone, branch = _make_monitoring_clone_with_history(tmp_path, "deploy", dated)
    repo = Repo(str(clone))

    subjects_before = _subjects(clone)
    assert subjects_before == ["status recent2", "status recent1", "status old3", "status old2", "status old1"]

    rewrote = trim_monitoring_history(repo, branch, retention_days=30)
    assert rewrote is True

    subjects_after = _subjects(clone)
    # Exactly: 2 kept commits (newest first) + the single synthetic base.
    assert len(subjects_after) == 3, subjects_after
    assert subjects_after[0] == "status recent2"
    assert subjects_after[1] == "status recent1"
    assert subjects_after[2].startswith("📉 History trimmed:"), subjects_after[2]

    # Invariant 3: force-push and confirm the remote matches the trimmed local branch.
    _force_push(clone, branch)
    assert _subjects(bare) == subjects_after


# --------------------------------------------------------------------------------------
# Kept-window integrity: a kept-window commit whose snapshot is a NO-OP diff vs its parent must NOT
# be silently dropped by the rebase (--empty=keep). Status snapshots can legitimately repeat content,
# so dropping an "empty" replayed commit would shorten the remote history before a force-push.
# --------------------------------------------------------------------------------------

def test_trim_keeps_window_with_empty_diff_commit(env, tmp_path):
    # old (50d) + recent window of 3 where recent2 repeats recent1's content (empty diff), recent3 new.
    dated = [("old", 50), ("recentA", 5), ("recentA", 3), ("recentB", 1)]
    bare, clone, branch = _make_monitoring_clone_with_history(tmp_path, "deploy", dated)
    repo = Repo(str(clone))

    rewrote = trim_monitoring_history(repo, branch, retention_days=30)
    assert rewrote is True
    subjects = _subjects(clone)
    # 3 kept commits (including the empty-diff one) + 1 synthetic base = 4. None silently dropped.
    assert len(subjects) == 4, subjects
    assert subjects[-1].startswith("📉 History trimmed:")
    assert _head_file(clone) == b"recentB"


# --------------------------------------------------------------------------------------
# Invariant 1: content integrity -- HEAD file byte-identical before and after the trim.
# --------------------------------------------------------------------------------------

def test_trim_preserves_head_content_byte_identical(env, tmp_path):
    dated = [("OLDCONTENT", 60), ("MIDCONTENT", 40), ("NEWCONTENT", 2)]
    bare, clone, branch = _make_monitoring_clone_with_history(tmp_path, "deploy", dated)
    repo = Repo(str(clone))

    before = _head_file(clone)
    assert before == b"NEWCONTENT"

    rewrote = trim_monitoring_history(repo, branch, retention_days=30)
    assert rewrote is True
    after = _head_file(clone)
    assert after == before, "trim must not alter current HEAD content"


# --------------------------------------------------------------------------------------
# Invariant 4: all-recent history -> NO rewrite, NO force-push (branch + remote unchanged).
# --------------------------------------------------------------------------------------

def test_all_recent_no_rewrite_no_force_push(env, tmp_path):
    dated = [("r1", 10), ("r2", 5), ("r3", 1)]  # all within 30 days
    bare, clone, branch = _make_monitoring_clone_with_history(tmp_path, "deploy", dated)
    repo = Repo(str(clone))

    head_before = str(repo.head.commit)
    remote_before = remote_branch_commits(bare, branch)

    rewrote = trim_monitoring_history(repo, branch, retention_days=30)
    assert rewrote is False, "no commit older than cutoff -> must not rewrite"
    assert str(repo.head.commit) == head_before, "local branch must be unchanged"
    # No push attempted by the helper; remote is identical (same hashes).
    assert remote_branch_commits(bare, branch) == remote_before


# --------------------------------------------------------------------------------------
# Invariant 3 again via run_once: a trimming pass force-updates the remote to match local.
# --------------------------------------------------------------------------------------

def test_trim_force_updates_remote(env, tmp_path):
    dated = [("old1", 90), ("old2", 60), ("recent1", 3)]
    bare, clone, branch = _make_monitoring_clone_with_history(tmp_path, "deploy", dated)
    repo = Repo(str(clone))

    rewrote = trim_monitoring_history(repo, branch, retention_days=30)
    assert rewrote is True
    local_subjects = _subjects(clone)
    _force_push(clone, branch)

    # Remote ref now equals the rewritten local head, and the remote log matches exactly.
    assert remote_branch_commits(bare, branch)[0] == str(repo.head.commit)
    assert _subjects(bare) == local_subjects
    # Only one synthetic base remains; both old commits collapsed into it.
    assert sum(1 for s in local_subjects if s.startswith("📉 History trimmed:")) == 1


# --------------------------------------------------------------------------------------
# Edge: every commit older than cutoff -> keep latest state as the single base commit.
# --------------------------------------------------------------------------------------

def test_edge_all_old_keeps_latest_state(env, tmp_path):
    dated = [("a", 100), ("b", 80), ("LATEST", 50)]  # all older than 30 days
    bare, clone, branch = _make_monitoring_clone_with_history(tmp_path, "deploy", dated)
    repo = Repo(str(clone))

    before = _head_file(clone)
    assert before == b"LATEST"

    rewrote = trim_monitoring_history(repo, branch, retention_days=30)
    assert rewrote is True
    subjects = _subjects(clone)
    assert len(subjects) == 1, subjects
    assert subjects[0].startswith("📉 History trimmed:")
    # Latest state preserved as the synthetic base's content.
    assert _head_file(clone) == before


# --------------------------------------------------------------------------------------
# Edge: a single (old) commit -> collapses to one base whose content == that commit.
# --------------------------------------------------------------------------------------

def test_edge_single_commit(env, tmp_path):
    dated = [("only", 90)]
    bare, clone, branch = _make_monitoring_clone_with_history(tmp_path, "deploy", dated)
    repo = Repo(str(clone))

    rewrote = trim_monitoring_history(repo, branch, retention_days=30)
    assert rewrote is True
    subjects = _subjects(clone)
    assert len(subjects) == 1
    assert subjects[0].startswith("📉 History trimmed:")
    assert _head_file(clone) == b"only"


def test_edge_single_recent_commit_no_trim(env, tmp_path):
    dated = [("only", 1)]  # within window
    bare, clone, branch = _make_monitoring_clone_with_history(tmp_path, "deploy", dated)
    repo = Repo(str(clone))
    head_before = str(repo.head.commit)
    assert trim_monitoring_history(repo, branch, retention_days=30) is False
    assert str(repo.head.commit) == head_before


# --------------------------------------------------------------------------------------
# Edge: brand-new branch first push -- nothing to trim. Driven through run_once, where the very
# first pass CREATES the orphan branch and pushes; there is no old history, so no force-push.
# --------------------------------------------------------------------------------------

def test_edge_brand_new_branch_first_push(env, tmp_path):
    apps_meta = {}
    for name in ("app1", "app2"):
        url, commit = make_app_code_repo(tmp_path, name)
        apps_meta[name] = app_meta_entry(url, commit, tmp_path / "deployed" / name)
    deploy_url = make_deploy_repo(tmp_path, "deploy", apps_meta)
    applications = {name: f"{deploy_url}@main" for name in apps_meta}
    # Tiny retention so the trim logic is definitely exercised on the very first push.
    agent = build_agent(tmp_path, applications)
    agent.config["monitoring_history_retention_days"] = 0.0001

    bare = tmp_path / "remotes" / "deploy.git"
    assert remote_branch_commits(bare, "main-monitoring") == []  # branch doesn't exist yet
    agent.run_once()  # must not raise; first push of a brand-new branch

    # The branch now exists with a status commit, and the merged file has both apps.
    assert status_commits(bare) >= 1
    feedback = remote_branch_file(bare, "main-monitoring", "testsite.toml", tmp_path, "newbr")
    assert "app1" in feedback and "app2" in feedback


# --------------------------------------------------------------------------------------
# Invariant 5: continuity -- trim, then a NEXT normal run_once appends + pushes normally; the branch
# stays valid and tracks origin. We drive this through run_once with a tiny retention so the FIRST
# pass trims, then change an app and run again.
# --------------------------------------------------------------------------------------

def test_continuity_next_run_appends_normally(env, tmp_path):
    # Seed an old+recent monitoring history under the EXACT path run_once will use, so run_once's
    # flush_status reuses this clone and trims it on the next status update.
    url1, first1, second1 = make_app_code_repo_two_commits(tmp_path, "app1")
    cp1 = tmp_path / "deployed" / "app1"
    apps_meta = {"app1": app_meta_entry(url1, first1, cp1)}
    deploy_url = make_deploy_repo(tmp_path, "deploy", apps_meta)
    applications = {"app1": f"{deploy_url}@main"}

    bare = tmp_path / "remotes" / "deploy.git"

    # First run establishes the monitoring branch normally.
    agent = build_agent(tmp_path, applications)
    agent.run_once()

    # Backdate the existing status commit to look OLD, by rewriting the monitoring clone history with
    # one old commit, then change the app so the next run produces a new status (forcing a trim of the
    # now-old prior commit). Use the real on-disk clone path.
    mon = Path(shared_clone_path(deploy_url, "main") + "-monitoring")
    repo = Repo(str(mon))
    # Append a clearly-old commit and push it, so prior history contains an old commit.
    old_epoch = time.time() - 90 * DAY
    _commit_dated(mon, "marker_old.txt", "x", "status OLD MARKER", old_epoch)
    sp.run(["git", "push", "origin", "main-monitoring"], cwd=str(mon), check=True, capture_output=True)

    # Now change the app's desired hash so the next run_once produces a genuinely new status commit
    # AND trims the old marker (retention small enough that the 90-day-old marker is dropped).
    apps_meta["app1"]["code_commit_hash"] = second1
    rewrite_deploy_meta(tmp_path, "deploy", apps_meta)
    agent2 = build_agent(tmp_path, applications)
    agent2.config["monitoring_history_retention_days"] = 30
    agent2.run_once()

    # The old marker is gone, replaced by a synthetic base; a fresh status commit sits on top.
    subjects = _subjects(bare)
    assert any(s.startswith("📉 History trimmed:") for s in subjects), subjects
    assert "status OLD MARKER" not in subjects, subjects

    # Continuity: a THIRD normal run with another change appends+pushes normally (branch still tracks
    # origin, no corruption). Move app1 back to first1. rewrite_deploy_meta clones into a fixed
    # rewrite/<slug> dir, so clear the prior clone before reusing it.
    import shutil
    shutil.rmtree(tmp_path / "rewrite" / "deploy", ignore_errors=True)
    apps_meta["app1"]["code_commit_hash"] = first1
    rewrite_deploy_meta(tmp_path, "deploy", apps_meta)
    agent3 = build_agent(tmp_path, applications)
    agent3.config["monitoring_history_retention_days"] = 30
    before_count = len(remote_branch_commits(bare, "main-monitoring"))
    agent3.run_once()  # must not raise
    after_count = len(remote_branch_commits(bare, "main-monitoring"))
    # A normal append happened (recent commits are within window so no further trim).
    assert after_count == before_count + 1, (before_count, after_count)
    # Branch still resolves and the deployed app reflects the requested hash.
    assert str(Repo(str(cp1)).head.commit) == first1


# --------------------------------------------------------------------------------------
# Retention override via config respected: a 1000-day retention keeps everything (no trim) even
# though commits are ~60 days old; the same history WOULD trim at the 30-day default.
# --------------------------------------------------------------------------------------

def test_retention_override_via_config_respected(env, tmp_path):
    dated = [("old", 60), ("recent", 1)]
    bare, clone, branch = _make_monitoring_clone_with_history(tmp_path, "deploy", dated)
    repo = Repo(str(clone))

    # Huge retention -> nothing is "old" -> no rewrite.
    assert trim_monitoring_history(repo, branch, retention_days=1000) is False
    # Default-sized retention -> the 60-day commit IS old -> rewrite.
    assert trim_monitoring_history(repo, branch, retention_days=30) is True


def test_retention_override_loaded_from_disk_config(env, tmp_path):
    # Regression guard for the config seam: prove a monitoring_history_retention_days key written to
    # the actual config.toml the agent LOADS is honored (not just an in-memory dict mutation). We
    # build the agent normally (which writes config.toml + sets GITOPS_AGENT_CONFIG), then re-read,
    # inject the key on disk, and re-instantiate the agent from that same file.
    import os
    import toml as _toml

    from gitops_agent.agent import GitOpsAgent

    url1, first1, second1 = make_app_code_repo_two_commits(tmp_path, "app1")
    cp1 = tmp_path / "deployed" / "app1"
    apps_meta = {"app1": app_meta_entry(url1, first1, cp1)}
    deploy_url = make_deploy_repo(tmp_path, "deploy", apps_meta)
    applications = {"app1": f"{deploy_url}@main"}
    bare = tmp_path / "remotes" / "deploy.git"

    # Write a config.toml on disk that includes the override key, then load the agent FROM that file.
    cfg = {
        "applications": applications,
        "infra_name": "testsite",
        "interval": 300,
        "monitoring_history_retention_days": 0,  # any pre-existing commit counts as old
    }
    cfg_path = tmp_path / "config.toml"
    cfg_path.write_text(_toml.dumps(cfg))
    os.environ["GITOPS_AGENT_CONFIG"] = str(cfg_path)
    try:
        agent = GitOpsAgent(config_mode=False)
    finally:
        os.environ.pop("GITOPS_AGENT_CONFIG", None)

    # Confirm the key was actually loaded from disk (not defaulted).
    assert agent.config.get("monitoring_history_retention_days") == 0

    agent.run_once()  # first status commit
    apps_meta["app1"]["code_commit_hash"] = second1
    rewrite_deploy_meta(tmp_path, "deploy", apps_meta)
    agent.run_once()  # second status -> prior commit "old" under disk retention 0 -> trim

    subjects = _subjects(bare)
    assert any(s.startswith("📉 History trimmed:") for s in subjects), subjects


def test_retention_override_through_run_once(env, tmp_path):
    # End-to-end: a tiny config retention makes even a just-made commit "old" on the SECOND status,
    # so the second pass trims. Confirms self.config.get(...) wiring in flush_status.
    url1, first1, second1 = make_app_code_repo_two_commits(tmp_path, "app1")
    cp1 = tmp_path / "deployed" / "app1"
    apps_meta = {"app1": app_meta_entry(url1, first1, cp1)}
    deploy_url = make_deploy_repo(tmp_path, "deploy", apps_meta)
    applications = {"app1": f"{deploy_url}@main"}
    bare = tmp_path / "remotes" / "deploy.git"

    agent = build_agent(tmp_path, applications)
    # 0.0001 days ~= 8.6s; the first status commit will be older than that by the time the second
    # status is produced after we sleep briefly -- but to avoid sleeping, set retention to 0 so ANY
    # pre-existing commit counts as old.
    agent.config["monitoring_history_retention_days"] = 0
    agent.run_once()  # first status commit
    apps_meta["app1"]["code_commit_hash"] = second1
    rewrite_deploy_meta(tmp_path, "deploy", apps_meta)
    agent.run_once()  # second status -> prior commit(s) are "old" under retention 0 -> trim

    subjects = _subjects(bare)
    assert any(s.startswith("📉 History trimmed:") for s in subjects), subjects
