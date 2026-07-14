"""FastMCP entrypoint для atomno-mcp-fns-calc (тонкий клиент).

Локальные тулзы (без токена, офлайн, детерминированно): calc_vat, calc_usn,
calc_insurance_ip, calc_ndfl, calc_patent, calc_penalty, get_rates (снапшот).
Hosted-тулзы (тариф Pro, ключ MCP_FNS_CALC_TOKEN): check_selfemployed,
check_ip_status, check_disqualified, check_account_block, check_tax_arrears и
свежий get_rates. Каждый ответ содержит disclaimer/source (см. spec, раздел 8).
"""

from __future__ import annotations

import argparse
import asyncio
import atexit
import logging
from typing import Annotated, Any

from fastmcp import FastMCP
from pydantic import Field

from . import __version__, calculators, rates
from .client import FnsCalcClient
from .config import Settings
from .errors import BackendError, FnsCalcError

logger = logging.getLogger("mcp_fns_calc")

mcp: FastMCP = FastMCP(
    name="atomno-mcp-fns-calc",
    instructions=(
        "Russian tax calculators + fiscal-status checks for accountants, sole "
        "proprietors (ИП) and small business. Calculators (VAT, USN, ИП insurance "
        "contributions, progressive NDFL 2025+, patent/ПСН, late-payment penalty) "
        "run locally and offline on a bundled rates snapshot — no key needed. "
        "Live status checks (self-employed/НПД, ИП/ОКВЭД, disqualified persons, "
        "account blocks, tax arrears) and always-fresh rates go through the Atomno "
        "hosted API and need a Pro key (MCP_FNS_CALC_TOKEN). Every answer "
        "carries a disclaimer and a source reference. Get a key at "
        "https://atomno-mcp.ru/pricing#fns-calc-pro."
    ),
)

_client: FnsCalcClient | None = None
_client_lock = asyncio.Lock()
_settings = Settings.from_env()


async def _get_client() -> FnsCalcClient:
    global _client
    if _client is not None:
        return _client
    async with _client_lock:
        if _client is None:
            _client = FnsCalcClient(_settings)
            atexit.register(_close_client_atexit)
    assert _client is not None
    return _client


def _close_client_atexit() -> None:
    if _client is None:
        return
    try:
        asyncio.run(_client.aclose())
    except RuntimeError:
        pass


def _no_token_hint() -> dict[str, Any]:
    return {
        "error": "missing_token",
        "message_ru": (
            "Не задан MCP_FNS_CALC_TOKEN. Live-проверки фискальных статусов — "
            "платные (тариф Pro). Калькуляторы работают и без ключа. "
            "Ключ: https://atomno-mcp.ru/pricing#fns-calc-pro"
        ),
        "disclaimer": rates.DISCLAIMER,
    }


async def _hosted_call(name: str, coro_factory) -> dict[str, Any]:
    if not _settings.has_token:
        return _no_token_hint()
    try:
        result = await coro_factory()
        result.setdefault("disclaimer", rates.DISCLAIMER)
        return result
    except BackendError as exc:
        if exc.status_code == 401:
            return _no_token_hint()
        logger.warning("%s backend %s: %s", name, exc.status_code, exc.detail)
        return {"error": "backend_error", "status": exc.status_code, "message": exc.detail}
    except FnsCalcError as exc:
        logger.warning("%s failed: %s", name, exc)
        return {"error": "unavailable", "message": str(exc)}


# ============================ КАЛЬКУЛЯТОРЫ (офлайн) ============================

@mcp.tool
async def calc_vat(
    amount: Annotated[float, Field(ge=0, description="Сумма (без НДС для add, с НДС для extract).")],
    rate: Annotated[float, Field(default=20, description="Ставка НДС: 20/10/0 или спец 5/7 (УСН с 2025).")] = 20,
    mode: Annotated[str, Field(default="add", description="add — начислить сверху; extract — выделить из суммы.", pattern="^(add|extract)$")] = "add",
) -> dict[str, Any]:
    """Расчёт НДС: начислить сверху или выделить из суммы (ст. 164, 168 НК РФ)."""
    return calculators.calc_vat(amount, rate, mode)


