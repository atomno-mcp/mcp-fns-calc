"""Smoke-тесты MCP-тулзов server.py (hosted + офлайн ветки)."""

from __future__ import annotations

import pytest
import respx
from httpx import Response

from mcp_fns_calc.config import Settings
from mcp_fns_calc.server import (
    calc_vat,
    check_disqualified,
    check_selfemployed,
    get_rates,
)


@pytest.mark.asyncio
async def test_get_rates_snapshot_offline() -> None:
    result = await get_rates(fresh=False)
    assert result["mode"] == "snapshot"
    assert "disclaimer" in result
    assert "cbr_key_rate_default" in result


@pytest.mark.asyncio
async def test_get_rates_fresh_without_token(monkeypatch: pytest.MonkeyPatch) -> None:
    import mcp_fns_calc.server as srv

    monkeypatch.setattr(
        srv,
        "_settings",
        Settings(api_base="https://api.example.test/fns-calc", token=None, timeout=5.0),
    )
    result = await get_rates(fresh=True)
    assert result["error"] == "missing_token"


@pytest.mark.asyncio
async def test_check_disqualified_requires_identifier() -> None:
    result = await check_disqualified()
    assert result["error"] == "validation"


@pytest.mark.asyncio
async def test_check_selfemployed_no_token(monkeypatch: pytest.MonkeyPatch) -> None:
    import mcp_fns_calc.server as srv

    monkeypatch.setattr(
        srv,
        "_settings",
        Settings(api_base="https://api.example.test/fns-calc", token=None, timeout=5.0),
    )
    result = await check_selfemployed(inn="525741209968")
    assert result["error"] == "missing_token"


@pytest.mark.asyncio
async def test_calc_vat_add_mode() -> None:
    result = await calc_vat(amount=1000.0, rate=20, mode="add")
    assert result["vat"] == 200.0


@pytest.mark.asyncio
@respx.mock
async def test_check_selfemployed_backend_401(monkeypatch: pytest.MonkeyPatch) -> None:
    import mcp_fns_calc.server as srv

    base = "https://api.example.test/fns-calc"
    monkeypatch.setattr(
        srv,
        "_settings",
        Settings(api_base=base, token="bad-key", timeout=5.0),
    )
    respx.post(f"{base}/v1/status/selfemployed").mock(return_value=Response(401, json={"detail": "unauthorized"}))

    result = await check_selfemployed(inn="525741209968")
    assert result["error"] == "missing_token"
