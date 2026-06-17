"""Shared pytest fixtures for the integration tests.

Currently provides the `env` fixture, which points GITOPS_AGENT_HOME (and the resolved
git_operations.APP_CONFIGS_DIR) at a per-test tmp dir so the agent never touches /opt or the real
filesystem. test_integration_monitoring.py and test_integration_dedup.py also define their own local
`env` -- a local fixture overrides the conftest one (pytest picks the closest definition), so this
file is non-breaking; it simply lets test files that don't redefine it (e.g. test_integration_health.py)
discover the fixture by name without importing it (importing a fixture trips Ruff's F811/F401).
"""

import pytest

from gitops_agent import git_operations as gops


@pytest.fixture
def env(tmp_path, monkeypatch):
    home = tmp_path / "gitops-home"
    app_configs = home / "app-configs"
    app_configs.mkdir(parents=True, exist_ok=True)
    monkeypatch.setenv("GITOPS_AGENT_HOME", str(home))
    monkeypatch.setattr(gops, "APP_CONFIGS_DIR", app_configs)
    return {"home": home, "app_configs": app_configs, "tmp": tmp_path}
