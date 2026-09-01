from __future__ import annotations

import hashlib
import json
from pathlib import Path

import pandas as pd
import pytest

import cn_equity_snapshot_pipelines.index_membership as index_membership
from cn_equity_snapshot_pipelines.index_membership import (
    TIMELINE_FILENAME_TEMPLATE,
    capture_snapshot,
    constituents_as_of,
    load_membership_timeline,
    normalize_symbol,
    query_constituents_as_of,
)


@pytest.fixture(autouse=True)
def _stub_index_membership_sources(monkeypatch):
    monkeypatch.setattr(
        index_membership,
        "fetch_current_constituents",
        lambda index_code: pd.DataFrame({"symbol": ["600519", "000001"]}),
    )
    monkeypatch.setattr(
        index_membership,
        "fetch_inclusion_dates",
        lambda index_code: {"600519": "2018-06-01", "000001": "2020-12-01"},
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


def test_first_snapshot_writes_incomplete_forward_accumulation_manifest(tmp_path: Path):
    result = capture_snapshot("000905", snapshot_date="2026-06-28", output_dir=tmp_path)

    manifest = json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))
    assert manifest["coverage_start"] == "2026-06-28"
    assert manifest["coverage_end"] == "2026-06-28"
    assert manifest["captured_through"] == "2026-06-28"
    assert manifest["historical_removed_members_complete"] is False
    assert manifest["source_as_known_semantics"] == "forward_accumulation_from_first_snapshot"
    assert manifest["comparison_status"] == "incomparable"
    assert manifest["reason_codes"] == [
        "HISTORICAL_REMOVED_MEMBERS_UNPROVEN",
        "NO_PRIOR_SNAPSHOT",
    ]
    assert manifest["changes"] == []
    assert manifest["removed"] == []


def test_query_at_first_snapshot_remains_incomparable_and_inherits_manifest_reasons(tmp_path: Path):
    result = capture_snapshot("000905", snapshot_date="2026-06-28", output_dir=tmp_path)
    manifest = json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))

    query = query_constituents_as_of("000905", "2026-06-28", snapshot_dir=tmp_path)

    assert query == {
        "index_code": "000905",
        "as_of": "2026-06-28",
        "coverage_start": "2026-06-28",
        "coverage_end": "2026-06-28",
        "captured_through": "2026-06-28",
        "comparison_status": "incomparable",
        "members": [],
        "changes": [],
        "removed": [],
        "reason_codes": manifest["reason_codes"],
    }


def test_later_snapshot_keeps_forward_accumulation_comparison(tmp_path: Path, monkeypatch):
    capture_snapshot("000905", snapshot_date="2026-06-28", output_dir=tmp_path)
    monkeypatch.setattr(
        index_membership,
        "fetch_current_constituents",
        lambda index_code: pd.DataFrame({"symbol": ["600519"]}),
    )

    result = capture_snapshot("000905", snapshot_date="2026-07-01", output_dir=tmp_path)

    manifest = json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))
    assert manifest["coverage_start"] == "2026-06-28"
    assert manifest["coverage_end"] == "2026-07-01"
    assert manifest["captured_through"] == "2026-07-01"
    assert manifest["historical_removed_members_complete"] is False
    assert manifest["comparison_status"] == "comparable"
    assert manifest["reason_codes"] == ["HISTORICAL_REMOVED_MEMBERS_UNPROVEN"]
    assert manifest["changes"] == []
    assert manifest["removed"] == ["000001"]


@pytest.mark.parametrize("as_of", ["2026-07-02", "2099-01-01"])
def test_query_after_coverage_end_fails_closed_and_inherits_manifest_reasons(
    tmp_path: Path,
    monkeypatch,
    as_of: str,
):
    capture_snapshot("000905", snapshot_date="2026-06-28", output_dir=tmp_path)
    monkeypatch.setattr(
        index_membership,
        "fetch_current_constituents",
        lambda index_code: pd.DataFrame({"symbol": ["600519"]}),
    )
    result = capture_snapshot("000905", snapshot_date="2026-07-01", output_dir=tmp_path)
    manifest = json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))

    query = query_constituents_as_of("000905", as_of, snapshot_dir=tmp_path)

    assert query == {
        "index_code": "000905",
        "as_of": as_of,
        "coverage_start": "2026-06-28",
        "coverage_end": "2026-07-01",
        "captured_through": "2026-07-01",
        "comparison_status": "incomparable",
        "members": [],
        "changes": [],
        "removed": [],
        "reason_codes": ["AS_OF_AFTER_COVERAGE_END", *manifest["reason_codes"]],
    }


