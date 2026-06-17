"""Tests for the dedup refactor: repo_slug parsing, app-grouping, and the origin-match guard.

The pure-helper tests touch no git/fs. The is_repo_with_origin tests build real local git
repos under tmp_path (no /opt, no network, no root) because that guard is what gates update_git_repo:
it refuses to fetch/reset an existing clone whose origin doesn't match the expected deploy-config
URL -- it is the single most safety-critical helper.
"""

from git import Repo

import pytest

from gitops_agent.agent import group_apps_by_repo, parse_config, shared_clone_path
from gitops_agent.git_operations import is_repo_with_origin, normalize_url, repo_slug, url_hash


def _make_repo(path, origin_url):
    """Create a real git repo at path with origin set to origin_url."""
    path.mkdir(parents=True, exist_ok=True)
    repo = Repo.init(str(path))
    repo.create_remote("origin", origin_url)
    return repo


@pytest.mark.parametrize(
    ("git_url", "expected"),
    [
        # ssh scp-style with nested namespaces and trailing .git
        ("git@gitlab.com:Org/Sub/tricon-2025-12.git", "tricon-2025-12"),
        ("git@github.com:username/repo1_config.git", "repo1_config"),
        # ssh without trailing .git
        ("git@github.com:username/repo1_config", "repo1_config"),
        # https with trailing .git
        ("https://github.com/username/repo1_config.git", "repo1_config"),
        # https nested namespaces, no .git
        ("https://gitlab.com/Org/Sub/another-repo", "another-repo"),
        # ssh:// scheme url
        ("ssh://git@gitlab.com:22/Org/Sub/myrepo.git", "myrepo"),
        # trailing slash
        ("https://github.com/username/repo1_config/", "repo1_config"),
        # dots in the repo name itself (only the trailing .git is stripped)
        ("git@github.com:org/some.repo.name.git", "some.repo.name"),
    ],
)
def test_repo_slug(git_url, expected):
    assert repo_slug(git_url) == expected


def test_repo_slug_strips_only_trailing_git():
    # ".git" appearing mid-name must not be stripped
    assert repo_slug("git@host:org/git-tools.git") == "git-tools"


def test_parse_config_default_branch():
    url, branch = parse_config("git@github.com:username/repo.git")
    assert url == "git@github.com:username/repo.git"
    assert branch == "main"


def test_parse_config_explicit_branch():
    url, branch = parse_config("git@github.com:username/repo.git@prod")
    assert url == "git@github.com:username/repo.git"
    assert branch == "prod"


def test_parse_config_https_with_branch_regression():
    # Regression: an https url whose path ends in ".git@branch" must NOT have its branch swallowed
    # by a blanket replace("git@", "") -- the branch is "prod", and ".git" is preserved on the url.
    url, branch = parse_config("https://github.com/u/repo.git@prod")
    assert url == "https://github.com/u/repo.git"
    assert branch == "prod"


def test_parse_config_file_url_with_branch():
    url, branch = parse_config("file:///tmp/x/deploy.git@branchA")
    assert url == "file:///tmp/x/deploy.git"
    assert branch == "branchA"


def test_parse_config_scp_default_branch_no_at():
    # scp-style url with no branch and userinfo "git@" must default to main, url untouched
    url, branch = parse_config("git@gitlab.com:Org/repo")
    assert url == "git@gitlab.com:Org/repo"
    assert branch == "main"


def test_group_apps_by_repo_https_branch_not_collapsed_to_main():
    # Two https apps on distinct branches must stay distinct (depends on parse_config fix)
    apps = {
        "app1": "https://github.com/u/repo.git@prod",
        "app2": "https://github.com/u/repo.git@staging",
    }
    grouped = group_apps_by_repo(apps)
    assert len(grouped) == 2
    assert grouped[("https://github.com/u/repo.git", "prod")] == ["app1"]
    assert grouped[("https://github.com/u/repo.git", "staging")] == ["app2"]


