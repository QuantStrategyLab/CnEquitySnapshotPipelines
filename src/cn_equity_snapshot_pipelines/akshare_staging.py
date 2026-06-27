from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path
from typing import Callable

import pandas as pd

from .akshare_metadata import (
    build_symbol_sector_map,
    lookup_sector,
    select_dividend_universe_symbols,
)
from .akshare_enrichment import (
    FACTOR_SNAPSHOT_COLUMNS,
    FHPS_CANDIDATE_DATES,
    compute_dividend_stability,
    compute_financial_features,
    compute_price_features,
    extract_fhps_features,
    merge_factor_row,
    normalize_symbol,
    stamp_as_of,
)

DEFAULT_STAGING_SYMBOLS = (
    "600519",
    "601088",
    "000001",
    "600036",
    "601398",
    "600900",
    "601318",
    "600028",
)


def _load_sample_fallback(sample_path: Path) -> pd.DataFrame:
    frame = pd.read_csv(sample_path)
    missing = [column for column in FACTOR_SNAPSHOT_COLUMNS if column not in frame.columns]
    if missing:
        missing_text = ", ".join(missing)
        raise ValueError(f"sample fallback missing columns: {missing_text}")
    return frame.loc[:, FACTOR_SNAPSHOT_COLUMNS].copy()


def _import_akshare():
    import akshare as ak

    return ak


def _fetch_fhps_table(ak) -> pd.DataFrame:
    last_error: Exception | None = None
    for report_date in FHPS_CANDIDATE_DATES:
        try:
            frame = ak.stock_fhps_em(date=report_date)
            if frame is not None and not frame.empty:
                frame = frame.copy()
                frame["symbol"] = frame["代码"].map(normalize_symbol)
                return frame
        except Exception as exc:
            last_error = exc
            continue
    if last_error is not None:
        raise last_error
    raise RuntimeError("stock_fhps_em returned no data for candidate report dates")


def _fetch_history(ak, symbol: str) -> pd.DataFrame:
    end_date = datetime.now(timezone.utc).strftime("%Y%m%d")
    return ak.stock_zh_a_hist(
        symbol=normalize_symbol(symbol),
        period="daily",
        start_date="20180101",
        end_date=end_date,
        adjust="qfq",
    )


def _fetch_financials(ak, symbol: str) -> pd.DataFrame:
    start_year = str(datetime.now(timezone.utc).year - 4)
    return ak.stock_financial_analysis_indicator(symbol=normalize_symbol(symbol), start_year=start_year)


def _fetch_dividends(ak, symbol: str) -> pd.DataFrame:
    return ak.stock_history_dividend_detail(symbol=normalize_symbol(symbol), indicator="分红")


def _fetch_sector(ak, symbol: str, *, sector_map: dict[str, str] | None = None) -> str:
    if sector_map:
        sector = lookup_sector(symbol, sector_map)
        if sector != "unknown":
            return sector
    return "unknown"


def resolve_universe_symbols(
    ak,
    fhps_table: pd.DataFrame,
    *,
    mode: str = "staging",
    custom_symbols: tuple[str, ...] | None = None,
    expanded_top_n: int = 40,
) -> tuple[str, ...]:
    if custom_symbols:
        return tuple(dict.fromkeys(normalize_symbol(item) for item in custom_symbols if normalize_symbol(item)))
    if mode == "expanded":
        selected = select_dividend_universe_symbols(
            fhps_table,
            top_n=int(expanded_top_n),
        )
        if selected:
            return selected
    return DEFAULT_STAGING_SYMBOLS


def build_factor_row_from_akshare(
    symbol: str,
    *,
    ak=None,
    fhps_table: pd.DataFrame | None = None,
    sector_map: dict[str, str] | None = None,
    fetch_history: Callable[[str], pd.DataFrame] | None = None,
    fetch_financials: Callable[[str], pd.DataFrame] | None = None,
    fetch_dividends: Callable[[str], pd.DataFrame] | None = None,
    fetch_sector: Callable[[str], str] | None = None,
) -> dict[str, object]:
    needs_akshare = any(
        item is None
        for item in (fetch_history, fetch_financials, fetch_dividends, fetch_sector, fhps_table)
    )
    ak_module = ak
    if needs_akshare and ak_module is None:
        ak_module = _import_akshare()
    if fhps_table is None and ak_module is not None:
        fhps_table = _fetch_fhps_table(ak_module)
    history_loader = fetch_history or (lambda item: _fetch_history(ak_module, item))
    financial_loader = fetch_financials or (lambda item: _fetch_financials(ak_module, item))
    dividend_loader = fetch_dividends or (lambda item: _fetch_dividends(ak_module, item))
    sector_loader = fetch_sector or (lambda item: _fetch_sector(ak_module, item, sector_map=sector_map))

    normalized = normalize_symbol(symbol)
    price = compute_price_features(history_loader(normalized))
    financials = compute_financial_features(financial_loader(normalized))
    dividend_stability_3y = compute_dividend_stability(dividend_loader(normalized))

    fhps_features = None
    if fhps_table is not None and not fhps_table.empty:
        matched = fhps_table.loc[fhps_table["symbol"] == normalized]
        if not matched.empty:
            fhps_features = extract_fhps_features(matched.iloc[0], close_cny=float(price["close_cny"]))

    sector = sector_loader(normalized)
    return merge_factor_row(
        symbol=normalized,
        price=price,
        fhps=fhps_features,
        financials=financials,
        dividend_stability_3y=dividend_stability_3y,
        sector=sector,
    )