def test_query_within_coverage_inherits_comparable_manifest_reasons(tmp_path: Path, monkeypatch):
    capture_snapshot("000905", snapshot_date="2026-06-28", output_dir=tmp_path)
    monkeypatch.setattr(
        index_membership,
        "fetch_current_constituents",
        lambda index_code: pd.DataFrame({"symbol": ["600519"]}),
    )
    result = capture_snapshot("000905", snapshot_date="2026-07-01", output_dir=tmp_path)
    manifest = json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))

    query = query_constituents_as_of("000905", "2026-07-01", snapshot_dir=tmp_path)

    assert query["comparison_status"] == "comparable"
    assert query["reason_codes"] == manifest["reason_codes"]


@pytest.mark.parametrize(
    "manifest_contents",
    [None, "{", "wrong-coverage-start", "hash-mismatch"],
)
def test_load_membership_timeline_fails_closed_without_valid_manifest(
    tmp_path: Path,
    manifest_contents: str | None,
    monkeypatch,
):
    result = capture_snapshot("000905", snapshot_date="2026-06-28", output_dir=tmp_path)
    manifest_path = Path(result["manifest_path"])
    if manifest_contents is None:
        manifest_path.unlink()
    elif manifest_contents in {"wrong-coverage-start", "hash-mismatch"}:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
        if manifest_contents == "wrong-coverage-start":
            manifest["coverage_start"] = "2020-01-01"
        else:
            manifest["timeline_sha256"] = "0" * 64
        manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    else:
        manifest_path.write_text(manifest_contents, encoding="utf-8")

    with pytest.raises(ValueError, match="manifest"):
        load_membership_timeline("000905", snapshot_dir=tmp_path)

    monkeypatch.setattr(
        index_membership,
        "fetch_current_constituents",
        lambda index_code: pytest.fail("must not fetch when manifest is invalid"),
    )
    with pytest.raises(ValueError, match="manifest"):
        capture_snapshot("000905", snapshot_date="2026-07-01", output_dir=tmp_path)


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
    # Without any snapshot, the legacy API fails closed.
    members = constituents_as_of(
        "000905",
        "2020-01-01",
        snapshot_dir=tmp_path,
        fallback_to_inclusion_table=False,
    )
    assert members == ()


def test_query_before_coverage_fails_closed_without_current_member_fallback(
    tmp_path: Path,
    monkeypatch,
):
    capture_snapshot("000905", snapshot_date="2026-06-15", output_dir=tmp_path)
    monkeypatch.setattr(
        index_membership,
        "fetch_current_constituents",
        lambda index_code: pytest.fail("query must not fetch current members"),
    )

    result = query_constituents_as_of(
        "000905",
        "2026-06-14",
        snapshot_dir=tmp_path,
    )

    assert result == {
        "index_code": "000905",
        "as_of": "2026-06-14",
        "coverage_start": "2026-06-15",
        "coverage_end": "2026-06-15",
        "captured_through": "2026-06-15",
        "comparison_status": "incomparable",
        "members": [],
        "changes": [],
        "removed": [],
        "reason_codes": [
            "AS_OF_BEFORE_COVERAGE_START",
            "HISTORICAL_REMOVED_MEMBERS_UNPROVEN",
            "NO_PRIOR_SNAPSHOT",
        ],
    }
    assert constituents_as_of(
        "000905",
        "2026-06-14",
        snapshot_dir=tmp_path,
        fallback_to_inclusion_table=True,
    ) == ()


def test_constituents_as_of_after_comparable_snapshot(tmp_path: Path):
    capture_snapshot("000905", snapshot_date="2026-06-15", output_dir=tmp_path)
    capture_snapshot("000905", snapshot_date="2026-06-21", output_dir=tmp_path)
    members = constituents_as_of(
        "000905",
        "2026-06-21",
        snapshot_dir=tmp_path,
        fallback_to_inclusion_table=False,
    )
    assert len(members) > 0
    assert all(isinstance(s, str) and len(s) == 6 for s in members)


