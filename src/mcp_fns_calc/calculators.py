"""Налоговые калькуляторы РФ — чистая детерминированная логика.

Без внешних зависимостей (только stdlib + rates.py) — легко тестируется и
работает офлайн. Все денежные значения считаются через Decimal и округляются
до копеек (2 знака) в конце. Каждая функция возвращает dict с полями расчёта,
формулой, ссылкой на норму НК РФ, а также disclaimer/source/snapshot_date.
"""

from __future__ import annotations

from decimal import ROUND_HALF_UP, Decimal
from typing import Any

from . import rates

_CENTS = Decimal("0.01")


def _money(value: Decimal) -> float:
    """Округлить до копеек и вернуть float (для JSON-сериализации в MCP-ответе)."""
    return float(value.quantize(_CENTS, rounding=ROUND_HALF_UP))


def _dec(value: float | int | str | Decimal) -> Decimal:
    return value if isinstance(value, Decimal) else Decimal(str(value))


def _envelope(payload: dict[str, Any], year: int, article: str) -> dict[str, Any]:
    payload["article"] = article
    payload["disclaimer"] = rates.DISCLAIMER
    payload["source"] = rates.source_note(year)
    payload["snapshot_date"] = rates.SNAPSHOT_DATE
    return payload


# --- НДС ---------------------------------------------------------------------

def calc_vat(amount: float, rate: float = 20.0, mode: str = "add") -> dict[str, Any]:
    """НДС: начислить сверху (add) или выделить из суммы (extract).

    rate — 20/10/0 (общие) или 5/7 (спец-ставки УСН с 2025). mode:
      add     — amount это сумма без НДС, считаем НДС и итог с НДС;
      extract — amount это сумма с НДС, выделяем НДС и сумму без НДС.
    """
    r = _dec(rate)
    if r not in rates.VAT_RATES and r not in rates.VAT_USN_SPECIAL:
        return {"error": "validation", "message_ru": f"Недопустимая ставка НДС: {rate}. Разрешены 20/10/0/5/7."}
    a = _dec(amount)
    if a < 0:
        return {"error": "validation", "message_ru": "Сумма не может быть отрицательной."}
    if mode == "add":
        vat = a * r / Decimal("100")
        net, gross = a, a + vat
        formula = f"НДС = {amount} × {rate}% = {_money(vat)}; итого = {_money(gross)}"
    elif mode == "extract":
        vat = a * r / (Decimal("100") + r)
        net, gross = a - vat, a
        formula = f"НДС = {amount} × {rate}/(100+{rate}) = {_money(vat)}; без НДС = {_money(net)}"
    else:
        return {"error": "validation", "message_ru": "mode должен быть 'add' или 'extract'."}
    return _envelope(
        {
            "net": _money(net),
            "vat": _money(vat),
            "gross": _money(gross),
            "rate": float(r),
            "mode": mode,
            "formula": formula,
        },
        year=2025,
        article="ст. 164, 168 НК РФ",
    )


# --- УСН ---------------------------------------------------------------------