@mcp.tool
async def calc_usn(
    income: Annotated[float, Field(ge=0, description="Доход за период.")],
    obj: Annotated[str, Field(default="income", description="income (6%) или income_minus_expense (15%).", pattern="^(income|income_minus_expense)$")] = "income",
    expenses: Annotated[float, Field(default=0, ge=0, description="Расходы (для income_minus_expense).")] = 0,
    rate: Annotated[float | None, Field(default=None, description="Региональная ставка, если отличается от базовой.")] = None,
    contributions_paid: Annotated[float, Field(default=0, ge=0, description="Уплаченные страховые взносы (для объекта income).")] = 0,
    has_employees: Annotated[bool, Field(default=False, description="Есть работники (ограничивает вычет взносов 50%).")] = False,
    advances_paid: Annotated[float, Field(default=0, ge=0, description="Ранее уплаченные авансовые платежи.")] = 0,
) -> dict[str, Any]:
    """Расчёт УСН: доходы 6% или доходы-минус-расходы 15% с мин.налогом (гл. 26.2 НК РФ)."""
    return calculators.calc_usn(income, obj, expenses, rate, contributions_paid, has_employees, advances_paid)


@mcp.tool
async def calc_insurance_ip(
    income: Annotated[float, Field(ge=0, description="Годовой доход ИП.")],
    year: Annotated[int, Field(default=2025, ge=2020, le=2030, description="Год расчёта.")] = 2025,
    months: Annotated[int, Field(default=12, ge=1, le=12, description="Полных месяцев деятельности (неполный год).")] = 12,
) -> dict[str, Any]:
    """Страховые взносы ИП «за себя»: фикс. часть + 1% с дохода > 300 000 ₽ (ст. 430 НК РФ)."""
    return calculators.calc_insurance_ip(income, year, months)


@mcp.tool
async def calc_ndfl(
    income: Annotated[float, Field(ge=0, description="Годовой доход.")],
    deductions: Annotated[float, Field(default=0, ge=0, description="Вычеты.")] = 0,
    year: Annotated[int, Field(default=2025, ge=2025, le=2030, description="Год (прогрессивная шкала с 2025).")] = 2025,
) -> dict[str, Any]:
    """НДФЛ по прогрессивной шкале 2025+ (13/15/18/20/22%) с разбивкой по ступеням (ст. 224 НК РФ)."""
    return calculators.calc_ndfl(income, deductions, year)


@mcp.tool
async def calc_patent(
    potential_income: Annotated[float, Field(ge=0, description="Потенциально возможный годовой доход (закон региона).")],
    months: Annotated[int, Field(default=12, ge=1, le=12, description="Срок патента в месяцах.")] = 12,
    contributions_paid: Annotated[float, Field(default=0, ge=0, description="Уплаченные страховые взносы.")] = 0,
    has_employees: Annotated[bool, Field(default=False, description="Есть работники (вычет взносов макс. 50%).")] = False,
) -> dict[str, Any]:
    """Стоимость патента (ПСН): ПВД × 6% × месяцы/12 минус взносы (гл. 26.5 НК РФ)."""
    return calculators.calc_patent(potential_income, months, contributions_paid, has_employees)


@mcp.tool
async def calc_penalty(
    amount: Annotated[float, Field(ge=0, description="Сумма недоимки.")],
    days: Annotated[int, Field(ge=0, description="Дней просрочки.")],
    key_rate: Annotated[float | None, Field(default=None, description="Ключевая ставка ЦБ РФ, % годовых (если не задана — снапшот).")] = None,
    payer: Annotated[str, Field(default="ip", description="org (1/300 до 30 дн, далее 1/150); ip/individual (1/300).", pattern="^(org|ip|individual)$")] = "ip",
) -> dict[str, Any]:
    """Пени за просрочку уплаты налога по ключевой ставке ЦБ РФ (ст. 75 НК РФ)."""
    return calculators.calc_penalty(amount, days, key_rate, payer)


@mcp.tool
async def get_rates(
    fresh: Annotated[bool, Field(default=False, description="true — тянуть свежие ставки с hosted (нужен ключ Pro); false — офлайн-снапшот.")] = False,
    kinds: Annotated[list[str] | None, Field(default=None, description="Фильтр: cbr_key_rate | ip_fixed_contrib | usn_limits | ndfl_scale | deflator.")] = None,
) -> dict[str, Any]:
    """Актуальные ставки и лимиты: офлайн-снапшот или свежие с hosted (fresh=true, Pro)."""
    if fresh:
        return await _hosted_call("get_rates", lambda: _get_rates_fresh(kinds))
    return {
        "mode": "snapshot",
        "snapshot_date": rates.SNAPSHOT_DATE,
        "ip_fixed_contrib": {str(y): float(v["fixed"]) for y, v in rates.IP_CONTRIBUTIONS.items()},
        "ip_1pct_cap": {str(y): float(v["cap_1pct"]) for y, v in rates.IP_CONTRIBUTIONS.items()},
        "ndfl_scale_2025": [
            {"up_to": (float(u) if u is not None else None), "rate": float(r)} for u, r in rates.NDFL_SCALE_2025
        ],
        "usn_income_limit": {str(y): float(v) for y, v in rates.USN_INCOME_LIMIT.items()},
        "cbr_key_rate_default": float(rates.CBR_KEY_RATE_DEFAULT),
        "note_ru": "Снапшот на дату сборки. Для гарантированно свежих ставок — fresh=true (тариф Pro).",
        "disclaimer": rates.DISCLAIMER,
    }