def test_reader_fails_closed_when_timeline_is_replaced_without_its_manifest(tmp_path: Path):
    result = capture_snapshot("000905", snapshot_date="2026-06-15", output_dir=tmp_path)
    timeline_path = Path(result["timeline_path"])
    timeline = pd.read_csv(timeline_path, dtype=str)
    timeline.loc[:, "last_seen_date"] = "2026-06-21"
    timeline.to_csv(timeline_path, index=False)

    with pytest.raises(ValueError, match="manifest"):
        query_constituents_as_of("000905", "2026-06-15", snapshot_dir=tmp_path)


def test_detect_removed_members(tmp_path: Path, monkeypatch):
    capture_snapshot("000905", snapshot_date="2026-01-01", output_dir=tmp_path)
    monkeypatch.setattr(
        index_membership,
        "fetch_current_constituents",
        lambda index_code: pd.DataFrame({"symbol": ["600519"]}),
    )

    result = capture_snapshot("000905", snapshot_date="2026-06-15", output_dir=tmp_path)
    assert result["newly_removed"] == 1


@pytest.mark.parametrize("rejected_date", ["2026-06-14", "2026-06-20"])
def test_capture_rejects_dates_before_coverage_or_latest_without_fetch_or_write(
    tmp_path: Path,
    monkeypatch,
    rejected_date: str,
):
    first = capture_snapshot("000905", snapshot_date="2026-06-15", output_dir=tmp_path)
    capture_snapshot("000905", snapshot_date="2026-06-21", output_dir=tmp_path)
    timeline_path = Path(first["timeline_path"])
    manifest_path = Path(first["manifest_path"])
    old_pair = (timeline_path.read_bytes(), manifest_path.read_bytes())
    monkeypatch.setattr(
        index_membership,
        "fetch_current_constituents",
        lambda index_code: pytest.fail("invalid capture date must fail before fetch"),
    )
    monkeypatch.setattr(
        index_membership,
        "fetch_inclusion_dates",
        lambda index_code: pytest.fail("invalid capture date must fail before fetch"),
    )

    with pytest.raises(ValueError, match="snapshot_date"):
        capture_snapshot("000905", snapshot_date=rejected_date, output_dir=tmp_path)

    assert (timeline_path.read_bytes(), manifest_path.read_bytes()) == old_pair


def test_capture_rejects_duplicate_snapshot_date_before_provider_or_write(
    tmp_path: Path,
    monkeypatch,
):
    first = capture_snapshot("000905", snapshot_date="2026-06-15", output_dir=tmp_path)
    timeline_path = Path(first["timeline_path"])
    manifest_path = Path(first["manifest_path"])
    old_pair = (timeline_path.read_bytes(), manifest_path.read_bytes())
    provider_calls: list[str] = []

    def record_provider_call(name: str):
        provider_calls.append(name)
        pytest.fail("duplicate snapshot date must fail before provider calls")

    monkeypatch.setattr(
        index_membership,
        "fetch_current_constituents",
        lambda index_code: record_provider_call("constituents"),
    )
    monkeypatch.setattr(
        index_membership,
        "fetch_inclusion_dates",
        lambda index_code: record_provider_call("inclusion_dates"),
    )

    with pytest.raises(ValueError) as exc_info:
        capture_snapshot("000905", snapshot_date="2026-06-15", output_dir=tmp_path)

    assert str(exc_info.value) == "snapshot_date precedes latest recorded snapshot"
    assert provider_calls == []
    assert (timeline_path.read_bytes(), manifest_path.read_bytes()) == old_pair


