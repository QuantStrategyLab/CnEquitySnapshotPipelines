"""Index membership snapshot pipeline — periodic constituent capture for PIT reconstruction.

Captures point-in-time index constituent lists from akshare and builds
an accumulated membership timeline CSV. The timeline feeds the PIT filter
in CnEquityStrategies to reduce survivorship bias in historical backtests.

Limitation
----------
akshare returns only current index constituents (with inclusion dates).
Removed historical members are not available retroactively. This pipeline
starts NOW and builds the timeline forward. Queries before the first snapshot
fail closed because current-member data cannot prove historical membership.
The timeline and manifest are separate files: writes are best effort rather
than crash-atomic, and readers fail closed when their hash-bound pair differs.

Timeline CSV schema
-------------------
symbol: str — 6-digit A-share security code
index_code: str — e.g. "000905" (CSI500)
first_seen_date: str — date this symbol first appeared in a snapshot (YYYY-MM-DD)
last_seen_date: str — date this symbol was last confirmed as a constituent
inclusion_date: str or empty — CSIndex published inclusion date (if available)
removed_date: str or empty — date this symbol was absent after being seen
"""

from __future__ import annotations

import argparse
import hashlib
import io
import json
import os
import re
import tempfile
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


DEFAULT_SNAPSHOT_DIR = Path(__file__).resolve().parents[2] / "data" / "index_membership"


TIMELINE_FILENAME_TEMPLATE = "cn_{index_code}_membership_timeline.csv"
MANIFEST_FILENAME_TEMPLATE = TIMELINE_FILENAME_TEMPLATE + ".manifest.json"
TIMELINE_COLUMNS = [
    "symbol",
    "index_code",
    "first_seen_date",
    "last_seen_date",
    "inclusion_date",
    "removed_date",
]

SOURCE_AS_KNOWN_SEMANTICS = "forward_accumulation_from_first_snapshot"
HISTORICAL_REMOVED_MEMBERS_UNPROVEN = "HISTORICAL_REMOVED_MEMBERS_UNPROVEN"
NO_PRIOR_SNAPSHOT = "NO_PRIOR_SNAPSHOT"
AS_OF_BEFORE_COVERAGE_START = "AS_OF_BEFORE_COVERAGE_START"
AS_OF_AFTER_COVERAGE_END = "AS_OF_AFTER_COVERAGE_END"
TIMELINE_NOT_FOUND = "TIMELINE_NOT_FOUND"


def _sha256(data: bytes) -> str:
    return hashlib.sha256(data).hexdigest()


def _manifest_path(output_dir: Path, index_code: str) -> Path:
    return output_dir / MANIFEST_FILENAME_TEMPLATE.format(index_code=index_code)


def _parse_iso_date(value: object, field: str, *, optional: bool = False) -> str | None:
    if value is None or pd.isna(value) or (optional and value == ""):
        if optional:
            return None
        raise ValueError(f"invalid index membership timeline: missing {field}")
    text = str(value)
    if not re.fullmatch(r"\d{4}-\d{2}-\d{2}", text):
        raise ValueError(f"invalid index membership timeline: invalid {field}")
    try:
        parsed = date.fromisoformat(text)
    except ValueError as exc:
        raise ValueError(f"invalid index membership timeline: invalid {field}") from exc
    if parsed.isoformat() != text:
        raise ValueError(f"invalid index membership timeline: invalid {field}")
    return text


def _validate_index_code(index_code: object) -> str:
    value = str(index_code)
    if not re.fullmatch(r"\d{6}", value):
        raise ValueError("index_code must be a 6-digit string")
    return value


def _validate_symbol(value: object, *, field: str = "symbol") -> str:
    symbol = str(value)
    if not re.fullmatch(r"\d{6}", symbol):
        raise ValueError(f"invalid index membership timeline: invalid {field}")
    return symbol


