from __future__ import annotations

import argparse
import time
import urllib.parse
from datetime import datetime, timedelta, timezone
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
PRICE_BASIS = "adjusted_close_equivalent"
MAX_BOUNDARY_GAP_DAYS = 14
MIN_BUSINESS_DAY_COVERAGE = 0.75


def normalize_symbol(value: object) -> str:
    text = str(value or "").strip().upper()
    if text.endswith(".SH") or text.endswith(".SZ"):
        text = text.split(".", 1)[0]
    return text.zfill(6) if text.isdigit() else text


def _import_akshare():
    import akshare as ak

    return ak


def yahoo_symbol(value: object) -> str:
    symbol = normalize_symbol(value)
    suffix = ".SZ" if symbol.startswith(("0", "1", "3")) else ".SS"
    return f"{symbol}{suffix}"


def tencent_symbol(value: object) -> str:
    symbol = normalize_symbol(value)
    prefix = "sz" if symbol.startswith(("0", "1", "3")) else "sh"
    return f"{prefix}{symbol}"


def _validate_history_coverage(
    frame: pd.DataFrame,
    *,
    symbol: str,
    start_date: pd.Timestamp,
    end_date: pd.Timestamp,
) -> None:
    dates = pd.DatetimeIndex(pd.to_datetime(frame["date"], errors="coerce").dropna().unique()).sort_values()
    expected = pd.bdate_range(start_date.normalize(), end_date.normalize())
    if (
        dates.empty
        or dates.min() > start_date.normalize() + pd.Timedelta(days=MAX_BOUNDARY_GAP_DAYS)
        or dates.max() < end_date.normalize() - pd.Timedelta(days=MAX_BOUNDARY_GAP_DAYS)
        or len(dates) / max(len(expected), 1) < MIN_BUSINESS_DAY_COVERAGE
    ):
        raise ValueError(f"incomplete adjusted ETF history coverage for {symbol}")


def fetch_tencent_etf_history(
    symbol: str,
    *,
    start_date: str = "20200101",
    end_date: str | None = None,
    max_attempts: int = 3,
    retry_delay_seconds: float = 1.0,
) -> pd.DataFrame:
    import requests

    start = pd.Timestamp(start_date)
    end = pd.Timestamp(end_date) if end_date else pd.Timestamp(datetime.now(timezone.utc).date())
    rows: list[dict[str, object]] = []
    for first_year in range(start.year, end.year + 1, 2):
        chunk_start = max(start, pd.Timestamp(first_year, 1, 1))
        chunk_end = min(end, pd.Timestamp(first_year + 1, 12, 31))
        params = {
            "param": (
                f"{tencent_symbol(symbol)},day,{chunk_start.date().isoformat()},"
                f"{chunk_end.date().isoformat()},2000,qfq"
            )
        }
        for attempt in range(1, max(int(max_attempts), 1) + 1):
            try:
                response = requests.get(
                    "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get",
                    params=params,
                    headers={"User-Agent": "Mozilla/5.0"},
                    timeout=30,
                )
                response.raise_for_status()
                payload = response.json()
                series = payload.get("data", {}).get(tencent_symbol(symbol), {})
                klines = series.get("qfqday") or series.get("day") or []
                if not klines:
                    raise ValueError(f"empty Tencent ETF history for {symbol}")
                basis = "tencent_qfq" if series.get("qfqday") else "tencent_qfq_identity"
                chunk_frame = pd.DataFrame(
                    [
                        {
                            "date": item[0],
                            "symbol": normalize_symbol(symbol),
                            "close": float(item[2]),
                            "price_basis": basis,
                        }
                        for item in klines
                    ]
                )
                _validate_history_coverage(
                    chunk_frame,
                    symbol=symbol,
                    start_date=chunk_start,
                    end_date=chunk_end,
                )
                rows.extend(chunk_frame.to_dict("records"))
                break
            except Exception:
                if attempt == max(int(max_attempts), 1):
                    raise
                time.sleep(max(float(retry_delay_seconds), 0.0) * attempt)
    frame = pd.DataFrame(rows).drop_duplicates(["date", "symbol"], keep="last")
    if frame.empty:
        raise ValueError(f"empty Tencent ETF history for {symbol}")
    return frame.sort_values("date").reset_index(drop=True)