def test_removed_member_can_be_reincluded_as_a_new_non_overlapping_segment(
    tmp_path: Path,
    monkeypatch,
):
    capture_snapshot("000905", snapshot_date="2026-06-15", output_dir=tmp_path)
    monkeypatch.setattr(
        index_membership,
        "fetch_current_constituents",
        lambda index_code: pd.DataFrame({"symbol": ["600519"]}),
    )
    capture_snapshot("000905", snapshot_date="2026-07-01", output_dir=tmp_path)
    monkeypatch.setattr(
        index_membership,
        "fetch_current_constituents",
        lambda index_code: pd.DataFrame({"symbol": ["600519", "000001"]}),
    )
    result = capture_snapshot("000905", snapshot_date="2026-08-01", output_dir=tmp_path)

    timeline_path = tmp_path / TIMELINE_FILENAME_TEMPLATE.format(index_code="000905")
    timeline = pd.read_csv(timeline_path, dtype=str)
    assert list(timeline.columns) == [
        "symbol",
        "index_code",
        "first_seen_date",
        "last_seen_date",
        "inclusion_date",
        "removed_date",
    ]
    segments = timeline.loc[timeline["symbol"] == "000001"]
    assert segments[["first_seen_date", "last_seen_date", "removed_date"]].fillna("").to_dict(
        orient="records"
    ) == [
        {
            "first_seen_date": "2026-06-15",
            "last_seen_date": "2026-06-15",
            "removed_date": "2026-07-01",
        },
        {
            "first_seen_date": "2026-08-01",
            "last_seen_date": "2026-08-01",
            "removed_date": "",
        },
    ]
    assert "000001" in constituents_as_of("000905", "2026-06-30", snapshot_dir=tmp_path)
    assert "000001" not in constituents_as_of("000905", "2026-07-01", snapshot_dir=tmp_path)
    assert "000001" not in constituents_as_of("000905", "2026-07-31", snapshot_dir=tmp_path)
    assert "000001" in constituents_as_of("000905", "2026-08-01", snapshot_dir=tmp_path)
    assert result["new_symbols"] == 0
    assert result["reintroduced_symbols"] == 1
    manifest = json.loads(Path(result["manifest_path"]).read_text(encoding="utf-8"))
    assert manifest["changes"] == ["000001"]
    assert manifest["removed"] == []


def test_manifest_commit_failure_rolls_back_to_old_readable_pair(tmp_path: Path, monkeypatch):
    first = capture_snapshot("000905", snapshot_date="2026-06-15", output_dir=tmp_path)
    timeline_path = Path(first["timeline_path"])
    manifest_path = Path(first["manifest_path"])
    old_pair = (timeline_path.read_bytes(), manifest_path.read_bytes())
    real_replace = index_membership.os.replace
    real_mkstemp = index_membership.tempfile.mkstemp
    commit_started = False

    def reject_staging_after_commit_starts(*args, **kwargs):
        if commit_started:
            pytest.fail("rollback must use the pre-staged timeline backup")
        return real_mkstemp(*args, **kwargs)

    def fail_manifest_commit(source, destination):
        nonlocal commit_started
        commit_started = True
        if Path(destination) == manifest_path:
            raise OSError("simulated manifest commit failure")
        return real_replace(source, destination)

    monkeypatch.setattr(index_membership.tempfile, "mkstemp", reject_staging_after_commit_starts)
    monkeypatch.setattr(index_membership.os, "replace", fail_manifest_commit)

    with pytest.raises(OSError, match="manifest commit"):
        capture_snapshot("000905", snapshot_date="2026-06-21", output_dir=tmp_path)

    assert (timeline_path.read_bytes(), manifest_path.read_bytes()) == old_pair
    assert not load_membership_timeline("000905", snapshot_dir=tmp_path).empty
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    assert manifest["timeline_sha256"] == hashlib.sha256(timeline_path.read_bytes()).hexdigest()


@pytest.mark.parametrize("interrupt_type", [KeyboardInterrupt, SystemExit])
def test_manifest_commit_interrupt_rolls_back_and_reraises_original(
    tmp_path: Path,
    monkeypatch,
    interrupt_type,
):
    first = capture_snapshot("000905", snapshot_date="2026-06-15", output_dir=tmp_path)
    timeline_path = Path(first["timeline_path"])
    manifest_path = Path(first["manifest_path"])
    old_pair = (timeline_path.read_bytes(), manifest_path.read_bytes())
    real_replace = index_membership.os.replace
    interruption = interrupt_type("simulated manifest commit interruption")

    def interrupt_manifest_commit(source, destination):
        if Path(destination) == manifest_path:
            raise interruption
        return real_replace(source, destination)

    monkeypatch.setattr(index_membership.os, "replace", interrupt_manifest_commit)

    with pytest.raises(interrupt_type) as exc_info:
        capture_snapshot("000905", snapshot_date="2026-06-21", output_dir=tmp_path)

    assert exc_info.value is interruption
    assert (timeline_path.read_bytes(), manifest_path.read_bytes()) == old_pair
    assert not load_membership_timeline("000905", snapshot_dir=tmp_path).empty
    assert list(tmp_path.glob(".*.tmp")) == []
    assert list(tmp_path.glob(".*.bak")) == []


