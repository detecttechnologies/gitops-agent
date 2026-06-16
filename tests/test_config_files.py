"""Tests for resolve_config_file_pairs in gitops_agent.git_operations.

Pure path-resolution tests, no I/O. Run standalone with:

    python -m pytest tests/test_config_files.py -q
"""

import sys
from pathlib import Path

# Make the package importable when running the file standalone.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gitops_agent.git_operations import resolve_config_file_pairs  # noqa: E402
from gitops_agent.agent import compare_file_contents  # noqa: E402

REPO_ROOT = Path("/opt/gitops-agent/app-configs/my_app/")


def _config_drift(pairs):
    """Replicate the drift predicate used in GitOpsAgent.pull_dep_cfg."""
    return any(
        not compare_file_contents(pair["dst_abs"], pair["src_abs"])
        for pair in pairs
        if pair["src_abs"].exists()
    )


def test_new_array_form():
    app_meta = {
        "config_files": [
            {"src": "Tricon-01/configs/dt-iva-4.toml", "dst": "/opt/app/config.toml"},
            {"src": "Tricon-01/secrets.env", "dst": "/opt/app/.env"},
        ]
    }
    pairs = resolve_config_file_pairs(app_meta, REPO_ROOT)
    assert pairs == [
        {
            "src_abs": REPO_ROOT / "Tricon-01/configs/dt-iva-4.toml",
            "dst_abs": Path("/opt/app/config.toml"),
        },
        {
            "src_abs": REPO_ROOT / "Tricon-01/secrets.env",
            "dst_abs": Path("/opt/app/.env"),
        },
    ]


def test_legacy_single_pair_form():
    app_meta = {
        "config_src_path_rel_in_this_repo": "infra_name/config.toml",
        "config_dst_path_abs": "/opt/app/config.toml",
    }
    pairs = resolve_config_file_pairs(app_meta, REPO_ROOT)
    assert pairs == [
        {
            "src_abs": REPO_ROOT / "infra_name/config.toml",
            "dst_abs": Path("/opt/app/config.toml"),
        }
    ]


def test_both_present_new_first_legacy_appended():
    app_meta = {
        "config_files": [
            {"src": "Tricon-01/a.toml", "dst": "/opt/app/a.toml"},
        ],
        "config_src_path_rel_in_this_repo": "legacy/old.toml",
        "config_dst_path_abs": "/opt/app/old.toml",
    }
    pairs = resolve_config_file_pairs(app_meta, REPO_ROOT)
    assert pairs == [
        {
            "src_abs": REPO_ROOT / "Tricon-01/a.toml",
            "dst_abs": Path("/opt/app/a.toml"),
        },
        {
            "src_abs": REPO_ROOT / "legacy/old.toml",
            "dst_abs": Path("/opt/app/old.toml"),
        },
    ]


def test_neither_present_returns_empty_list():
    app_meta = {
        "code_url": "git@github.com:org/my-app.git",
        "code_commit_hash": "abc123",
        "code_local_path": "/opt/app",
    }
    assert resolve_config_file_pairs(app_meta, REPO_ROOT) == []


def test_relative_src_resolves_to_absolute_under_repo_root():
    app_meta = {"config_files": [{"src": "sub/dir/file.toml", "dst": "/abs/dst.toml"}]}
    pairs = resolve_config_file_pairs(app_meta, REPO_ROOT)
    src_abs = pairs[0]["src_abs"]
    assert src_abs.is_absolute()
    assert src_abs == REPO_ROOT / "sub/dir/file.toml"
    assert pairs[0]["dst_abs"].is_absolute()


def test_repo_root_accepts_str():
    app_meta = {"config_files": [{"src": "a.toml", "dst": "/opt/a.toml"}]}
    pairs = resolve_config_file_pairs(app_meta, "/opt/gitops-agent/app-configs/my_app/")
    assert pairs[0]["src_abs"] == Path("/opt/gitops-agent/app-configs/my_app/a.toml")


def test_drift_ignores_pairs_with_missing_source(tmp_path):
    # A pair whose src does not exist is skipped at copy time, so it must NOT be
    # reported as drift (otherwise the app would be flagged for update forever).
    missing_src = tmp_path / "does_not_exist.toml"
    some_dst = tmp_path / "dst.toml"
    some_dst.write_text("anything")
    pairs = [{"src_abs": missing_src, "dst_abs": some_dst}]
    assert _config_drift(pairs) is False


def test_drift_true_when_existing_source_differs_from_dst(tmp_path):
    src = tmp_path / "src.toml"
    dst = tmp_path / "dst.toml"
    src.write_text("a = 1")
    dst.write_text("a = 2")
    pairs = [{"src_abs": src, "dst_abs": dst}]
    assert _config_drift(pairs) is True


def test_drift_false_when_all_existing_sources_match(tmp_path):
    src = tmp_path / "src.toml"
    dst = tmp_path / "dst.toml"
    src.write_text("a = 1")
    dst.write_text("a = 1")
    pairs = [{"src_abs": src, "dst_abs": dst}]
    assert _config_drift(pairs) is False


def test_drift_true_when_any_one_of_many_differs(tmp_path):
    src1, dst1 = tmp_path / "s1", tmp_path / "d1"
    src2, dst2 = tmp_path / "s2", tmp_path / "d2"
    for p in (src1, dst1, src2):
        p.write_text("same")
    dst2.write_text("different")
    pairs = [
        {"src_abs": src1, "dst_abs": dst1},
        {"src_abs": src2, "dst_abs": dst2},
    ]
    assert _config_drift(pairs) is True


def test_drift_false_for_empty_pairs():
    assert _config_drift([]) is False


if __name__ == "__main__":
    sys.exit(__import__("pytest").main([__file__, "-q"]))