def fetch_yahoo_etf_history(
    symbol: str,
    *,
    start_date: str = "20200101",
    end_date: str | None = None,
    max_attempts: int = 3,
    retry_delay_seconds: float = 1.0,
) -> pd.DataFrame:
    import requests

    start = pd.Timestamp(start_date, tz="UTC")
    end = pd.Timestamp(end_date, tz="UTC") if end_date else pd.Timestamp(datetime.now(timezone.utc) + timedelta(days=1))
    query = urllib.parse.urlencode(
        {
            "period1": int(start.timestamp()),
            "period2": int(end.timestamp()),
            "interval": "1d",
            "events": "history",
        }
    )
    url = f"https://query1.finance.yahoo.com/v8/finance/chart/{urllib.parse.quote(yahoo_symbol(symbol))}?{query}"
    attempts = max(int(max_attempts), 1)
    for attempt in range(1, attempts + 1):
        try:
            response = requests.get(url, headers={"User-Agent": "Mozilla/5.0"}, timeout=30)
            response.raise_for_status()
            payload = response.json()
            result = payload.get("chart", {}).get("result") or []
            if not result:
                raise ValueError(f"empty Yahoo ETF history for {symbol}")
            series = result[0]
            timestamps = series.get("timestamp") or []
            indicators = series.get("indicators") or {}
            # Preserve the existing AkShare adjust="qfq" contract: `close` is adjusted, not raw exchange close.
            adjusted = (indicators.get("adjclose") or [{}])[0].get("adjclose") or []
            if len(adjusted) != len(timestamps) or any(value is None for value in adjusted):
                raise ValueError(f"incomplete Yahoo adjusted ETF history for {symbol}")
            rows = []
            for index, raw_timestamp in enumerate(timestamps):
                close = adjusted[index]
                rows.append(
                    {
                        "date": pd.Timestamp.fromtimestamp(int(raw_timestamp), tz="UTC").date().isoformat(),
                        "symbol": normalize_symbol(symbol),
                        "close": float(close),
                        "price_basis": "yahoo_adjusted_close",
                    }
                )
            frame = pd.DataFrame(rows)
            if frame.empty:
                raise ValueError(f"empty Yahoo ETF history for {symbol}")
            _validate_history_coverage(
                frame,
                symbol=symbol,
                start_date=start.tz_localize(None),
                end_date=end.tz_localize(None),
            )
            return frame
        except Exception:
            if attempt == attempts:
                raise
            time.sleep(max(float(retry_delay_seconds), 0.0) * attempt)
    raise AssertionError("unreachable")


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
            "price_basis": "akshare_qfq",
        }
    )
    return output.dropna(subset=["date", "close"])


def fetch_hybrid_etf_history(symbol: str, *, start_date: str = "20200101") -> pd.DataFrame:
    try:
        return fetch_yahoo_etf_history(symbol, start_date=start_date)
    except Exception:
        return fetch_tencent_etf_history(symbol, start_date=start_date)


def build_market_history_frame(
    symbols: tuple[str, ...],
    *,
    ak=None,
    start_date: str = "20200101",
    request_delay_seconds: float = 0.5,
    source: str = "akshare",
) -> pd.DataFrame:
    frames: list[pd.DataFrame] = []
    errors: dict[str, str] = {}
    requested_symbols = tuple(dict.fromkeys(normalize_symbol(symbol) for symbol in symbols))
    fetchers = {
        "akshare": fetch_etf_history,
        "hybrid": fetch_hybrid_etf_history,
        "tencent": fetch_tencent_etf_history,
        "yahoo": fetch_yahoo_etf_history,
    }
    if source not in fetchers:
        raise ValueError("source must be 'akshare', 'hybrid', 'tencent', or 'yahoo'")
    fetcher = fetchers[source]
    for index, symbol in enumerate(requested_symbols):
        try:
            kwargs = {"start_date": start_date}
            if source == "akshare":
                kwargs["ak"] = ak
            frames.append(fetcher(symbol, **kwargs))
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
    source: str = "akshare",
) -> dict[str, object]:
    ak = _import_akshare() if source == "akshare" else None
    frame = build_market_history_frame(symbols, ak=ak, start_date=start_date, source=source)
    path = Path(output_path)
    path.parent.mkdir(parents=True, exist_ok=True)
    frame.to_csv(path, index=False)
    return {
        "output_path": str(path),
        "row_count": int(len(frame)),
        "symbols": sorted(frame["symbol"].unique().tolist()),
        "start_date": start_date,
        "source": source,
        "price_basis": PRICE_BASIS,
        "source_price_bases": sorted(frame["price_basis"].unique().tolist()),
    }


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(description="Stage ETF market history CSV for cn_index_etf_tactical_rotation.")
    parser.add_argument("--output", default="data/staging/market_history/etf_universe.latest.csv")
    parser.add_argument("--symbols", default=",".join(DEFAULT_ETF_SYMBOLS))
    parser.add_argument("--start-date", default="20200101")
    parser.add_argument("--source", choices=("akshare", "hybrid", "tencent", "yahoo"), default="akshare")
    args = parser.parse_args(argv)
    symbols = tuple(symbol.strip() for symbol in args.symbols.split(",") if symbol.strip())
    diagnostics = write_market_history_csv(
        output_path=args.output,
        symbols=symbols,
        start_date=args.start_date,
        source=args.source,
    )
    print(diagnostics)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