def test_manifest_commit_interrupt_preserves_backup_when_rollback_fails(
    tmp_path: Path,
    monkeypatch,
):
    first = capture_snapshot("000905", snapshot_date="2026-06-15", output_dir=tmp_path)
    timeline_path = Path(first["timeline_path"])
    manifest_path = Path(first["manifest_path"])
    old_pair = (timeline_path.read_bytes(), manifest_path.read_bytes())
    real_replace = index_membership.os.replace
    interruption = KeyboardInterrupt("simulated manifest commit interruption")
    timeline_replace_count = 0

    def interrupt_manifest_commit_and_fail_rollback(source, destination):
        nonlocal timeline_replace_count
        destination = Path(destination)
        if destination == timeline_path:
            timeline_replace_count += 1
            if timeline_replace_count == 2:
                raise OSError("simulated timeline rollback failure")
        elif destination == manifest_path:
            raise interruption
        return real_replace(source, destination)

    monkeypatch.setattr(
        index_membership.os,
        "replace",
        interrupt_manifest_commit_and_fail_rollback,
    )

    with pytest.raises(KeyboardInterrupt) as exc_info:
        capture_snapshot("000905", snapshot_date="2026-06-21", output_dir=tmp_path)

    assert exc_info.value is interruption
    backups = list(tmp_path.glob(f".{timeline_path.name}.recovery-backup.*.bak"))
    assert len(backups) == 1
    assert backups[0].read_bytes() == old_pair[0]
    assert manifest_path.read_bytes() == old_pair[1]
    assert list(tmp_path.glob(".*.tmp")) == []

    real_replace(backups[0], timeline_path)
    assert (timeline_path.read_bytes(), manifest_path.read_bytes()) == old_pair
    assert not load_membership_timeline("000905", snapshot_dir=tmp_path).empty


@pytest.mark.parametrize("failed_artifact", ["timeline", "manifest"])
def test_old_pair_read_failure_cleans_staged_files_without_changing_pair(
    tmp_path: Path,
    monkeypatch,
    failed_artifact: str,
):
    first = capture_snapshot("000905", snapshot_date="2026-06-15", output_dir=tmp_path)
    timeline_path = Path(first["timeline_path"])
    manifest_path = Path(first["manifest_path"])
    real_read_bytes = Path.read_bytes
    old_pair = (real_read_bytes(timeline_path), real_read_bytes(manifest_path))
    failed_path = timeline_path if failed_artifact == "timeline" else manifest_path

    def fail_selected_old_artifact(path):
        if path == failed_path:
            raise OSError(f"simulated old {failed_artifact} read failure")
        return real_read_bytes(path)

    monkeypatch.setattr(Path, "read_bytes", fail_selected_old_artifact)

    with pytest.raises(OSError, match=f"old {failed_artifact} read"):
        index_membership._write_timeline_pair_best_effort(
            timeline_path,
            manifest_path,
            b"new timeline bytes",
            b"new manifest bytes",
        )

    assert (real_read_bytes(timeline_path), real_read_bytes(manifest_path)) == old_pair
    assert list(tmp_path.glob(".*.tmp")) == []
    assert list(tmp_path.glob(".*.bak")) == []


def test_failed_manifest_commit_and_rollback_preserves_manual_recovery_backup(
    tmp_path: Path,
    monkeypatch,
):
    first = capture_snapshot("000905", snapshot_date="2026-06-15", output_dir=tmp_path)
    timeline_path = Path(first["timeline_path"])
    manifest_path = Path(first["manifest_path"])
    old_pair = (timeline_path.read_bytes(), manifest_path.read_bytes())
    real_replace = index_membership.os.replace
    timeline_replace_count = 0

    def fail_manifest_commit_and_timeline_rollback(source, destination):
        nonlocal timeline_replace_count
        destination = Path(destination)
        if destination == timeline_path:
            timeline_replace_count += 1
            if timeline_replace_count == 2:
                raise OSError("simulated timeline rollback failure")
        elif destination == manifest_path:
            raise OSError("simulated manifest commit failure")
        return real_replace(source, destination)

    monkeypatch.setattr(
        index_membership.os,
        "replace",
        fail_manifest_commit_and_timeline_rollback,
    )

    with pytest.raises(
        RuntimeError,
        match=(
            "index membership pair commit failed and automatic rollback failed; "
            "recovery backup preserved"
        ),
    ):
        capture_snapshot("000905", snapshot_date="2026-06-21", output_dir=tmp_path)

    backups = list(
        tmp_path.glob(f".{timeline_path.name}.recovery-backup.*.bak")
    )
    assert len(backups) == 1
    assert backups[0].read_bytes() == old_pair[0]
    assert manifest_path.read_bytes() == old_pair[1]
    assert list(tmp_path.glob(".*.tmp")) == []

    real_replace(backups[0], timeline_path)
    assert (timeline_path.read_bytes(), manifest_path.read_bytes()) == old_pair
    assert not load_membership_timeline("000905", snapshot_dir=tmp_path).empty


