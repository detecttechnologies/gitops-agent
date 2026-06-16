"""Tests for resolve_config_file_pairs in gitops_agent.git_operations.

Pure path-resolution tests, no I/O. Run standalone with:

    python -m pytest tests/test_config_files.py -q
"""

import sys
from pathlib import Path

# Make the package importable when running the file standalone.
sys.path.insert(0, str(Path(__file__).resolve().parent.parent))

from gitops_agent.git_operations import resolve_config_file_pairs  # noqa: E402

REPO_ROOT = Path("/opt/gitops-agent/app-configs/my_app/")


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


if __name__ == "__main__":
    sys.exit(__import__("pytest").main([__file__, "-q"]))
