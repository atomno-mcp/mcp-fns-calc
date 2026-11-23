"""Тройка источник / дата / однофамилец для проверки дисквалификации."""

from __future__ import annotations

from datetime import datetime

import pytest

from mcp_fns_calc.person_notice import (
    HOMONYM_WARNING_RU,
    attach_disqualified_notice,
    build_notice_ru,
    format_queried_at_ru,
)


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


@pytest.mark.parametrize(
    ("value", "expected"),
    [
        (datetime(2026, 9, 2, 10, 22), "2 сентября 2026, 10:22 UTC"),
        ("2026-09-02T00:15:00+05:00", "1 сентября 2026, 19:15 UTC"),
        (None, "время запроса не сохранено"),
        ("исходное значение без даты", "исходное значение без даты"),
    ],
)
def test_format_query_time_keeps_its_meaning(value, expected) -> None:
    assert format_queried_at_ru(value) == expected


@pytest.mark.parametrize(
    ("data_as_of", "registry_date_note", "expected"),
    [
        ("2026-09-01", None, "Данные перечня на 2026-09-01."),
        (None, "перечень на 2026-08-31", "Даты перечней: перечень на 2026-08-31."),
    ],
)
def test_registry_date_is_reported_separately_from_query_time(data_as_of, registry_date_note, expected) -> None:
    notice = build_notice_ru(
        source_registry_ru="Реестр ФНС",
        source_url="https://service.nalog.ru/disqualified.do",
        queried_at="2026-09-02T10:22:00Z",
        data_as_of=data_as_of,
        has_match=False,
        registry_date_note=registry_date_note,
    )
    assert "2 сентября 2026, 10:22 UTC" in notice
    assert expected in notice


def test_attach_keeps_already_formatted_notice() -> None:
    response = attach_disqualified_notice({"match_found": True, "checked_at": None})
    before = dict(response)
    assert attach_disqualified_notice(response) == before
    assert response["disclaimer"].count(HOMONYM_WARNING_RU) == 1
