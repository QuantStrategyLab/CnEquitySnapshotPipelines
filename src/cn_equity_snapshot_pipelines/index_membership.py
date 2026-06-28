"""Index membership snapshot pipeline — periodic constituent capture for PIT reconstruction.

Captures point-in-time index constituent lists from akshare and builds
an accumulated membership timeline CSV. The timeline feeds the PIT filter
in CnEquityStrategies to reduce survivorship bias in historical backtests.

Limitation
----------
akshare returns only current index constituents (with inclusion dates).
Removed historical members are not available retroactively. This pipeline
starts NOW and builds the timeline forward. For historical periods before
the first snapshot, fall back to inclusion-date + price-history grandfathering
(see CnEquityStrategies.research.momentum_stock_universe.filter_offensive_for_pit).

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
from datetime import datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd


DEFAULT_SNAPSHOT_DIR = Path(__file__).resolve().parents[2] / "data" / "index_membership"


TIMELINE_FILENAME_TEMPLATE = "cn_{index_code}_membership_timeline.csv"


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
    output_dir = Path(output_dir or DEFAULT_SNAPSHOT_DIR)
    output_dir.mkdir(parents=True, exist_ok=True)
    as_of = snapshot_date or datetime.now(timezone.utc).date().isoformat()

    constituents = fetch_current_constituents(index_code)
    inclusion_map = fetch_inclusion_dates(index_code)

    timeline_path = output_dir / TIMELINE_FILENAME_TEMPLATE.format(index_code=index_code)
    existing: pd.DataFrame = (
        pd.read_csv(timeline_path, dtype=str) if timeline_path.exists() else pd.DataFrame()
    )

    seen: set[str] = set()
    if not existing.empty:
        seen = set(existing["symbol"].unique())

    new_rows: list[dict[str, str]] = []
    for symbol in constituents["symbol"].unique():
        normalized = normalize_symbol(symbol)
        if not normalized:
            continue
        incl_date = inclusion_map.get(normalized, "")
        if normalized in seen:
            # Update last_seen_date for existing members
            mask = existing["symbol"] == normalized
            existing.loc[mask, "last_seen_date"] = as_of
            if incl_date and not existing.loc[mask, "inclusion_date"].iloc[0]:
                existing.loc[mask, "inclusion_date"] = incl_date
        else:
            new_rows.append(
                {
                    "symbol": normalized,
                    "index_code": str(index_code),
                    "first_seen_date": as_of,
                    "last_seen_date": as_of,
                    "inclusion_date": incl_date,
                    "removed_date": "",
                }
            )

    # Detect removals: symbols that were in previous snapshots but are
    # absent this time. Mark their removed_date.
    newly_removed: set[str] = set()
    current_symbols = set(constituents["symbol"].unique())
    if not existing.empty:
        previously_active = set(
            existing.loc[
                existing["removed_date"].isna() | (existing["removed_date"] == ""), "symbol"
            ]
        )
        newly_removed = previously_active - current_symbols
        if newly_removed:
            existing.loc[
                existing["symbol"].isin(newly_removed)
                & ((existing["removed_date"].isna()) | (existing["removed_date"] == "")),
                "removed_date",
            ] = as_of

    updated = pd.concat(
        [existing] + ([pd.DataFrame(new_rows)] if new_rows else []),
        ignore_index=True,
    )
    updated = updated.drop_duplicates(subset=["symbol", "index_code"], keep="last")
    updated = updated.sort_values(["symbol", "last_seen_date"]).reset_index(drop=True)
    updated.to_csv(timeline_path, index=False)

    return {
        "index_code": str(index_code),
        "as_of": as_of,
        "constituent_count": int(len(constituents)),
        "new_symbols": len(new_rows),
        "newly_removed": len(newly_removed),
        "timeline_path": str(timeline_path),
        "total_tracked": int(len(updated)),
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
    snapshot_dir = Path(snapshot_dir or DEFAULT_SNAPSHOT_DIR)
    timeline_path = snapshot_dir / TIMELINE_FILENAME_TEMPLATE.format(index_code=index_code)
    if not timeline_path.exists():
        return pd.DataFrame(columns=["symbol", "index_code", "first_seen_date", "last_seen_date", "inclusion_date", "removed_date"])
    frame = pd.read_csv(timeline_path, dtype=str)
    for col in ("first_seen_date", "last_seen_date", "inclusion_date", "removed_date"):
        if col in frame.columns:
            frame[col] = frame[col].replace({pd.NA: None, "nan": None, "": None})
    return frame


def constituents_as_of(
    index_code: str,
    as_of: str,
    *,
    snapshot_dir: str | Path | None = None,
    fallback_to_inclusion_table: bool = True,
) -> tuple[str, ...]:
    """Return index constituents as of a given date.

    Uses the accumulated membership timeline if available. Falls back to
    inclusion-date PIT filtering when the timeline does not cover the as_of
    date (i.e. the first snapshot was taken after the as_of date).

    Returns a tuple of 6-digit symbol strings sorted alphabetically.
    """
    as_of_ts = pd.Timestamp(as_of).normalize()
    timeline = load_membership_timeline(index_code, snapshot_dir=snapshot_dir)

    if not timeline.empty:
        first_snapshot = timeline["first_seen_date"].dropna().min()
        if first_snapshot and pd.Timestamp(first_snapshot).normalize() <= as_of_ts:
            # Timeline covers this date
            mask = (
                (timeline["last_seen_date"].fillna("9999-12-31").astype(str) >= as_of)
                & (timeline["first_seen_date"].astype(str) <= as_of)
                & (
                    timeline["removed_date"].isna()
                    | (timeline["removed_date"] == "")
                    | (timeline["removed_date"].astype(str) > as_of)
                )
            )
            members = tuple(
                sorted(
                    timeline.loc[mask, "symbol"].unique().tolist()
                )
            )
            if members:
                return members

    # Fallback: use inclusion-date table from akshare (current members only)
    if fallback_to_inclusion_table:
        from cn_equity_strategies.research.momentum_stock_universe import index_constituents_as_of

        return index_constituents_as_of(str(index_code), as_of)

    return ()


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
