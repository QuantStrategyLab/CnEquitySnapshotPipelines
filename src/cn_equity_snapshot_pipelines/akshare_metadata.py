from __future__ import annotations

import json
import time
from collections.abc import Mapping
from datetime import date, datetime, timezone
from pathlib import Path
from typing import Any

import pandas as pd

from .akshare_enrichment import normalize_symbol

DEFAULT_SECTOR_CACHE_PATH = (
    Path(__file__).resolve().parents[2] / "data" / "cache" / "symbol_sector_map.json"
)


def _is_st_name(name: str) -> bool:
    text = str(name or "").upper()
    return "ST" in text or "*ST" in text


def select_dividend_universe_symbols(
    fhps_table: pd.DataFrame,
    *,
    min_dividend_yield: float = 0.025,
    max_dividend_yield: float = 0.12,
    top_n: int = 40,
    exclude_st: bool = True,
) -> tuple[str, ...]:
    if fhps_table.empty:
        return ()
    frame = fhps_table.copy()
    frame["symbol"] = frame["代码"].map(normalize_symbol)
    frame["name"] = frame["名称"].astype(str)
    frame["dividend_yield_ttm"] = pd.to_numeric(frame["现金分红-股息率"], errors="coerce")
    frame = frame.loc[frame["dividend_yield_ttm"].notna()]
    if exclude_st:
        frame = frame.loc[~frame["name"].map(_is_st_name)]
    frame = frame.loc[
        frame["dividend_yield_ttm"].between(float(min_dividend_yield), float(max_dividend_yield), inclusive="both")
    ]
    frame = frame.sort_values(
        by=["dividend_yield_ttm", "symbol"],
        ascending=[False, True],
    )
    symbols = tuple(dict.fromkeys(frame["symbol"].tolist()))
    if top_n > 0:
        symbols = symbols[: int(top_n)]
    return symbols


def build_symbol_sector_map(
    ak: Any,
    *,
    cache_path: Path | None = DEFAULT_SECTOR_CACHE_PATH,
    max_boards: int | None = None,
    sleep_seconds: float = 0.05,
    force_refresh: bool = False,
) -> dict[str, str]:
    path = Path(cache_path) if cache_path is not None else None
    if path is not None and path.exists() and not force_refresh:
        try:
            payload = json.loads(path.read_text(encoding="utf-8"))
            if isinstance(payload, dict) and payload:
                return {normalize_symbol(key): str(value) for key, value in payload.items()}
        except (json.JSONDecodeError, OSError):
            pass

    if not force_refresh:
        return {}

    boards = ak.stock_board_industry_name_em()
    if boards.empty or "板块名称" not in boards.columns:
        return {}

    mapping: dict[str, str] = {}
    board_names = boards["板块名称"].astype(str).tolist()
    if max_boards is not None:
        board_names = board_names[: int(max_boards)]

    for board_name in board_names:
        try:
            cons = ak.stock_board_industry_cons_em(symbol=board_name)
        except Exception:
            continue
        if cons is None or cons.empty or "代码" not in cons.columns:
            continue
        for symbol in cons["代码"].map(normalize_symbol):
            if symbol:
                mapping[symbol] = board_name
        if sleep_seconds > 0:
            time.sleep(float(sleep_seconds))

    if path is not None and mapping:
        path.parent.mkdir(parents=True, exist_ok=True)
        path.write_text(json.dumps(mapping, ensure_ascii=False, indent=2, sort_keys=True) + "\n", encoding="utf-8")
    return mapping


def lookup_sector(symbol: str, sector_map: Mapping[str, str] | None) -> str:
    if not sector_map:
        return "unknown"
    return str(sector_map.get(normalize_symbol(symbol), "unknown"))


def list_days_from_history(hist: pd.DataFrame, *, as_of: date | None = None) -> int:
    if hist.empty or "日期" not in hist.columns:
        return 1
    first_date = pd.to_datetime(hist["日期"].iloc[0], errors="coerce")
    if pd.isna(first_date):
        return 1
    as_of_ts = pd.Timestamp(as_of or datetime.now(timezone.utc).date()).normalize()
    return max(int((as_of_ts - first_date.normalize()).days), 1)


__all__ = [
    "DEFAULT_SECTOR_CACHE_PATH",
    "build_symbol_sector_map",
    "list_days_from_history",
    "lookup_sector",
    "select_dividend_universe_symbols",
]