def test_shared_clone_path_includes_slug_and_branch():
    url = "git@gitlab.com:Org/Sub/tricon-2025-12.git"
    path = shared_clone_path(url, "prod")
    expected_hash = url_hash(url)
    assert path == f"/opt/gitops-agent/app-configs/tricon-2025-12@prod-{expected_hash}"
    # The human-readable slug and branch remain a legible prefix
    assert path.startswith("/opt/gitops-agent/app-configs/tricon-2025-12@prod-")


def test_shared_clone_path_disambiguates_same_basename_distinct_repos():
    # Regression: two DISTINCT repos sharing a basename + branch must NOT collapse onto one dir
    org_a = shared_clone_path("git@gitlab.com:OrgA/deploy.git", "prod")
    org_b = shared_clone_path("git@gitlab.com:OrgB/deploy.git", "prod")
    assert org_a != org_b
    # Both still carry the legible "deploy@prod" prefix, differing only in the hash suffix
    assert org_a.startswith("/opt/gitops-agent/app-configs/deploy@prod-")
    assert org_b.startswith("/opt/gitops-agent/app-configs/deploy@prod-")


def test_shared_clone_path_stable_and_branch_sensitive():
    url = "git@gitlab.com:Org/deploy.git"
    # Same url+branch is deterministic
    assert shared_clone_path(url, "prod") == shared_clone_path(url, "prod")
    # Different branch -> different path
    assert shared_clone_path(url, "prod") != shared_clone_path(url, "staging")


def test_url_hash_ignores_trailing_git_and_slash():
    # Equivalent url spellings hash identically (so the clone path is stable across them)
    assert url_hash("git@gitlab.com:Org/deploy.git") == url_hash("git@gitlab.com:Org/deploy")
    assert url_hash("https://github.com/u/r/") == url_hash("https://github.com/u/r")


def test_url_hash_distinguishes_distinct_repos():
    assert url_hash("git@gitlab.com:OrgA/deploy.git") != url_hash("git@gitlab.com:OrgB/deploy.git")


def test_normalize_url():
    assert normalize_url("  git@gitlab.com:Org/deploy.git  ") == "git@gitlab.com:Org/deploy"
    assert normalize_url("https://github.com/u/r/") == "https://github.com/u/r"


def test_normalize_url_strips_scheme_credentials():
    # Embedded credentials in scheme urls are dropped so token/no-token spellings compare equal
    assert normalize_url("https://user:token@github.com/u/r.git") == "https://github.com/u/r"
    assert normalize_url("https://user:token@github.com/u/r") == normalize_url("https://github.com/u/r")


def test_normalize_url_leaves_scp_style_untouched():
    # The "git@" in scp-style git@host:path is NOT credentials and must be preserved
    assert normalize_url("git@github.com:u/r.git") == "git@github.com:u/r"


def test_group_apps_by_repo_collapses_shared_repo():
    # 4 apps pointing at the SAME repo+branch should collapse into a single group
    apps = {
        "app1": "git@gitlab.com:Org/Sub/deploy-cfg.git@prod",
        "app2": "git@gitlab.com:Org/Sub/deploy-cfg.git@prod",
        "app3": "git@gitlab.com:Org/Sub/deploy-cfg.git@prod",
        "app4": "git@gitlab.com:Org/Sub/deploy-cfg.git@prod",
    }
    grouped = group_apps_by_repo(apps)
    assert len(grouped) == 1
    (url, branch), names = next(iter(grouped.items()))
    assert url == "git@gitlab.com:Org/Sub/deploy-cfg.git"
    assert branch == "prod"
    assert names == ["app1", "app2", "app3", "app4"]


