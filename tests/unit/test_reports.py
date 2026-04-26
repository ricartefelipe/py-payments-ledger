from __future__ import annotations

from datetime import time

from src.api.routers.reports import _parse_dt


def test_parse_to_date_uses_end_of_day() -> None:
    parsed = _parse_dt("2026-04-26", end_of_day=True)

    assert parsed is not None
    assert parsed.time() == time.max


def test_parse_from_date_uses_start_of_day() -> None:
    parsed = _parse_dt("2026-04-26")

    assert parsed is not None
    assert parsed.time() == time.min
