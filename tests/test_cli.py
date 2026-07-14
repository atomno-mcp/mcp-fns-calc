"""CLI-тесты: --help / --version / валидация флагов / дефолты (atomno-mcp-conventions)."""

from __future__ import annotations

import subprocess
import sys

import pytest


def _run(*args: str) -> subprocess.CompletedProcess[str]:
    return subprocess.run(
        [sys.executable, "-m", "mcp_fns_calc.server", *args],
        capture_output=True,
        text=True,
        timeout=30,
    )


class TestHelp:
    def test_help_exits_zero(self) -> None:
        r = _run("--help")
        assert r.returncode == 0
        assert "atomno-mcp-fns-calc" in r.stdout


class TestVersion:
    def test_version_exits_zero(self) -> None:
        r = _run("--version")
        assert r.returncode == 0
        assert "atomno-mcp-fns-calc" in r.stdout


class TestTransportValidation:
    def test_bad_transport_rejected(self) -> None:
        assert _run("--transport", "carrier-pigeon").returncode != 0


class TestLogLevelValidation:
    def test_bad_log_level_rejected(self) -> None:
        assert _run("--log-level", "LOUD").returncode != 0


class TestParserDefaults:
    def test_settings_defaults(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.delenv("MCP_FNS_CALC_TOKEN", raising=False)
        monkeypatch.delenv("MCP_FNS_CALC_API_BASE", raising=False)
        from mcp_fns_calc.config import DEFAULT_API_BASE, Settings

        s = Settings.from_env()
        assert s.api_base == DEFAULT_API_BASE
        assert s.token is None
        assert s.has_token is False


class TestInvalidEnvBailsOutCleanly:
    def test_bad_timeout_falls_back(self, monkeypatch: pytest.MonkeyPatch) -> None:
        monkeypatch.setenv("MCP_FNS_CALC_TIMEOUT", "not-a-number")
        from mcp_fns_calc.config import DEFAULT_TIMEOUT, Settings

        assert Settings.from_env().timeout == DEFAULT_TIMEOUT