def _validate_timeline(frame: pd.DataFrame, index_code: str) -> None:
    if list(frame.columns) != TIMELINE_COLUMNS or frame.empty:
        raise ValueError("invalid index membership timeline: expected six-column schema")

    intervals: dict[str, list[tuple[str, str | None]]] = {}
    for row in frame.itertuples(index=False):
        symbol = _validate_symbol(row.symbol)
        if str(row.index_code) != index_code:
            raise ValueError("invalid index membership timeline: index_code mismatch")
        first_seen = _parse_iso_date(row.first_seen_date, "first_seen_date")
        last_seen = _parse_iso_date(row.last_seen_date, "last_seen_date")
        _parse_iso_date(row.inclusion_date, "inclusion_date", optional=True)
        removed = _parse_iso_date(row.removed_date, "removed_date", optional=True)
        if first_seen > last_seen:
            raise ValueError("invalid index membership timeline: first_seen_date after last_seen_date")
        if removed is not None and removed <= last_seen:
            raise ValueError("invalid index membership timeline: removed_date must follow last_seen_date")
        intervals.setdefault(symbol, []).append((first_seen, removed))

    for symbol_intervals in intervals.values():
        previous_removed: str | None = None
        for position, (first_seen, removed) in enumerate(sorted(symbol_intervals)):
            if position and (previous_removed is None or first_seen < previous_removed):
                raise ValueError("invalid index membership timeline: overlapping membership intervals")
            previous_removed = removed


def _read_manifest(
    timeline_path: Path,
    index_code: str,
    *,
    timeline: pd.DataFrame,
    timeline_bytes: bytes,
) -> dict[str, Any]:
    manifest_path = _manifest_path(timeline_path.parent, index_code)
    try:
        manifest = json.loads(manifest_path.read_text(encoding="utf-8"))
    except (FileNotFoundError, UnicodeDecodeError, json.JSONDecodeError) as exc:
        raise ValueError(f"invalid index membership manifest for {index_code}") from exc

    required = {
        "manifest_type",
        "index_code",
        "coverage_start",
        "historical_removed_members_complete",
        "source_as_known_semantics",
        "comparison_status",
        "reason_codes",
        "changes",
        "removed",
        "timeline_sha256",
    }
    if not isinstance(manifest, dict) or not required.issubset(manifest):
        raise ValueError(f"invalid index membership manifest for {index_code}")
    coverage_start = timeline["first_seen_date"].min()
    _parse_iso_date(manifest["coverage_start"], "manifest coverage_start")
    has_coverage_end = "coverage_end" in manifest
    has_captured_through = "captured_through" in manifest
    if has_coverage_end != has_captured_through:
        raise ValueError(f"invalid index membership manifest for {index_code}")
    if has_coverage_end:
        coverage_end = _parse_iso_date(manifest["coverage_end"], "manifest coverage_end")
        captured_through = _parse_iso_date(manifest["captured_through"], "manifest captured_through")
        assert coverage_end is not None and captured_through is not None
    else:
        coverage_end = max(
            value
            for column in ("first_seen_date", "last_seen_date", "removed_date")
            for value in timeline[column].dropna().astype(str)
        )
        captured_through = coverage_end
    changes = manifest["changes"]
    removed = manifest["removed"]
    if not isinstance(changes, list) or not isinstance(removed, list):
        raise ValueError(f"invalid index membership manifest for {index_code}")
    try:
        validated_changes = [_validate_symbol(value, field="manifest changes") for value in changes]
        validated_removed = [_validate_symbol(value, field="manifest removed") for value in removed]
    except ValueError as exc:
        raise ValueError(f"invalid index membership manifest for {index_code}") from exc
    if (
        validated_changes != sorted(set(validated_changes))
        or validated_removed != sorted(set(validated_removed))
    ):
        raise ValueError(f"invalid index membership manifest for {index_code}")
    valid_comparison = (
        (
            manifest["comparison_status"] == "incomparable"
            and manifest["reason_codes"]
            == [HISTORICAL_REMOVED_MEMBERS_UNPROVEN, NO_PRIOR_SNAPSHOT]
            and changes == []
            and removed == []
        )
        or (
            manifest["comparison_status"] == "comparable"
            and manifest["reason_codes"] == [HISTORICAL_REMOVED_MEMBERS_UNPROVEN]
        )
    )
    if (
        manifest["manifest_type"] != "index_membership_timeline"
        or manifest["index_code"] != str(index_code)
        or manifest["coverage_start"] != coverage_start
        or coverage_end < coverage_start
        or captured_through != coverage_end
        or manifest["historical_removed_members_complete"] is not False
        or manifest["source_as_known_semantics"] != SOURCE_AS_KNOWN_SEMANTICS
        or manifest["timeline_sha256"] != _sha256(timeline_bytes)
        or not valid_comparison
    ):
        raise ValueError(f"invalid index membership manifest for {index_code}")
    return {
        **manifest,
        "coverage_end": coverage_end,
        "captured_through": captured_through,
    }