def test_group_apps_by_repo_splits_on_branch():
    # Same repo, different branches -> separate groups
    apps = {
        "app1": "git@gitlab.com:Org/deploy-cfg.git@prod",
        "app2": "git@gitlab.com:Org/deploy-cfg.git@staging",
    }
    grouped = group_apps_by_repo(apps)
    assert len(grouped) == 2
    assert grouped[("git@gitlab.com:Org/deploy-cfg.git", "prod")] == ["app1"]
    assert grouped[("git@gitlab.com:Org/deploy-cfg.git", "staging")] == ["app2"]


def test_group_apps_by_repo_splits_on_repo():
    # Different repos -> separate groups
    apps = {
        "app1": "git@gitlab.com:Org/repo-a.git@main",
        "app2": "git@gitlab.com:Org/repo-b.git@main",
    }
    grouped = group_apps_by_repo(apps)
    assert len(grouped) == 2


def test_group_apps_by_repo_default_branch_groups_together():
    # One entry with explicit @main, one without -> both default to main, so same group
    apps = {
        "app1": "git@gitlab.com:Org/repo.git@main",
        "app2": "git@gitlab.com:Org/repo.git",
    }
    grouped = group_apps_by_repo(apps)
    assert len(grouped) == 1
    assert grouped[("git@gitlab.com:Org/repo.git", "main")] == ["app1", "app2"]


def test_group_apps_by_repo_preserves_first_appearance_order():
    apps = {
        "z_app": "git@h:o/r1.git@main",
        "a_app": "git@h:o/r2.git@main",
        "m_app": "git@h:o/r1.git@main",
    }
    grouped = group_apps_by_repo(apps)
    keys = list(grouped.keys())
    assert keys == [("git@h:o/r1.git", "main"), ("git@h:o/r2.git", "main")]
    assert grouped[("git@h:o/r1.git", "main")] == ["z_app", "m_app"]


# --- is_repo_with_origin: the origin-guard that gates update_git_repo's fetch/reset ---


def test_is_repo_with_origin_matches_exact_url(tmp_path):
    repo_dir = tmp_path / "deploy"
    _make_repo(repo_dir, "git@gitlab.com:Org/deploy.git")
    assert is_repo_with_origin(repo_dir, "git@gitlab.com:Org/deploy.git") is True


def test_is_repo_with_origin_matches_ignoring_trailing_git(tmp_path):
    repo_dir = tmp_path / "deploy"
    _make_repo(repo_dir, "git@gitlab.com:Org/deploy.git")
    # The expected url may be spelled without .git; the guard must still match
    assert is_repo_with_origin(repo_dir, "git@gitlab.com:Org/deploy") is True


def test_is_repo_with_origin_rejects_different_namespace_same_basename(tmp_path):
    # The collision case the whole design exists to prevent: same basename, different org
    repo_dir = tmp_path / "deploy"
    _make_repo(repo_dir, "git@gitlab.com:OrgA/deploy.git")
    assert is_repo_with_origin(repo_dir, "git@gitlab.com:OrgB/deploy.git") is False


def test_is_repo_with_origin_rejects_when_no_origin_remote(tmp_path):
    repo_dir = tmp_path / "no_origin"
    repo_dir.mkdir()
    Repo.init(str(repo_dir))  # a git repo, but with no "origin" remote
    assert is_repo_with_origin(repo_dir, "git@gitlab.com:Org/deploy.git") is False


def test_is_repo_with_origin_rejects_non_git_directory(tmp_path):
    # A plain non-git directory must never be considered a match -> update_git_repo won't reset it
    plain = tmp_path / "just_a_dir"
    plain.mkdir()
    (plain / "some_file.txt").write_text("not a repo")
    assert is_repo_with_origin(plain, "git@gitlab.com:Org/deploy.git") is False


def test_is_repo_with_origin_ignores_embedded_credentials(tmp_path):
    # Same https repo cloned with a token vs without must still be recognised as the same repo
    repo_dir = tmp_path / "deploy"
    _make_repo(repo_dir, "https://user:tok@gitlab.com/Org/deploy.git")
    assert is_repo_with_origin(repo_dir, "https://gitlab.com/Org/deploy.git") is True
