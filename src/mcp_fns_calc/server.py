"""FastMCP entrypoint для atomno-mcp-fns-calc (тонкий клиент).

Локальные тулзы (без токена, офлайн, детерминированно): calc_vat, calc_usn,
calc_insurance_ip, calc_ndfl, calc_patent, calc_penalty, get_rates (снапшот).
Hosted-тулзы (тариф Pro, ключ MCP_FNS_CALC_TOKEN): check_selfemployed,
check_ip_status, check_disqualified, check_account_block, check_tax_arrears и
свежий get_rates. Каждый ответ содержит disclaimer и ссылку на первоисточник.
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
    """Compute Russian VAT (НДС): add on top of net or extract from gross (NK RF art. 164, 168).

    **When to use**
    - Invoice line: net→gross (mode=add) or gross→net+VAT (mode=extract).
    - USN taxpayers on special VAT rates 5% or 7% (from 2025 rules).
    - Quick sanity check before bookkeeping export.

    **When NOT to use**
    - VAT declaration filing, multi-rate invoices, or export/zero-rate cases.
    - Mixed taxable/exempt lines — sum per line in accounting software.

    **Parameters**
    - amount: base sum in RUB (net for add, gross for extract).
    - rate: 20, 10, 0, 5, or 7 (percent).
    - mode: `add` | `extract`.

    **Returns**: {net, vat, gross, rate, mode, formula, article, disclaimer, snapshot_date}.

    **Limitations**: read-only, offline, single-rate line; no side effects.
    **Example**: amount=100000, rate=20, mode=add → vat=20000, gross=120000.
    """
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
    """Calculate simplified tax (USN/УСН): 6% on income or 15% income-minus-expense with minimum tax (NK RF ch. 26.2).

    **When to use**
    - Estimate USN tax for a quarter/year before payment.
    - Object `income` (6%): deduct paid IP contributions (capped at 50% if has_employees).
    - Object `income_minus_expense` (15%): apply minimum tax 1% of income if 15% base is lower.

    **When NOT to use**
    - Exact advance-payment calendar or KKT cash reporting.
    - Taxpayers above USN income limits — check get_rates for limits first.

    **Parameters**: income, obj, expenses, rate (regional override), contributions_paid, has_employees, advances_paid.

    **Returns**: {tax, base, rate, min_tax, contributions_deduction, advances, formula, article, disclaimer}.

    **Limitations**: read-only, offline; regional rates optional; subtracts advances_paid from result.
    """
    return calculators.calc_usn(income, obj, expenses, rate, contributions_paid, has_employees, advances_paid)


@mcp.tool
async def calc_insurance_ip(
    income: Annotated[float, Field(ge=0, description="Годовой доход ИП.")],
    year: Annotated[int, Field(default=2025, ge=2020, le=2030, description="Год расчёта.")] = 2025,
    months: Annotated[int, Field(default=12, ge=1, le=12, description="Полных месяцев деятельности (неполный год).")] = 12,
) -> dict[str, Any]:
    """IP fixed insurance contributions + 1% on income above 300k RUB (NK RF art. 430).

    When to use: annual IP self-contribution estimate; set months<12 for partial year.
    When NOT to use: employee payroll — use payroll tools; amounts for closed years without snapshot check.
    Side effects: read-only, deterministic, offline; rates from bundled snapshot.

    Returns: {fixed, extra_1pct, total, income, year, months, formula, article, disclaimer}.
    """
    return calculators.calc_insurance_ip(income, year, months)


@mcp.tool
async def calc_ndfl(
    income: Annotated[float, Field(ge=0, description="Годовой доход.")],
    deductions: Annotated[float, Field(default=0, ge=0, description="Вычеты.")] = 0,
    year: Annotated[int, Field(default=2025, ge=2025, le=2030, description="Год (прогрессивная шкала с 2025).")] = 2025,
) -> dict[str, Any]:
    """Progressive personal income tax (NDFL/НДФЛ) 2025+ with per-bracket breakdown (NK RF art. 224).

    When to use: estimate annual NDFL on salary or IP income after deductions.
    When NOT to use: monthly withholding by employer — use payroll; non-resident rates not modeled.
    Side effects: read-only, deterministic, offline; no auth or network.

    Returns: {tax_total, taxable_base, brackets[], formula, article, disclaimer, snapshot_date}.
    """
    return calculators.calc_ndfl(income, deductions, year)


@mcp.tool
async def calc_patent(
    potential_income: Annotated[float, Field(ge=0, description="Потенциально возможный годовой доход (закон региона).")],
    months: Annotated[int, Field(default=12, ge=1, le=12, description="Срок патента в месяцах.")] = 12,
    contributions_paid: Annotated[float, Field(default=0, ge=0, description="Уплаченные страховые взносы.")] = 0,
    has_employees: Annotated[bool, Field(default=False, description="Есть работники (вычет взносов макс. 50%).")] = False,
) -> dict[str, Any]:
    """Patent taxation (PSN/ПСН) cost: potential income × 6% × months/12 minus contributions (NK RF ch. 26.5).

    When to use: compare patent cost vs USN for an activity; potential_income from regional law.
    When NOT to use: eligibility/OKVED limits — verify with FNS; employee-heavy cases need manual review.
    Side effects: read-only, deterministic, offline; no auth or network.

    Returns: {patent_cost, before_deduction, contributions_deduction, months, formula, article, disclaimer}.
    """
    return calculators.calc_patent(potential_income, months, contributions_paid, has_employees)


@mcp.tool
async def calc_penalty(
    amount: Annotated[float, Field(ge=0, description="Сумма недоимки (руб.), на которую начисляются пени.")],
    days: Annotated[int, Field(ge=0, description="Число календарных дней просрочки с даты, когда платёж должен был быть уплачен.")],
    key_rate: Annotated[float | None, Field(default=None, description="Ключевая ставка ЦБ РФ, % годовых. Если не задана — берётся из встроенного снапшота (см. snapshot_date в ответе).")] = None,
    payer: Annotated[str, Field(default="ip", description="Тип плательщика: org (организация), ip (ИП), individual (физлицо, не ИП).", pattern="^(org|ip|individual)$")] = "ip",
) -> dict[str, Any]:
    """Estimate late-payment tax penalty (пени) per NK RF art. 75 using the CBR key rate.

    **When to use**
    - Quick what-if: how much penalty accrues on a known arrears amount and delay in days.
    - Org vs IP/individual: orgs use 1/300 for days 1–30, then 1/150; IP and individuals use 1/300 for all days.
    - Before a payment plan or negotiation — ballpark figure, not a filing document.

    **When NOT to use**
    - Multi-period calculation where the key rate changed during the delay (this tool uses one flat rate).
    - Penalties on non-tax debts (commercial loans, fines outside NK art. 75).
    - Official amount on a tax notice — always take the figure from FNS/LK; use this tool only for estimates.

    **Parameters**
    - amount: principal arrears in RUB (non-negative).
    - days: calendar days of delay (0 → penalty 0).
    - key_rate: annual CBR %; omit to use bundled snapshot default (response includes warning_ru if default used).
    - payer: `org` | `ip` | `individual` — selects the 1/300 vs 1/150 split for legal entities.

    **Returns** (dict)
    - penalty (float): total penalty in RUB, rounded to kopecks.
    - amount, days, key_rate, payer: echo inputs used in the formula.
    - formula (str): human-readable breakdown.
    - article: "ст. 75 НК РФ".
    - snapshot_date, disclaimer, source: metadata for audit trail.
    - warning_ru (optional): present when key_rate was taken from snapshot, not passed explicitly.

    **Limitations**
    - Single key rate for the whole period; for rate changes call get_rates(fresh=true) and run per sub-period manually.
    - Does not include other sanctions (штрафы) under NK art. 122/123 — only пени art. 75.
    - Read-only, offline, deterministic; no side effects; idempotent for identical inputs.

    **Examples**
    - Org, 100_000 RUB, 45 days, key_rate=21: days 1–30 @ amount×21%/300×30 + days 31–45 @ amount×21%/150×15.
    - IP, 50_000 RUB, 10 days: amount×rate%/300×10 for all days.
    - days=0 → penalty=0 regardless of amount.
    """
    return calculators.calc_penalty(amount, days, key_rate, payer)


@mcp.tool
async def get_rates(
    fresh: Annotated[bool, Field(default=False, description="true — тянуть свежие ставки с hosted (нужен ключ Pro); false — офлайн-снапшот.")] = False,
    kinds: Annotated[list[str] | None, Field(default=None, description="Фильтр: cbr_key_rate | ip_fixed_contrib | usn_limits | ndfl_scale | deflator.")] = None,
) -> dict[str, Any]:
    """Tax rates and limits: offline snapshot (default) or live hosted pull (fresh=true, Pro key).

    **When to use**
    - Before calc_penalty: pass fresh CBR key_rate or read cbr_key_rate_default from snapshot.
    - Before calc_usn/calc_insurance_ip: verify USN income limits and IP contribution fixed amounts.
    - kinds filter narrows response: cbr_key_rate | ip_fixed_contrib | usn_limits | ndfl_scale | deflator.

    **When NOT to use**
    - Legal interpretation of limits — snapshot may lag; use fresh=true for production DD.

    **Parameters**
    - fresh: false (default) = bundled snapshot, no network; true = hosted API (MCP_FNS_CALC_TOKEN).
    - kinds: optional list to subset tables.

    **Returns**: rate tables, snapshot_date (offline) or live payload + disclaimer.

    **Limitations**: fresh=true requires Pro token; snapshot date fixed at package build time.
    """
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
    """Check self-employed (NPD/НПД) status by taxpayer INN via FNS open data. Requires Pro API key.

    When to use: before paying a sole proprietor as individual — mandatory NPD verification.
    When NOT to use: offline what-if math — use calculators; without token returns missing_token error.
    Side effects: read-only HTTPS to hosted backend; no local data mutation. Needs MCP_FNS_CALC_TOKEN.

    Returns: {is_selfemployed, inn, checked_at, source, disclaimer} or error/missing_token.
    """
    return await _hosted_call("check_selfemployed", lambda: _call(lambda c: c.check_selfemployed(inn)))


@mcp.tool
async def check_ip_status(
    inn: Annotated[str, Field(min_length=12, max_length=12, description="ИНН ИП (12 цифр).")],
) -> dict[str, Any]:
    """IP registration status, OKVED and EGRIP dates by 12-digit INN. Requires Pro API key.

    When to use: confirm counterparty is active IP before contract/payment.
    When NOT to use: full EGRUL extract — use mcp-egrul; offline mode unavailable.
    Side effects: read-only HTTPS; needs MCP_FNS_CALC_TOKEN.

    Returns: {status, inn, okved, registration_date, ... disclaimer} or error.
    """
    return await _hosted_call("check_ip_status", lambda: _call(lambda c: c.check_ip_status(inn)))


@mcp.tool
async def check_disqualified(
    inn: Annotated[str | None, Field(default=None, description="ИНН для поиска в реестре дисквалифицированных.")] = None,
    fio: Annotated[str | None, Field(default=None, description="ФИО для поиска (если нет ИНН).")] = None,
) -> dict[str, Any]:
    """Match against FNS disqualified persons register by INN or FIO. Requires Pro API key.

    When to use: compliance screen before appointing director/signatory; provide inn or fio.
    When NOT to use: sole evidence for legal decisions — result is indicative, verify manually.
    Side effects: read-only HTTPS; needs MCP_FNS_CALC_TOKEN. At least one of inn/fio required.

    Returns: {matches[], query, disclaimer} or validation/error.
    """
    if not inn and not fio:
        return {"error": "validation", "message_ru": "Нужно указать inn или fio."}
    return await _hosted_call("check_disqualified", lambda: _call(lambda c: c.check_disqualified(inn, fio)))


@mcp.tool
async def check_account_block(
    inn: Annotated[str, Field(min_length=10, max_length=12, description="ИНН организации/ИП.")],
) -> dict[str, Any]:
    """Bank account operation suspension info from FNS (10–12 digit INN). Requires Pro API key.

    When to use: treasury/compliance check before large outbound payment.
    When NOT to use: real-time bank balance — different service; offline unavailable.
    Side effects: read-only HTTPS; needs MCP_FNS_CALC_TOKEN.

    Returns: {suspended, inn, details, disclaimer} or error.
    """
    return await _hosted_call("check_account_block", lambda: _call(lambda c: c.check_account_block(inn)))


@mcp.tool
async def check_tax_arrears(
    inn: Annotated[str, Field(min_length=10, max_length=12, description="ИНН организации/ИП.")],
) -> dict[str, Any]:
    """Tax arrears / debt flags from FNS Transparent Business open data. Requires Pro API key.

    When to use: counterparty due diligence alongside mcp-fns-check; 10–12 digit INN.
    When NOT to use: official debt certificate — request from FNS; offline unavailable.
    Side effects: read-only HTTPS; needs MCP_FNS_CALC_TOKEN.

    Returns: {has_arrears, inn, amounts[], disclaimer} or error.
    """
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
