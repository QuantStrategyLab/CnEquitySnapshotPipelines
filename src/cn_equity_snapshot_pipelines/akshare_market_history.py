from __future__ import annotations

import argparse
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

DEFAULT_ETF_SYMBOLS = (
    "510300",
    "510500",
    "159915",
    "588000",
    "512100",
    "512170",
    "515030",
    "512760",
    "518880",
    "513100",
    "511880",
    "511260",
)


def normalize_symbol(value: object) -> str:
    text = str(value or "").strip().upper()
    if text.endswith(".SH") or text.endswith(".SZ"):
        text = text.split(".", 1)[0]
    return text.zfill(6) if text.isdigit() else text


def _import_akshare():
    import akshare as ak

    return ak


def fetch_etf_history(symbol: str, *, ak=None, start_date: str = "20200101") -> pd.DataFrame:
    ak_module = ak or _import_akshare()
    end_date = datetime.now(timezone.utc).strftime("%Y%m%d")
    frame = ak_module.fund_etf_hist_em(
        symbol=normalize_symbol(symbol),
        period="daily",
        start_date=start_date,
        end_date=end_date,
        adjust="qfq",
    )
    if frame.empty:
        raise ValueError(f"empty ETF history for {symbol}")
    output = pd.DataFrame(
        {
            "date": pd.to_datetime(frame["日期"], errors="coerce").dt.date.astype(str),
            "symbol": normalize_symbol(symbol),
            "close": pd.to_numeric(frame["收盘"], errors="coerce"),
        }
    )
    return output.dropna(subset=["date", "close"])


def build_market_history_frame(
    symbols: tuple[str, ...],
    *,
    ak=None,
    start_date: str = "20200101",
) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    errors: dict[str, str] = {}
    for symbol in symbols:
        try:
            frames.append(fetch_etf_history(symbol, ak=ak, start_date=start_date))
        except Exception as exc:
            errors[normalize_symbol(symbol)] = str(exc)
    if not frames:
        missing = ", ".join(sorted(errors))
        raise RuntimeError(f"failed to fetch ETF histories: {missing}")
    history = pd.concat(frames, ignore_index=True)
    history = history.sort_values(["symbol", "date"]).reset_index(drop=True)
    return history


def write_market_history_csv(
    *,
    output_path: str | Path,
    symbols: tuple[str, ...] = DEFAULT_ETF_SYMBOLS,
    start_date: str = "20200101",
) -> dict[str, object]:
    ak = _import_akshare()
    frame = build_market_history_frame(symbols, ak=ak, start_date=start_date)
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False)
    return {
        "output_path": str(path),
        "row_count": int(len(frame)),
        "symbols": [normalize_symbol(symbol) for symbol in symbols],
        "start_date": start_date,
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Stage ETF market history CSV for cn_index_etf_tactical_rotation.")
    parser.add_argument("--output", default="data/staging/market_history/etf_universe.latest.csv")
    parser.add_argument("--symbols", default=",".join(DEFAULT_ETF_SYMBOLS))
    parser.add_argument("--start-date", default="20200101")
    args = parser.parse_args(argv)
    symbols = tuple(symbol.strip() for symbol in args.symbols.split(",") if symbol.strip())
    diagnostics = write_market_history_csv(
        output_path=args.output,
        symbols=symbols,
        start_date=args.start_date,
    )
    print(diagnostics)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