def calc_usn(
    income: float,
    obj: str = "income",
    expenses: float = 0.0,
    rate: float | None = None,
    contributions_paid: float = 0.0,
    has_employees: bool = False,
    advances_paid: float = 0.0,
) -> dict[str, Any]:
    """УСН: объект 'income' (6%) или 'income_minus_expense' (15%).

    rate — региональная ставка (если не задана, берётся базовая 6/15).
    contributions_paid — уплаченные страховые взносы (уменьшают налог по 'income'
    полностью для ИП без работников, максимум на 50% при наличии работников).
    advances_paid — ранее уплаченные авансовые платежи (вычитаются из итога).
    """
    inc = _dec(income)
    if inc < 0:
        return {"error": "validation", "message_ru": "Доход не может быть отрицательным."}
    adv = _dec(advances_paid)

    if obj == "income":
        r = _dec(rate) if rate is not None else rates.USN_INCOME_RATE
        tax = inc * r / Decimal("100")
        contrib = _dec(contributions_paid)
        max_reduction = tax / Decimal("2") if has_employees else tax
        reduction = min(contrib, max_reduction)
        tax_after = tax - reduction
        to_pay = max(Decimal("0"), tax_after - adv)
        return _envelope(
            {
                "object": "income",
                "rate": float(r),
                "tax_before_reduction": _money(tax),
                "contributions_reduction": _money(reduction),
                "tax_after_reduction": _money(tax_after),
                "advances_paid": _money(adv),
                "to_pay": _money(to_pay),
                "formula": (
                    f"налог = {income} × {float(r)}% = {_money(tax)}; "
                    f"− взносы {_money(reduction)} "
                    f"({'макс. 50%' if has_employees else 'без ограничения (ИП без работников)'}); "
                    f"− авансы {_money(adv)} = {_money(to_pay)}"
                ),
            },
            year=2025,
            article="ст. 346.20, 346.21 НК РФ",
        )

    if obj == "income_minus_expense":
        r = _dec(rate) if rate is not None else rates.USN_PROFIT_RATE
        exp = _dec(expenses)
        base = inc - exp
        tax = max(Decimal("0"), base) * r / Decimal("100")
        min_tax = inc * rates.USN_MIN_TAX_RATE / Decimal("100")
        payable = max(tax, min_tax)
        to_pay = max(Decimal("0"), payable - adv)
        return _envelope(
            {
                "object": "income_minus_expense",
                "rate": float(r),
                "tax_base": _money(base),
                "tax_calculated": _money(tax),
                "min_tax": _money(min_tax),
                "min_tax_applies": bool(min_tax > tax),
                "advances_paid": _money(adv),
                "to_pay": _money(to_pay),
                "formula": (
                    f"база = {income} − {expenses} = {_money(base)}; "
                    f"налог = {_money(tax)}; мин.налог 1% = {_money(min_tax)}; "
                    f"к уплате max(налог, мин) − авансы = {_money(to_pay)}"
                ),
            },
            year=2025,
            article="ст. 346.18, 346.20, 346.21 НК РФ",
        )

    return {"error": "validation", "message_ru": "obj должен быть 'income' или 'income_minus_expense'."}


# --- Страховые взносы ИП «за себя» -------------------------------------------

def calc_insurance_ip(income: float, year: int = 2025, months: int = 12) -> dict[str, Any]:
    """Взносы ИП «за себя»: фикс. часть (пропорц. месяцам) + 1% с дохода > 300 000 ₽.

    months — количество полных месяцев ведения деятельности в году (1–12) для
    неполного года. Дополнительный взнос ограничен предельным размером года.
    """
    if not (1 <= months <= 12):
        return {"error": "validation", "message_ru": "months должен быть от 1 до 12."}
    data = rates.ip_contributions_for(year)
    fixed_full = data["fixed"]
    cap = data["cap_1pct"]
    fixed = fixed_full * Decimal(months) / Decimal("12")
    inc = _dec(income)
    over = max(Decimal("0"), inc - rates.IP_CONTRIB_THRESHOLD)
    additional = min(over * Decimal("1") / Decimal("100"), cap)
    total = fixed + additional
    return _envelope(
        {
            "year": year,
            "months": months,
            "fixed_part": _money(fixed),
            "fixed_full_year": _money(fixed_full),
            "additional_1pct": _money(additional),
            "additional_capped": bool(over / Decimal("100") > cap),
            "total": _money(total),
            "formula": (
                f"фикс = {_money(fixed_full)} × {months}/12 = {_money(fixed)}; "
                f"доп = 1% × ({income} − 300000) = {_money(additional)} "
                f"(потолок {_money(cap)}); итого = {_money(total)}"
            ),
        },
        year=year,
        article="ст. 430 НК РФ",
    )


# --- НДФЛ (прогрессивная шкала 2025+) ----------------------------------------

def calc_ndfl(income: float, deductions: float = 0.0, year: int = 2025) -> dict[str, Any]:
    """НДФЛ по прогрессивной шкале 2025+ (13/15/18/20/22%). deductions — вычеты."""
    inc = _dec(income)
    ded = _dec(deductions)
    base = max(Decimal("0"), inc - ded)
    scale = rates.NDFL_SCALE_2025
    tax = Decimal("0")
    prev = Decimal("0")
    breakdown: list[dict[str, Any]] = []
    for upper, rate in scale:
        cap = upper if upper is not None else base
        if base <= prev:
            break
        taxable = min(base, cap) - prev
        if taxable <= 0:
            prev = cap
            continue
        part = taxable * rate / Decimal("100")
        tax += part
        breakdown.append(
            {
                "from": _money(prev),
                "to": _money(min(base, cap)),
                "rate": float(rate),
                "tax": _money(part),
            }
        )
        prev = cap
        if upper is None or base <= upper:
            break
    effective = (tax / base * Decimal("100")) if base > 0 else Decimal("0")
    return _envelope(
        {
            "year": year,
            "tax_base": _money(base),
            "tax": _money(tax),
            "net_income": _money(inc - tax),
            "effective_rate": float(effective.quantize(Decimal("0.01"), rounding=ROUND_HALF_UP)),
            "breakdown": breakdown,
        },
        year=year,
        article="п. 1 ст. 224 НК РФ",
    )