def _manifest_bytes(
    *,
    timeline_bytes: bytes,
    index_code: str,
    coverage_start: str,
    coverage_end: str,
    captured_through: str,
    comparison_status: str,
    reason_codes: list[str],
    changes: list[str],
    removed: list[str],
) -> bytes:
    return (
        json.dumps(
            {
                "manifest_type": "index_membership_timeline",
                "index_code": index_code,
                "coverage_start": coverage_start,
                "coverage_end": coverage_end,
                "captured_through": captured_through,
                "historical_removed_members_complete": False,
                "source_as_known_semantics": SOURCE_AS_KNOWN_SEMANTICS,
                "comparison_status": comparison_status,
                "reason_codes": reason_codes,
                "changes": changes,
                "removed": removed,
                "timeline_sha256": _sha256(timeline_bytes),
            },
            ensure_ascii=False,
            indent=2,
            sort_keys=True,
        )
        + "\n"
    ).encode("utf-8")


def _stage_bytes(destination: Path, data: bytes, *, recovery_backup: bool = False) -> Path:
    descriptor, name = tempfile.mkstemp(
        prefix=(
            f".{destination.name}.recovery-backup."
            if recovery_backup
            else f".{destination.name}."
        ),
        suffix=".bak" if recovery_backup else ".tmp",
        dir=destination.parent,
    )
    try:
        with os.fdopen(descriptor, "wb") as handle:
            handle.write(data)
            handle.flush()
            os.fsync(handle.fileno())
    except Exception:
        Path(name).unlink(missing_ok=True)
        raise
    return Path(name)


def _write_timeline_pair_best_effort(
    timeline_path: Path,
    manifest_path: Path,
    timeline_bytes: bytes,
    manifest_bytes: bytes,
) -> None:
    """Stage and replace the two files with rollback, but not crash atomicity.

    Filesystems cannot atomically replace both independent paths. A process
    crash between replacements can therefore leave a mismatched pair; readers
    fail closed because they require both files and validate timeline_sha256.
    """
    staged_timeline = _stage_bytes(timeline_path, timeline_bytes)
    staged_manifest: Path | None = None
    recovery_backup: Path | None = None
    preserve_recovery_backup = False
    try:
        staged_manifest = _stage_bytes(manifest_path, manifest_bytes)
        old_timeline = timeline_path.read_bytes() if timeline_path.exists() else None
        if old_timeline is not None:
            manifest_path.read_bytes()
            recovery_backup = _stage_bytes(
                timeline_path,
                old_timeline,
                recovery_backup=True,
            )
        os.replace(staged_timeline, timeline_path)
        try:
            os.replace(staged_manifest, manifest_path)
        except BaseException as commit_error:
            if old_timeline is None:
                timeline_path.unlink(missing_ok=True)
            else:
                rollback_error: BaseException | None = None
                try:
                    assert recovery_backup is not None
                    os.replace(recovery_backup, timeline_path)
                except BaseException as exc:
                    preserve_recovery_backup = True
                    rollback_error = exc
                if rollback_error is not None:
                    if isinstance(commit_error, (KeyboardInterrupt, SystemExit)):
                        raise
                    if isinstance(rollback_error, (KeyboardInterrupt, SystemExit)):
                        raise rollback_error
                    raise RuntimeError(
                        "index membership pair commit failed and automatic rollback failed; "
                        "recovery backup preserved"
                    ) from None
            raise
    finally:
        staged_timeline.unlink(missing_ok=True)
        if staged_manifest is not None:
            staged_manifest.unlink(missing_ok=True)
        if recovery_backup is not None and not preserve_recovery_backup:
            recovery_backup.unlink(missing_ok=True)