def test_successful_pair_commit_leaves_no_staging_or_backup_files(tmp_path: Path):
    capture_snapshot("000905", snapshot_date="2026-06-15", output_dir=tmp_path)
    capture_snapshot("000905", snapshot_date="2026-06-21", output_dir=tmp_path)

    assert list(tmp_path.glob(".*.tmp")) == []
    assert list(tmp_path.glob(".*.bak")) == []


@pytest.mark.parametrize(
    ("column", "value"),
    [
        ("symbol", "ABC"),
        ("index_code", "000300"),
        ("first_seen_date", "2026/06/15"),
        ("last_seen_date", "2026-06-14"),
        ("inclusion_date", "not-a-date"),
        ("removed_date", "2026-06-15"),
    ],
)
def test_capture_validates_complete_timeline_before_fetch(
    tmp_path: Path,
    monkeypatch,
    column: str,
    value: str,
):
    result = capture_snapshot("000905", snapshot_date="2026-06-15", output_dir=tmp_path)
    timeline_path = Path(result["timeline_path"])
    manifest_path = Path(result["manifest_path"])
    timeline = pd.read_csv(timeline_path, dtype=str)
    timeline.loc[0, column] = value
    timeline.to_csv(timeline_path, index=False)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["timeline_sha256"] = hashlib.sha256(timeline_path.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(
        index_membership,
        "fetch_current_constituents",
        lambda index_code: pytest.fail("invalid timeline must fail before fetch"),
    )
    monkeypatch.setattr(
        index_membership,
        "fetch_inclusion_dates",
        lambda index_code: pytest.fail("invalid timeline must fail before fetch"),
    )

    with pytest.raises(ValueError, match="timeline"):
        capture_snapshot("000905", snapshot_date="2026-06-21", output_dir=tmp_path)


def test_capture_rejects_non_six_column_schema_before_fetch(tmp_path: Path, monkeypatch):
    result = capture_snapshot("000905", snapshot_date="2026-06-15", output_dir=tmp_path)
    timeline_path = Path(result["timeline_path"])
    manifest_path = Path(result["manifest_path"])
    timeline = pd.read_csv(timeline_path, dtype=str).drop(columns="inclusion_date")
    timeline.to_csv(timeline_path, index=False)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["timeline_sha256"] = hashlib.sha256(timeline_path.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(
        index_membership,
        "fetch_current_constituents",
        lambda index_code: pytest.fail("bad schema must fail before fetch"),
    )
    monkeypatch.setattr(
        index_membership,
        "fetch_inclusion_dates",
        lambda index_code: pytest.fail("bad schema must fail before fetch"),
    )

    with pytest.raises(ValueError, match="six-column schema"):
        capture_snapshot("000905", snapshot_date="2026-06-21", output_dir=tmp_path)


def test_capture_rejects_overlapping_segments_before_fetch(tmp_path: Path, monkeypatch):
    result = capture_snapshot("000905", snapshot_date="2026-06-15", output_dir=tmp_path)
    timeline_path = Path(result["timeline_path"])
    manifest_path = Path(result["manifest_path"])
    timeline = pd.read_csv(timeline_path, dtype=str)
    duplicate = timeline.iloc[[0]].copy()
    duplicate["first_seen_date"] = "2026-06-16"
    duplicate["last_seen_date"] = "2026-06-16"
    timeline = pd.concat([timeline, duplicate], ignore_index=True)
    timeline.to_csv(timeline_path, index=False)
    manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    manifest["timeline_sha256"] = hashlib.sha256(timeline_path.read_bytes()).hexdigest()
    manifest_path.write_text(json.dumps(manifest), encoding="utf-8")
    monkeypatch.setattr(
        index_membership,
        "fetch_current_constituents",
        lambda index_code: pytest.fail("invalid intervals must fail before fetch"),
    )

    with pytest.raises(ValueError, match="timeline"):
        capture_snapshot("000905", snapshot_date="2026-06-21", output_dir=tmp_path)
