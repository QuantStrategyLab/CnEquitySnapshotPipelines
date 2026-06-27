from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

FACTOR_SNAPSHOT_COLUMNS = (
    "symbol",
    "sector",
    "close_cny",
    "adv20_cny",
    "market_cap_cny",
    "dividend_yield_ttm",
    "dividend_stability_3y",
    "earnings_positive",
    "payout_ratio",
    "roe_ttm",
    "roe_stability_3y",
    "realized_vol_126",
    "mom_12_1",
    "sma200_gap",
    "suspension_days_63",
    "is_st",
    "list_days",
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


def _normalize_symbol(value: object) -> str:
    text = str(value or "").strip().upper()
    if text.endswith(".SH") or text.endswith(".SZ"):
        text = text.split(".", 1)[0]
    return text.zfill(6) if text.isdigit() else text


def _fetch_spot_rows() -> pd.DataFrame:
    import akshare as ak

    spot = ak.stock_zh_a_spot_em()
    spot = spot.rename(
        columns={
            "代码": "symbol",
            "名称": "name",
            "最新价": "close_cny",
            "成交额": "turnover_cny",
            "总市值": "market_cap_cny",
            "市盈率-动态": "pe_ttm",
        }
    )
    spot["symbol"] = spot["symbol"].map(_normalize_symbol)
    return spot


def build_factor_snapshot_from_akshare(
    *,
    symbols: tuple[str, ...] = DEFAULT_STAGING_SYMBOLS,
    sample_fallback_path: str | Path | None = None,
    min_rows: int = 4,
) -> tuple[pd.DataFrame, dict[str, object]]:
    diagnostics: dict[str, object] = {"source": "akshare", "requested_symbols": list(symbols)}
    try:
        spot = _fetch_spot_rows()
    except Exception as exc:
        diagnostics["source"] = "sample_fallback"
        diagnostics["akshare_error"] = str(exc)
        if sample_fallback_path is None:
            raise
        frame = _load_sample_fallback(Path(sample_fallback_path))
        diagnostics["row_count"] = len(frame)
        return frame, diagnostics

    normalized_symbols = {_normalize_symbol(symbol) for symbol in symbols}
    filtered = spot.loc[spot["symbol"].isin(normalized_symbols)].copy()
    if len(filtered) < min_rows:
        diagnostics["source"] = "sample_fallback"
        diagnostics["akshare_error"] = f"only {len(filtered)} rows matched requested symbols"
        if sample_fallback_path is None:
            raise ValueError(diagnostics["akshare_error"])
        frame = _load_sample_fallback(Path(sample_fallback_path))
        diagnostics["row_count"] = len(frame)
        return frame, diagnostics

    rows: list[dict[str, object]] = []
    for _, item in filtered.iterrows():
        close_cny = float(item.get("close_cny") or 0.0)
        turnover_cny = float(item.get("turnover_cny") or 0.0)
        market_cap_cny = float(item.get("market_cap_cny") or 0.0)
        rows.append(
            {
                "symbol": item["symbol"],
                "sector": "unknown",
                "close_cny": close_cny,
                "adv20_cny": max(turnover_cny, 1.0),
                "market_cap_cny": market_cap_cny,
                "dividend_yield_ttm": 0.04,
                "dividend_stability_3y": 0.70,
                "earnings_positive": True,
                "payout_ratio": 0.40,
                "roe_ttm": 0.12,
                "roe_stability_3y": 0.65,
                "realized_vol_126": 0.18,
                "mom_12_1": 0.05,
                "sma200_gap": 0.02,
                "suspension_days_63": 0,
                "is_st": False,
                "list_days": 2000,
            }
        )
    frame = pd.DataFrame(rows, columns=list(FACTOR_SNAPSHOT_COLUMNS))
    if "as_of" not in frame.columns and "snapshot_date" not in frame.columns:
        stamp = datetime.now(timezone.utc).date().isoformat()
        frame.insert(0, "as_of", stamp)
    diagnostics["row_count"] = len(frame)
    return frame, diagnostics


def write_staging_factor_snapshot(
    *,
    output_path: str | Path,
    symbols: tuple[str, ...] = DEFAULT_STAGING_SYMBOLS,
    sample_fallback_path: str | Path | None = None,
) -> dict[str, object]:
    frame, diagnostics = build_factor_snapshot_from_akshare(
        symbols=symbols,
        sample_fallback_path=sample_fallback_path,
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
        "--sample-fallback",
        default=str(Path(__file__).resolve().parents[2] / "examples" / "dividend_quality" / "factor_snapshot.sample.csv"),
    )
    args = parser.parse_args(argv)
    symbols = tuple(symbol.strip() for symbol in args.symbols.split(",") if symbol.strip())
    diagnostics = write_staging_factor_snapshot(
        output_path=args.output,
        symbols=symbols,
        sample_fallback_path=args.sample_fallback,
    )
    print(diagnostics)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
