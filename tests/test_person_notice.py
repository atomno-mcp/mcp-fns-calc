"""Тройка источник / дата / однофамилец для проверки дисквалификации."""

from __future__ import annotations

from mcp_fns_calc.person_notice import HOMONYM_WARNING_RU, attach_disqualified_notice


def test_attach_on_match() -> None:
    result = attach_disqualified_notice(
        {
            "match_found": True,
            "matches": [{}],
            "disclaimer": "базовый",
            "checked_at": "2026-09-02T10:22:00+00:00",
        }
    )
    assert result["source_url"] == "https://service.nalog.ru/disqualified.do"
    assert result["data_as_of"] is None
    assert result["homonym_warning_ru"] == HOMONYM_WARNING_RU
    assert "однофамилец" in result["notice_ru"]
    assert "однофамилец" in result["disclaimer"]
    assert result["match_found"] is True
    assert "Запрошено в реестре" in result["notice_ru"]
    assert "2 сентября 2026, 10:22 UTC" in result["notice_ru"]
    assert "не публикует" in result["notice_ru"]


def test_attach_on_no_match() -> None:
    result = attach_disqualified_notice(
        {
            "match_found": False,
            "matches": [],
            "checked_at": "2026-09-02T10:22:00+00:00",
        }
    )
    assert result["source_url"] == "https://service.nalog.ru/disqualified.do"
    assert result["homonym_warning_ru"] is None
    assert "однофамилец" not in result["notice_ru"]
    assert "Запрошено в реестре" in result["notice_ru"]
    assert "не публикует" in result["notice_ru"]
    assert "неизвестна" not in result["notice_ru"]


def test_error_passthrough() -> None:
    raw = {"error": "validation"}
    assert attach_disqualified_notice(raw) == raw