def _load_timeline_pair(
    index_code: str,
    snapshot_dir: Path,
) -> tuple[pd.DataFrame, dict[str, Any] | None]:
    timeline_path = snapshot_dir / TIMELINE_FILENAME_TEMPLATE.format(index_code=index_code)
    manifest_path = _manifest_path(snapshot_dir, index_code)
    if timeline_path.exists() != manifest_path.exists():
        raise ValueError(f"invalid index membership manifest artifact pair for {index_code}")
    if not timeline_path.exists():
        return pd.DataFrame(columns=TIMELINE_COLUMNS), None
    try:
        timeline_bytes = timeline_path.read_bytes()
        timeline = pd.read_csv(io.BytesIO(timeline_bytes), dtype=str)
    except (OSError, UnicodeDecodeError, pd.errors.ParserError, pd.errors.EmptyDataError) as exc:
        raise ValueError(f"invalid index membership timeline for {index_code}") from exc
    _validate_timeline(timeline, index_code)
    manifest = _read_manifest(
        timeline_path,
        index_code,
        timeline=timeline,
        timeline_bytes=timeline_bytes,
    )
    return timeline, manifest


def normalize_symbol(value: object) -> str:
    text = str(value or "").strip().upper()
    if "." in text:
        text = text.split(".", 1)[0]
    return text.zfill(6) if text.isdigit() else text


def _import_akshare():
    import akshare as ak

    return ak


def fetch_current_constituents(index_code: str) -> pd.DataFrame:
    """Fetch current index constituents via akshare.

    Returns a DataFrame with columns: [symbol, name, inclusion_date]
    """
    ak = _import_akshare()
    frame = ak.index_stock_cons_csindex(symbol=str(index_code))
    if frame is None or frame.empty:
        raise RuntimeError(f"index_stock_cons_csindex returned no data for {index_code}")
    column = "成分券代码"
    if column not in frame.columns:
        raise RuntimeError(f"missing {column} in index constituents for {index_code}")
    result = pd.DataFrame(
        {
            "symbol": frame[column].map(normalize_symbol),
        }
    )
    return result


def fetch_inclusion_dates(index_code: str) -> dict[str, str]:
    """Fetch CSIndex published inclusion dates for current members.

    Returns {symbol: inclusion_date} mapping. Only current members are
    included — removed members are not available via akshare.
    """
    ak = _import_akshare()
    try:
        frame = ak.index_stock_cons(symbol=str(index_code))
    except Exception:
        return {}
    if frame is None or frame.empty:
        return {}
    code_col = "品种代码"
    date_col = "纳入日期"
    if code_col not in frame.columns or date_col not in frame.columns:
        return {}
    working = frame.copy()
    working["symbol"] = working[code_col].map(normalize_symbol)
    working["inclusion_date"] = pd.to_datetime(working[date_col], errors="coerce").dt.date.astype(str)
    working = working.loc[working["inclusion_date"] != "NaT"]
    # Keep the earliest inclusion per symbol (first time added)
    earliest = (
        working.loc[working["inclusion_date"].notna()]
        .groupby("symbol")["inclusion_date"]
        .min()
        .to_dict()
    )
    return earliest