async def _get_rates_fresh(kinds: list[str] | None) -> dict[str, Any]:
    client = await _get_client()
    return await client.get_rates(kinds)


# ============================ ПРОВЕРКИ СТАТУСОВ (hosted) ======================

@mcp.tool
async def check_selfemployed(
    inn: Annotated[str, Field(min_length=10, max_length=12, description="ИНН физлица (10 или 12 цифр).")],
) -> dict[str, Any]:
    """Статус самозанятого (НПД) по ИНН на дату запроса (сервис ФНС). Тариф Pro."""
    return await _hosted_call("check_selfemployed", lambda: _call(lambda c: c.check_selfemployed(inn)))


@mcp.tool
async def check_ip_status(
    inn: Annotated[str, Field(min_length=12, max_length=12, description="ИНН ИП (12 цифр).")],
) -> dict[str, Any]:
    """Статус ИП, ОКВЭД и даты из ЕГРИП по ИНН. Тариф Pro."""
    return await _hosted_call("check_ip_status", lambda: _call(lambda c: c.check_ip_status(inn)))


@mcp.tool
async def check_disqualified(
    inn: Annotated[str | None, Field(default=None, description="ИНН для поиска в реестре дисквалифицированных.")] = None,
    fio: Annotated[str | None, Field(default=None, description="ФИО для поиска (если нет ИНН).")] = None,
) -> dict[str, Any]:
    """Совпадение с реестром дисквалифицированных лиц ФНС. Нейтральный результат, требует ручной верификации. Тариф Pro."""
    if not inn and not fio:
        return {"error": "validation", "message_ru": "Нужно указать inn или fio."}
    return await _hosted_call("check_disqualified", lambda: _call(lambda c: c.check_disqualified(inn, fio)))


@mcp.tool
async def check_account_block(
    inn: Annotated[str, Field(min_length=10, max_length=12, description="ИНН организации/ИП.")],
) -> dict[str, Any]:
    """Сведения о приостановлении операций по счетам (сервис ФНС). Тариф Pro."""
    return await _hosted_call("check_account_block", lambda: _call(lambda c: c.check_account_block(inn)))


@mcp.tool
async def check_tax_arrears(
    inn: Annotated[str, Field(min_length=10, max_length=12, description="ИНН организации/ИП.")],
) -> dict[str, Any]:
    """Наличие недоимок/задолженности из открытых данных «Прозрачный бизнес» ФНС. Тариф Pro."""
    return await _hosted_call("check_tax_arrears", lambda: _call(lambda c: c.check_tax_arrears(inn)))


async def _call(fn) -> dict[str, Any]:
    client = await _get_client()
    return await fn(client)


# ============================ CLI ============================================

def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        prog="atomno-mcp-fns-calc",
        description="MCP server: Russian tax calculators (offline) + fiscal-status checks (hosted).",
    )
    parser.add_argument("--version", "-V", action="version", version=f"atomno-mcp-fns-calc {__version__}")
    parser.add_argument(
        "--transport",
        choices=["stdio", "http", "sse", "streamable-http"],
        default="stdio",
        help="MCP transport (default: stdio).",
    )
    parser.add_argument("--host", default="127.0.0.1", help="Host for http transports (default: 127.0.0.1).")
    parser.add_argument("--port", type=int, default=8000, help="Port for http transports (default: 8000).")
    parser.add_argument(
        "--log-level",
        default="WARNING",
        choices=["DEBUG", "INFO", "WARNING", "ERROR", "CRITICAL"],
        help="Logging level (default: WARNING).",
    )
    args = parser.parse_args(argv)
    logging.basicConfig(level=getattr(logging, args.log_level))
    if args.transport in ("http", "sse", "streamable-http"):
        mcp.run(transport=args.transport, host=args.host, port=args.port)
    else:
        mcp.run(transport=args.transport)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
