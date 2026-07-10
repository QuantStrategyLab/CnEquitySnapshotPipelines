from __future__ import annotations

import argparse
import time
from datetime import datetime, timezone
from pathlib import Path

import pandas as pd

DEFAULT_ETF_SYMBOLS = (
    "510300",
    "510500",
    "159915",
    "159949",
    "588000",
    "512100",
    "512170",
    "515030",
    "512760",
    "518880",
    "513100",
    "511880",
    "511260",
    "159819",
    "159995",
    "159994",
    "159852",
    "159792",
    "512800",
    "512690",
    "159928",
)


def normalize_symbol(value: object) -> str:
    text = str(value or "").strip().upper()
    if text.endswith(".SH") or text.endswith(".SZ"):
        text = text.split(".", 1)[0]
    return text.zfill(6) if text.isdigit() else text


def _import_akshare():
    import akshare as ak

    return ak


def fetch_etf_history(
    symbol: str,
    *,
    ak=None,
    start_date: str = "20200101",
    max_attempts: int = 3,
    retry_delay_seconds: float = 1.0,
) -> pd.DataFrame:
    ak_module = ak or _import_akshare()
    end_date = datetime.now(timezone.utc).strftime("%Y%m%d")
    attempts = max(int(max_attempts), 1)
    for attempt in range(1, attempts + 1):
        try:
            frame = ak_module.fund_etf_hist_em(
                symbol=normalize_symbol(symbol),
                period="daily",
                start_date=start_date,
                end_date=end_date,
                adjust="qfq",
            )
            if frame.empty:
                raise ValueError(f"empty ETF history for {symbol}")
            break
        except Exception:
            if attempt == attempts:
                raise
            time.sleep(max(float(retry_delay_seconds), 0.0) * attempt)
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
    request_delay_seconds: float = 0.5,
) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    errors: dict[str, str] = {}
    requested_symbols = tuple(dict.fromkeys(normalize_symbol(symbol) for symbol in symbols))
    for index, symbol in enumerate(requested_symbols):
        try:
            frames.append(fetch_etf_history(symbol, ak=ak, start_date=start_date))
        except Exception as exc:
            errors[normalize_symbol(symbol)] = str(exc)
        if index + 1 < len(requested_symbols):
            time.sleep(max(float(request_delay_seconds), 0.0))
    if errors:
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
        "symbols": sorted(frame["symbol"].unique().tolist()),
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
