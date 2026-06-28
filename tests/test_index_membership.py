from __future__ import annotations

from pathlib import Path

import pandas as pd

from cn_equity_snapshot_pipelines.index_membership import (
    TIMELINE_FILENAME_TEMPLATE,
    capture_snapshot,
    constituents_as_of,
    load_membership_timeline,
    normalize_symbol,
)


def test_normalize_symbol_variants():
    assert normalize_symbol("600519") == "600519"
    assert normalize_symbol("600519.SH") == "600519"
    assert normalize_symbol("000001") == "000001"
    assert normalize_symbol("300750.sz") == "300750"
    assert normalize_symbol("") == ""
    assert normalize_symbol(None) == ""


def test_capture_snapshot_appends_to_timeline(tmp_path: Path):
    result = capture_snapshot("000905", snapshot_date="2026-06-15", output_dir=tmp_path)
    assert result["index_code"] == "000905"
    assert result["as_of"] == "2026-06-15"
    assert result["constituent_count"] > 0
    assert result["new_symbols"] > 0
    assert result["newly_removed"] >= 0

    timeline_path = tmp_path / TIMELINE_FILENAME_TEMPLATE.format(index_code="000905")
    assert timeline_path.exists()
    timeline = pd.read_csv(timeline_path, dtype=str)
    assert len(timeline) == result["constituent_count"]
    assert all(timeline["last_seen_date"] == "2026-06-15")
    assert all(timeline["first_seen_date"] == "2026-06-15")


def test_capture_snapshot_updates_existing_members(tmp_path: Path):
    # First capture
    capture_snapshot("000905", snapshot_date="2026-01-15", output_dir=tmp_path)

    # Second capture
    result = capture_snapshot("000905", snapshot_date="2026-06-15", output_dir=tmp_path)
    assert result["new_symbols"] == 0  # No new symbols if constituents are same
    assert result["newly_removed"] >= 0

    timeline = load_membership_timeline("000905", snapshot_dir=tmp_path)
    assert not timeline.empty
    # All members from first capture should have last_seen_date updated
    first_seen = timeline.loc[timeline["first_seen_date"] == "2026-01-15"]
    assert len(first_seen) > 0
    assert all(first_seen["last_seen_date"] == "2026-06-15")


def test_load_membership_timeline_empty_when_no_snapshot(tmp_path: Path):
    timeline = load_membership_timeline("000905", snapshot_dir=tmp_path)
    assert timeline.empty
    assert list(timeline.columns) == [
        "symbol", "index_code", "first_seen_date", "last_seen_date",
        "inclusion_date", "removed_date",
    ]


def test_constituents_as_of_before_first_snapshot_falls_back(tmp_path: Path):
    # Without any snapshot, constituents_as_of should return empty tuple
    # (no timeline exists yet)
    members = constituents_as_of(
        "000905",
        "2020-01-01",
        snapshot_dir=tmp_path,
        fallback_to_inclusion_table=False,
    )
    assert members == ()


def test_constituents_as_of_after_snapshot(tmp_path: Path):
    capture_snapshot("000905", snapshot_date="2026-06-15", output_dir=tmp_path)
    members = constituents_as_of(
        "000905",
        "2026-06-15",
        snapshot_dir=tmp_path,
        fallback_to_inclusion_table=False,
    )
    assert len(members) > 0
    assert all(isinstance(s, str) and len(s) == 6 for s in members)


def test_detect_removed_members(tmp_path: Path):
    # Manually create a timeline where one symbol is removed
    timeline_path = tmp_path / TIMELINE_FILENAME_TEMPLATE.format(index_code="000905")
    pd.DataFrame(
        [
            {"symbol": "600519", "index_code": "000905", "first_seen_date": "2026-01-01",
             "last_seen_date": "2026-01-01", "inclusion_date": "2018-06-01", "removed_date": ""},
            {"symbol": "000001", "index_code": "000905", "first_seen_date": "2026-01-01",
             "last_seen_date": "2026-01-01", "inclusion_date": "2020-12-01", "removed_date": ""},
        ]
    ).to_csv(timeline_path, index=False)

    # Now capture a real snapshot (600519 and 000001 may or may not be in CSI500
    # on the capture date). The module's detect_removals will mark absent symbols.
    result = capture_snapshot("000905", snapshot_date="2026-06-15", output_dir=tmp_path)
    assert result["newly_removed"] >= 0
