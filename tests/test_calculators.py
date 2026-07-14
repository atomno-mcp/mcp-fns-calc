"""Тесты калькуляторов — сверенные значения (НК РФ, снапшот 2025/2026)."""

from __future__ import annotations

from mcp_fns_calc import calculators as c


class TestVat:
    def test_add_20(self) -> None:
        r = c.calc_vat(1000, 20, "add")
        assert r["net"] == 1000.0 and r["vat"] == 200.0 and r["gross"] == 1200.0

    def test_extract_20(self) -> None:
        r = c.calc_vat(1200, 20, "extract")
        assert r["net"] == 1000.0 and r["vat"] == 200.0 and r["gross"] == 1200.0

    def test_bad_rate(self) -> None:
        assert c.calc_vat(1000, 13, "add")["error"] == "validation"

    def test_bad_mode(self) -> None:
        assert c.calc_vat(1000, 20, "sideways")["error"] == "validation"

    def test_has_disclaimer(self) -> None:
        assert "disclaimer" in c.calc_vat(1000)


class TestUsn:
    def test_income_6_reduced_by_contributions(self) -> None:
        r = c.calc_usn(2_400_000, "income", contributions_paid=53_658)
        assert r["tax_before_reduction"] == 144_000.0
        assert r["to_pay"] == 90_342.0

    def test_income_employees_cap_50pct(self) -> None:
        r = c.calc_usn(1_000_000, "income", contributions_paid=100_000, has_employees=True)
        # налог 60000, вычет максимум 30000
        assert r["tax_after_reduction"] == 30_000.0

    def test_profit_15(self) -> None:
        r = c.calc_usn(3_000_000, "income_minus_expense", expenses=1_800_000)
        assert r["tax_calculated"] == 180_000.0
        assert r["min_tax"] == 30_000.0
        assert r["min_tax_applies"] is False
        assert r["to_pay"] == 180_000.0

    def test_min_tax_applies(self) -> None:
        r = c.calc_usn(1_000_000, "income_minus_expense", expenses=990_000)
        assert r["min_tax_applies"] is True
        assert r["to_pay"] == 10_000.0

    def test_bad_object(self) -> None:
        assert c.calc_usn(100, "nonsense")["error"] == "validation"


class TestInsuranceIp:
    def test_2025_full_year(self) -> None:
        r = c.calc_insurance_ip(2_400_000, 2025)
        assert r["fixed_part"] == 53_658.0
        assert r["additional_1pct"] == 21_000.0
        assert r["total"] == 74_658.0

    def test_2026_partial(self) -> None:
        r = c.calc_insurance_ip(200_000, 2026, months=6)
        assert r["fixed_part"] == 28_695.0
        assert r["additional_1pct"] == 0.0

    def test_cap_applies(self) -> None:
        # огромный доход → доп. взнос упирается в потолок 300888 (2025)
        r = c.calc_insurance_ip(100_000_000, 2025)
        assert r["additional_1pct"] == 300_888.0
        assert r["additional_capped"] is True

    def test_bad_months(self) -> None:
        assert c.calc_insurance_ip(100, 2025, months=13)["error"] == "validation"


class TestNdfl:
    def test_6m(self) -> None:
        r = c.calc_ndfl(6_000_000)
        assert r["tax"] == 882_000.0  # 312000 + 390000 + 180000

    def test_full_scale_60m(self) -> None:
        r = c.calc_ndfl(60_000_000)
        assert r["tax"] == 11_602_000.0
        assert len(r["breakdown"]) == 5

    def test_flat_13_below_first_bracket(self) -> None:
        r = c.calc_ndfl(2_000_000)
        assert r["tax"] == 260_000.0
        assert r["effective_rate"] == 13.0

    def test_deductions(self) -> None:
        r = c.calc_ndfl(2_400_000, deductions=400_000)
        assert r["tax_base"] == 2_000_000.0
        assert r["tax"] == 260_000.0


class TestPatent:
    def test_full_year(self) -> None:
        assert c.calc_patent(1_000_000, 12)["to_pay"] == 60_000.0

    def test_half_year(self) -> None:
        assert c.calc_patent(1_000_000, 6)["cost_before_reduction"] == 30_000.0


class TestPenalty:
    def test_org_over_30_days(self) -> None:
        r = c.calc_penalty(100_000, 40, 21, "org")
        # 100000*21%/300*30 + 100000*21%/150*10 = 2100 + 1400
        assert r["penalty"] == 3_500.0

    def test_ip_flat_1_300(self) -> None:
        r = c.calc_penalty(100_000, 40, 21, "ip")
        assert r["penalty"] == 2_800.0

    def test_default_rate_warns(self) -> None:
        r = c.calc_penalty(100_000, 10, payer="ip")
        assert "warning_ru" in r

    def test_bad_payer(self) -> None:
        assert c.calc_penalty(100_000, 10, 21, "alien")["error"] == "validation"