def capture_snapshot(
    index_code: str,
    *,
    snapshot_date: str | None = None,
    output_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Capture a single point-in-time snapshot of index constituents.

    Appends to the accumulated timeline CSV. If the timeline file does not
    exist yet, creates it from scratch.
    """
    index_code = _validate_index_code(index_code)
    as_of = _parse_iso_date(
        snapshot_date or datetime.now(timezone.utc).date().isoformat(),
        "snapshot_date",
    )
    assert as_of is not None
    output_dir = Path(output_dir or DEFAULT_SNAPSHOT_DIR)
    output_dir.mkdir(parents=True, exist_ok=True)
    timeline_path = output_dir / TIMELINE_FILENAME_TEMPLATE.format(index_code=index_code)
    manifest_path = _manifest_path(output_dir, index_code)
    existing, existing_manifest = _load_timeline_pair(index_code, output_dir)
    if existing_manifest is not None:
        coverage_start = existing_manifest["coverage_start"]
        latest_snapshot = max(
            value
            for column in ("first_seen_date", "last_seen_date", "removed_date")
            for value in existing[column].dropna().astype(str)
        )
        if as_of < coverage_start:
            raise ValueError("snapshot_date precedes coverage_start")
        if as_of <= latest_snapshot:
            raise ValueError("snapshot_date precedes latest recorded snapshot")

    constituents = fetch_current_constituents(index_code)
    inclusion_map = fetch_inclusion_dates(index_code)
    if list(constituents.columns) != ["symbol"] or constituents.empty:
        raise ValueError("invalid current index constituents")
    current_symbols = {_validate_symbol(normalize_symbol(value)) for value in constituents["symbol"]}
    normalized_inclusion_map: dict[str, str] = {}
    for symbol, inclusion_date in inclusion_map.items():
        normalized_symbol = _validate_symbol(normalize_symbol(symbol), field="inclusion symbol")
        parsed_inclusion_date = _parse_iso_date(inclusion_date, "inclusion_date")
        assert parsed_inclusion_date is not None
        normalized_inclusion_map[normalized_symbol] = parsed_inclusion_date

    new_rows: list[dict[str, str]] = []
    seen_symbols = set(existing["symbol"])
    active_mask = existing["removed_date"].isna() | (existing["removed_date"] == "")
    active_symbols = set(existing.loc[active_mask, "symbol"])
    for symbol in sorted(current_symbols):
        inclusion_date = normalized_inclusion_map.get(symbol, "")
        if symbol in active_symbols:
            mask = active_mask & (existing["symbol"] == symbol)
            existing.loc[mask, "last_seen_date"] = as_of
            if inclusion_date and existing.loc[mask, "inclusion_date"].isna().all():
                existing.loc[mask, "inclusion_date"] = inclusion_date
        else:
            new_rows.append(
                {
                    "symbol": symbol,
                    "index_code": index_code,
                    "first_seen_date": as_of,
                    "last_seen_date": as_of,
                    "inclusion_date": inclusion_date,
                    "removed_date": "",
                }
            )

    # Detect removals: symbols that were in previous snapshots but are
    # absent this time. Mark their removed_date.
    newly_removed = active_symbols - current_symbols
    if newly_removed:
        removal_mask = active_mask & existing["symbol"].isin(newly_removed)
        if (existing.loc[removal_mask, "last_seen_date"] >= as_of).any():
            raise ValueError("snapshot_date must follow last_seen_date when membership changes")
        existing.loc[removal_mask, "removed_date"] = as_of

    updated = pd.concat(
        [existing] + ([pd.DataFrame(new_rows)] if new_rows else []),
        ignore_index=True,
    )
    updated = updated[TIMELINE_COLUMNS]
    updated = updated.sort_values(["symbol", "first_seen_date"]).reset_index(drop=True)
    _validate_timeline(updated, index_code)
    timeline_bytes = updated.to_csv(index=False, lineterminator="\n").encode("utf-8")
    coverage_start = (
        existing_manifest["coverage_start"] if existing_manifest is not None else as_of
    )
    coverage_end = as_of
    captured_through = as_of
    comparison_status = "comparable" if existing_manifest is not None else "incomparable"
    reason_codes = [HISTORICAL_REMOVED_MEMBERS_UNPROVEN]
    if existing_manifest is None:
        reason_codes.append(NO_PRIOR_SNAPSHOT)
    manifest_bytes = _manifest_bytes(
        timeline_bytes=timeline_bytes,
        index_code=index_code,
        coverage_start=coverage_start,
        coverage_end=coverage_end,
        captured_through=captured_through,
        comparison_status=comparison_status,
        reason_codes=reason_codes,
        changes=sorted(row["symbol"] for row in new_rows) if existing_manifest is not None else [],
        removed=sorted(newly_removed) if existing_manifest is not None else [],
    )
    _write_timeline_pair_best_effort(timeline_path, manifest_path, timeline_bytes, manifest_bytes)
    new_symbols = {row["symbol"] for row in new_rows} - seen_symbols
    reintroduced_symbols = {row["symbol"] for row in new_rows} & seen_symbols

    return {
        "index_code": index_code,
        "as_of": as_of,
        "constituent_count": int(len(constituents)),
        "new_symbols": len(new_symbols),
        "reintroduced_symbols": len(reintroduced_symbols),
        "newly_removed": len(newly_removed),
        "timeline_path": str(timeline_path),
        "manifest_path": str(manifest_path),
        "total_tracked": int(updated["symbol"].nunique()),
    }


def load_membership_timeline(
    index_code: str,
    *,
    snapshot_dir: str | Path | None = None,
) -> pd.DataFrame:
    """Load the accumulated membership timeline for an index.

    Returns columns: [symbol, index_code, first_seen_date, last_seen_date,
                      inclusion_date, removed_date]
    """
    index_code = _validate_index_code(index_code)
    snapshot_dir = Path(snapshot_dir or DEFAULT_SNAPSHOT_DIR)
    frame, _ = _load_timeline_pair(index_code, snapshot_dir)
    for col in ("first_seen_date", "last_seen_date", "inclusion_date", "removed_date"):
        if col in frame.columns:
            frame[col] = frame[col].replace({pd.NA: None, "nan": None, "": None})
    return frame


def query_constituents_as_of(
    index_code: str,
    as_of: str,
    *,
    snapshot_dir: str | Path | None = None,
) -> dict[str, Any]:
    """Return a JSON-serializable, fail-closed membership query result."""
    index_code = _validate_index_code(index_code)
    normalized_as_of = _parse_iso_date(as_of, "as_of")
    assert normalized_as_of is not None
    timeline, manifest = _load_timeline_pair(
        index_code,
        Path(snapshot_dir or DEFAULT_SNAPSHOT_DIR),
    )
    coverage_start = manifest["coverage_start"] if manifest is not None else None
    coverage_end = manifest["coverage_end"] if manifest is not None else None
    captured_through = manifest["captured_through"] if manifest is not None else None
    base_result: dict[str, Any] = {
        "index_code": index_code,
        "as_of": normalized_as_of,
        "coverage_start": coverage_start,
        "coverage_end": coverage_end,
        "captured_through": captured_through,
        "comparison_status": "incomparable",
        "members": [],
        "changes": [],
        "removed": [],
        "reason_codes": list(manifest["reason_codes"]) if manifest is not None else [],
    }
    if manifest is None:
        base_result["reason_codes"] = [TIMELINE_NOT_FOUND]
        return base_result
    if normalized_as_of < coverage_start:
        base_result["reason_codes"] = [AS_OF_BEFORE_COVERAGE_START, *base_result["reason_codes"]]
        return base_result
    if normalized_as_of > coverage_end:
        base_result["reason_codes"] = [AS_OF_AFTER_COVERAGE_END, *base_result["reason_codes"]]
        return base_result
    if manifest["comparison_status"] != "comparable":
        return base_result

    active = (timeline["first_seen_date"] <= normalized_as_of) & (
        timeline["removed_date"].isna() | (timeline["removed_date"] > normalized_as_of)
    )
    base_result.update(
        {
            "comparison_status": "comparable",
            "members": sorted(timeline.loc[active, "symbol"].unique().tolist()),
            "changes": sorted(
                timeline.loc[
                    (timeline["first_seen_date"] == normalized_as_of)
                    & (timeline["first_seen_date"] > coverage_start),
                    "symbol",
                ]
                .unique()
                .tolist()
            ),
            "removed": sorted(
                timeline.loc[timeline["removed_date"] == normalized_as_of, "symbol"]
                .unique()
                .tolist()
            ),
        }
    )
    return base_result


def constituents_as_of(
    index_code: str,
    as_of: str,
    *,
    snapshot_dir: str | Path | None = None,
    fallback_to_inclusion_table: bool = True,
) -> tuple[str, ...]:
    """Return index constituents as of a given date.

    Uses only the accumulated membership timeline. The fallback argument is
    retained for call compatibility but never queries current-member data.

    Returns a tuple of 6-digit symbol strings sorted alphabetically.
    """
    del fallback_to_inclusion_table
    result = query_constituents_as_of(index_code, as_of, snapshot_dir=snapshot_dir)
    return tuple(result["members"])


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Capture an index membership snapshot via AkShare."
    )
    parser.add_argument(
        "--index-code",
        default="000905",
        help="CSIndex code (default: 000905 for CSI500)",
    )
    parser.add_argument(
        "--snapshot-date",
        default=None,
        help="Override snapshot date (YYYY-MM-DD). Defaults to UTC today.",
    )
    parser.add_argument(
        "--output-dir",
        default=str(DEFAULT_SNAPSHOT_DIR),
    )
    args = parser.parse_args(argv)

    result = capture_snapshot(
        str(args.index_code),
        snapshot_date=args.snapshot_date,
        output_dir=args.output_dir,
    )
    print(f"Index {result['index_code']} snapshot {result['as_of']}")
    print(f"  Constituents: {result['constituent_count']}")
    print(f"  New: {result['new_symbols']}, Removed: {result['newly_removed']}")
    print(f"  Timeline: {result['timeline_path']}")
    print(f"  Total tracked: {result['total_tracked']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
