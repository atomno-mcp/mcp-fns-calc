"""Тесты hosted-клиента: happy-path, 4xx, 5xx, timeout (respx-моки httpx)."""

from __future__ import annotations

import httpx
import pytest
import respx

from mcp_fns_calc.client import FnsCalcClient
from mcp_fns_calc.config import Settings
from mcp_fns_calc.errors import BackendError, BackendUnavailable

BASE = "https://api.test.local/fns-calc"


def _client() -> FnsCalcClient:
    return FnsCalcClient(Settings(api_base=BASE, token="k", timeout=5.0))


@respx.mock
async def test_selfemployed_happy_path() -> None:
    respx.post(f"{BASE}/v1/status/selfemployed").mock(
        return_value=httpx.Response(200, json={"inn": "7712345678", "is_selfemployed": True})
    )
    c = _client()
    try:
        res = await c.check_selfemployed("7712345678")
        assert res["is_selfemployed"] is True
    finally:
        await c.aclose()


@respx.mock
async def test_401_raises_backend_error() -> None:
    respx.post(f"{BASE}/v1/status/ip").mock(
        return_value=httpx.Response(401, json={"message_ru": "нет ключа"})
    )
    c = _client()
    try:
        with pytest.raises(BackendError) as exc:
            await c.check_ip_status("771234567890")
        assert exc.value.status_code == 401
    finally:
        await c.aclose()


@respx.mock
async def test_5xx_raises_backend_error() -> None:
    respx.post(f"{BASE}/v1/status/arrears").mock(return_value=httpx.Response(503, text="down"))
    c = _client()
    try:
        with pytest.raises(BackendError) as exc:
            await c.check_tax_arrears("7712345678")
        assert exc.value.status_code == 503
    finally:
        await c.aclose()


@respx.mock
async def test_timeout_raises_unavailable() -> None:
    respx.post(f"{BASE}/v1/status/account-block").mock(side_effect=httpx.TimeoutException("t"))
    c = _client()
    try:
        with pytest.raises(BackendUnavailable):
            await c.check_account_block("7712345678")
    finally:
        await c.aclose()


@respx.mock
async def test_get_rates_get_request() -> None:
    respx.get(f"{BASE}/v1/rates").mock(
        return_value=httpx.Response(200, json={"cbr_key_rate": 21.0})
    )
    c = _client()
    try:
        res = await c.get_rates(["cbr_key_rate"])
        assert res["cbr_key_rate"] == 21.0
    finally:
        await c.aclose()