def build_factor_snapshot_from_akshare(
    *,
    symbols: tuple[str, ...] = DEFAULT_STAGING_SYMBOLS,
    sample_fallback_path: str | Path | None = None,
    min_rows: int = 4,
    as_of: str | None = None,
    universe_mode: str = "custom",
    expanded_top_n: int = 40,
    sector_map: dict[str, str] | None = None,
    refresh_sector_map: bool = False,
) -> tuple[pd.DataFrame, dict[str, object]]:
    diagnostics: dict[str, object] = {
        "source": "akshare",
        "requested_symbols": list(symbols),
        "universe_mode": universe_mode,
        "symbol_errors": {},
    }
    try:
        ak = _import_akshare()
        fhps_table = _fetch_fhps_table(ak)
        if universe_mode in {"staging", "expanded"}:
            symbols = resolve_universe_symbols(
                ak,
                fhps_table,
                mode=universe_mode,
                expanded_top_n=expanded_top_n,
            )
        if sector_map is None:
            sector_map = build_symbol_sector_map(ak, force_refresh=refresh_sector_map)
    except Exception as exc:
        diagnostics["source"] = "sample_fallback"
        diagnostics["akshare_error"] = str(exc)
        if sample_fallback_path is None:
            raise
        frame = stamp_as_of(_load_sample_fallback(Path(sample_fallback_path)), as_of=as_of)
        diagnostics["row_count"] = len(frame)
        return frame, diagnostics

    rows: list[dict[str, object]] = []
    for symbol in symbols:
        try:
            rows.append(
                build_factor_row_from_akshare(
                    symbol,
                    ak=ak,
                    fhps_table=fhps_table,
                    sector_map=sector_map,
                )
            )
        except Exception as exc:
            diagnostics["symbol_errors"][normalize_symbol(symbol)] = str(exc)

    if len(rows) < min_rows:
        diagnostics["source"] = "sample_fallback"
        diagnostics["akshare_error"] = f"only {len(rows)} symbols enriched successfully"
        if sample_fallback_path is None:
            raise ValueError(diagnostics["akshare_error"])
        frame = stamp_as_of(_load_sample_fallback(Path(sample_fallback_path)), as_of=as_of)
        diagnostics["row_count"] = len(frame)
        return frame, diagnostics

    frame = stamp_as_of(pd.DataFrame(rows, columns=list(FACTOR_SNAPSHOT_COLUMNS)), as_of=as_of)
    diagnostics["row_count"] = len(frame)
    diagnostics["fhps_rows"] = int(len(fhps_table))
    diagnostics["sector_map_size"] = int(len(sector_map or {}))
    return frame, diagnostics


def write_staging_factor_snapshot(
    *,
    output_path: str | Path,
    symbols: tuple[str, ...] = DEFAULT_STAGING_SYMBOLS,
    sample_fallback_path: str | Path | None = None,
    as_of: str | None = None,
    universe_mode: str = "custom",
    expanded_top_n: int = 40,
    sector_map: dict[str, str] | None = None,
    refresh_sector_map: bool = False,
) -> dict[str, object]:
    frame, diagnostics = build_factor_snapshot_from_akshare(
        symbols=symbols,
        sample_fallback_path=sample_fallback_path,
        as_of=as_of,
        universe_mode=universe_mode,
        expanded_top_n=expanded_top_n,
        sector_map=sector_map,
        refresh_sector_map=refresh_sector_map,
    )
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False)
    diagnostics["output_path"] = str(path)
    return diagnostics


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Stage cn_dividend_quality_snapshot factor CSV via AkShare.")
    parser.add_argument("--output", default="data/staging/dividend_quality/factor_snapshot.latest.csv")
    parser.add_argument("--symbols", default=",".join(DEFAULT_STAGING_SYMBOLS))
    parser.add_argument(
        "--universe-mode",
        choices=("custom", "staging", "expanded"),
        default="custom",
        help="custom=use --symbols; staging=8-symbol default; expanded=fhps top dividend yield pool",
    )
    parser.add_argument("--expanded-top-n", type=int, default=40)
    parser.add_argument("--refresh-sector-map", action="store_true")
    parser.add_argument("--as-of", default=None, help="Optional as_of date (YYYY-MM-DD). Defaults to UTC today.")
    parser.add_argument(
        "--sample-fallback",
        default=str(Path(__file__).resolve().parents[2] / "examples" / "dividend_quality" / "factor_snapshot.sample.csv"),
    )
    args = parser.parse_args(argv)
    symbols = tuple(symbol.strip() for symbol in args.symbols.split(",") if symbol.strip())
    diagnostics = write_staging_factor_snapshot(
        output_path=args.output,
        symbols=symbols,
        sample_fallback_path=args.sample_fallback,
        as_of=args.as_of,
        universe_mode=args.universe_mode,
        expanded_top_n=args.expanded_top_n,
        refresh_sector_map=args.refresh_sector_map,
    )
    print(diagnostics)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