# --- ПСН (патент) ------------------------------------------------------------

def calc_patent(
    potential_income: float,
    months: int = 12,
    contributions_paid: float = 0.0,
    has_employees: bool = False,
) -> dict[str, Any]:
    """Стоимость патента: ПВД × 6% × месяцы/12, минус уплаченные взносы.

    potential_income — потенциально возможный годовой доход (из закона региона).
    """
    if not (1 <= months <= 12):
        return {"error": "validation", "message_ru": "months должен быть от 1 до 12."}
    pvd = _dec(potential_income)
    cost = pvd * rates.PSN_RATE / Decimal("100") * Decimal(months) / Decimal("12")
    contrib = _dec(contributions_paid)
    max_reduction = cost / Decimal("2") if has_employees else cost
    reduction = min(contrib, max_reduction)
    to_pay = max(Decimal("0"), cost - reduction)
    return _envelope(
        {
            "months": months,
            "cost_before_reduction": _money(cost),
            "contributions_reduction": _money(reduction),
            "to_pay": _money(to_pay),
            "formula": (
                f"патент = {potential_income} × 6% × {months}/12 = {_money(cost)}; "
                f"− взносы {_money(reduction)} = {_money(to_pay)}"
            ),
        },
        year=2025,
        article="ст. 346.50, 346.51 НК РФ",
    )


# --- Пени --------------------------------------------------------------------

def calc_penalty(
    amount: float,
    days: int,
    key_rate: float | None = None,
    payer: str = "ip",
) -> dict[str, Any]:
    """Пени за просрочку уплаты налога.

    payer='org' (организация): 1/300 за первые 30 дней, 1/150 с 31-го дня.
    payer='ip'|'individual': 1/300 за весь период.
    key_rate — ключевая ставка ЦБ РФ, % годовых (если не задана — снапшот,
    сверять с cbr.ru). Для точного многопериодного расчёта — hosted get_rates.
    """
    if days < 0:
        return {"error": "validation", "message_ru": "days не может быть отрицательным."}
    amt = _dec(amount)
    rate = _dec(key_rate) if key_rate is not None else rates.CBR_KEY_RATE_DEFAULT
    rate_used_default = key_rate is None

    if payer == "org":
        d1 = min(days, 30)
        d2 = max(0, days - 30)
        # Пеня по фракции ставки: сумма × (rate/100) × (1/N) × дни
        pen1 = amt * rate / Decimal("100") / Decimal("300") * Decimal(d1)
        pen2 = amt * rate / Decimal("100") / Decimal("150") * Decimal(d2)
        penalty = pen1 + pen2
        formula = (
            f"{amount} × {float(rate)}%/300 × {d1}дн + "
            f"{amount} × {float(rate)}%/150 × {d2}дн = {_money(penalty)}"
        )
    elif payer in ("ip", "individual"):
        penalty = amt * rate / Decimal("100") / Decimal("300") * Decimal(days)
        formula = f"{amount} × {float(rate)}%/300 × {days}дн = {_money(penalty)}"
    else:
        return {"error": "validation", "message_ru": "payer должен быть 'org', 'ip' или 'individual'."}

    result = _envelope(
        {
            "amount": _money(amt),
            "days": days,
            "key_rate": float(rate),
            "payer": payer,
            "penalty": _money(penalty),
            "formula": formula,
        },
        year=2025,
        article="ст. 75 НК РФ",
    )
    if rate_used_default:
        result["warning_ru"] = (
            "Ключевая ставка взята из снапшота — проверьте актуальную на cbr.ru "
            "или используйте свежий get_rates (Pro). Для периодов с разной ставкой "
            "расчёт по одной ставке приблизителен."
        )
    return result
