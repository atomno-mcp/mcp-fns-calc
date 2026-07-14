"""Конфигурация тонкого клиента из переменных окружения.

Калькуляторы работают без токена. Проверки статусов и свежий get_rates идут
через hosted-бэкенд и требуют ключ (тариф Pro).

    MCP_FNS_CALC_API_BASE — базовый URL hosted-бэкенда (default: публичный прод).
    MCP_FNS_CALC_TOKEN    — API-ключ (заголовок X-API-Key). Без него live-проверки → 401.
    MCP_FNS_CALC_TIMEOUT  — таймаут HTTP в секундах (default 30).
"""

from __future__ import annotations

import os
from dataclasses import dataclass

DEFAULT_API_BASE = "https://api.atomno-mcp.ru/fns-calc"
DEFAULT_TIMEOUT = 30.0


@dataclass(frozen=True)
class Settings:
    api_base: str
    token: str | None
    timeout: float

    @classmethod
    def from_env(cls) -> Settings:
        base = (os.environ.get("MCP_FNS_CALC_API_BASE") or DEFAULT_API_BASE).rstrip("/")
        token = os.environ.get("MCP_FNS_CALC_TOKEN") or None
        try:
            timeout = float(os.environ.get("MCP_FNS_CALC_TIMEOUT") or DEFAULT_TIMEOUT)
        except ValueError:
            timeout = DEFAULT_TIMEOUT
        return cls(api_base=base, token=token, timeout=timeout)

    @property
    def has_token(self) -> bool:
        return bool(self.token)
