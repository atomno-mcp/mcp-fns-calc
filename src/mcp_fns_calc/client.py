"""HTTP-клиент к hosted-бэкенду fns-calc (api.atomno-mcp.ru/fns-calc).

Тонкая обёртка над httpx: один общий AsyncClient, заголовок X-API-Key, маппинг
ошибок в FnsCalcError. Никакой бизнес-логики — только транспорт для live-проверок
фискальных статусов и свежих справочников. Проверки проксируются на лету;
ПДн третьих лиц на нашей стороне не персистятся.
"""

from __future__ import annotations

from typing import Any

import httpx

from . import __version__
from .config import Settings
from .errors import BackendError, BackendUnavailable

_USER_AGENT = f"atomno-mcp-fns-calc/{__version__}"


class FnsCalcClient:
    def __init__(self, settings: Settings) -> None:
        self._settings = settings
        headers = {"User-Agent": _USER_AGENT, "Accept": "application/json"}
        if settings.token:
            headers["X-API-Key"] = settings.token
        self._client = httpx.AsyncClient(
            base_url=settings.api_base,
            timeout=settings.timeout,
            headers=headers,
        )

    async def aclose(self) -> None:
        await self._client.aclose()

    async def _post(self, path: str, payload: dict[str, Any]) -> dict[str, Any]:
        try:
            resp = await self._client.post(path, json=payload)
        except httpx.TimeoutException as exc:
            raise BackendUnavailable(f"timeout calling {path}") from exc
        except httpx.HTTPError as exc:
            raise BackendUnavailable(f"network error calling {path}: {exc}") from exc
        return self._parse(resp)

    async def _get(self, path: str, params: dict[str, Any] | None = None) -> dict[str, Any]:
        try:
            resp = await self._client.get(path, params=params or {})
        except httpx.TimeoutException as exc:
            raise BackendUnavailable(f"timeout calling {path}") from exc
        except httpx.HTTPError as exc:
            raise BackendUnavailable(f"network error calling {path}: {exc}") from exc
        return self._parse(resp)

    @staticmethod
    def _parse(resp: httpx.Response) -> dict[str, Any]:
        if resp.status_code >= 400:
            raise BackendError(resp.status_code, _extract_detail(resp))
        try:
            return resp.json()
        except ValueError as exc:
            raise BackendError(resp.status_code, "invalid JSON in response") from exc

    # --- live-проверки статусов (проксируются на лету) ---
    async def check_selfemployed(self, inn: str) -> dict[str, Any]:
        return await self._post("/v1/status/selfemployed", {"inn": inn})

    async def check_ip_status(self, inn: str) -> dict[str, Any]:
        return await self._post("/v1/status/ip", {"inn": inn})

    async def check_disqualified(self, inn: str | None, fio: str | None) -> dict[str, Any]:
        return await self._post("/v1/status/disqualified", {"inn": inn, "fio": fio})

    async def check_account_block(self, inn: str) -> dict[str, Any]:
        return await self._post("/v1/status/account-block", {"inn": inn})

    async def check_tax_arrears(self, inn: str) -> dict[str, Any]:
        return await self._post("/v1/status/arrears", {"inn": inn})

    # --- свежие справочники ---
    async def get_rates(self, kinds: list[str] | None = None) -> dict[str, Any]:
        return await self._get("/v1/rates", {"kinds": ",".join(kinds)} if kinds else None)


def _extract_detail(resp: httpx.Response) -> str:
    try:
        body = resp.json()
    except ValueError:
        return resp.text[:300] or resp.reason_phrase
    if isinstance(body, dict):
        for key in ("message_ru", "detail", "message", "error"):
            if body.get(key):
                return str(body[key])
    return str(body)[:300]
