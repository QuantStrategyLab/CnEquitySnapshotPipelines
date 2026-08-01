from __future__ import annotations

import argparse
from dataclasses import dataclass, field
import json
import math
import re
import time
import urllib.parse
from collections.abc import Callable
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
TENCENT_ENDPOINT = "https://web.ifzq.gtimg.cn/appstock/app/fqkline/get"
_CAPTURE_MARKER = object()
_CANONICAL_SYMBOL_RE = re.compile(r"^[0-9]{6}$")


class ProvenanceCaptureError(ValueError):
    """Raised when an HTTP response cannot be accepted as a complete capture."""


@dataclass(frozen=True)
class CapturedChunk:
    symbol: str
    start_date: str
    end_date: str
    received_at: str
    response_media_type: str
    raw_bytes: bytes
    request_identity_bytes: bytes
    input_kind: str = "provider_http_response"
    fallback_used: bool = False
    _capture_marker: object = field(default=None, repr=False, compare=False)


@dataclass(frozen=True)
class CaptureRun:
    requested_symbols: tuple[str, ...]
    start_date: str
    end_date: str
    chunks: tuple[CapturedChunk, ...]


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _iso_date(value: str) -> str:
    try:
        return pd.Timestamp(value).date().isoformat()
    except (TypeError, ValueError):
        raise ProvenanceCaptureError("invalid capture date") from None


def _capture_symbol(value: object) -> str:
    if not isinstance(value, str) or not _CANONICAL_SYMBOL_RE.fullmatch(value):
        raise ProvenanceCaptureError("capture symbol must be canonical six-digit identity")
    return value


def expected_tencent_chunks(start_date: str, end_date: str) -> tuple[tuple[str, str], ...]:
    start = pd.Timestamp(start_date)
    end = pd.Timestamp(end_date)
    if start > end:
        raise ProvenanceCaptureError("capture start date is after end date")
    chunks: list[tuple[str, str]] = []
    for first_year in range(start.year, end.year + 1, 2):
        chunk_start = max(start, pd.Timestamp(first_year, 1, 1))
        chunk_end = min(end, pd.Timestamp(first_year + 1, 12, 31))
        chunks.append((chunk_start.date().isoformat(), chunk_end.date().isoformat()))
    return tuple(chunks)


def canonical_tencent_request_identity(
    *,
    symbol: str,
    start_date: str,
    end_date: str,
    response_media_type: str,
) -> bytes:
    normalized_symbol = _capture_symbol(symbol)
    params = {
        "param": (
            f"{tencent_symbol(normalized_symbol)},day,{start_date},{end_date},2000,qfq"
        )
    }
    return _canonical_json_bytes(
        {
            "date_chunk": {"end": end_date, "start": start_date},
            "endpoint": TENCENT_ENDPOINT,
            "method": "GET",
            "query_params": params,
            "response_media_type": response_media_type,
            "symbol": normalized_symbol,
        }
    )


def _reject_duplicate_json_keys(pairs: list[tuple[str, object]]) -> dict[str, object]:
    result: dict[str, object] = {}
    for key, value in pairs:
        if key in result:
            raise ProvenanceCaptureError("duplicate JSON key in captured response")
        result[key] = value
    return result


def _reject_nonfinite_json(_: str) -> None:
    raise ProvenanceCaptureError("non-finite JSON value in captured response")


def parse_tencent_captured_chunk(chunk: CapturedChunk) -> pd.DataFrame:
    """Parse and fully validate rows from the exact captured response bytes."""
    if chunk._capture_marker is not _CAPTURE_MARKER:
        raise ProvenanceCaptureError("capture was not produced by the HTTP capture surface")
    if chunk.input_kind != "provider_http_response":
        raise ProvenanceCaptureError("capture is not a provider HTTP response")
    if chunk.fallback_used:
        raise ProvenanceCaptureError("fallback capture is forbidden")
    if not isinstance(chunk.raw_bytes, bytes) or not chunk.raw_bytes:
        raise ProvenanceCaptureError("missing raw response bytes")
    if not isinstance(chunk.response_media_type, str) or not chunk.response_media_type.strip():
        raise ProvenanceCaptureError("missing observed Content-Type")
    expected_identity = canonical_tencent_request_identity(
        symbol=chunk.symbol,
        start_date=chunk.start_date,
        end_date=chunk.end_date,
        response_media_type=chunk.response_media_type,
    )
    if chunk.request_identity_bytes != expected_identity:
        raise ProvenanceCaptureError("canonical request identity mismatch")
    try:
        payload = json.loads(
            chunk.raw_bytes,
            object_pairs_hook=_reject_duplicate_json_keys,
            parse_constant=_reject_nonfinite_json,
        )
    except (UnicodeDecodeError, json.JSONDecodeError, TypeError):
        raise ProvenanceCaptureError("invalid JSON in captured response") from None
    if not isinstance(payload, dict) or not isinstance(payload.get("data"), dict):
        raise ProvenanceCaptureError("invalid JSON capture shape")
    provider_symbol = tencent_symbol(chunk.symbol)
    data = payload["data"]
    if set(data) != {provider_symbol}:
        raise ProvenanceCaptureError("wrong symbol in captured response")
    series = data[provider_symbol]
    if not isinstance(series, dict):
        raise ProvenanceCaptureError("invalid JSON capture series")
    if "qfqday" not in series:
        if "day" in series:
            raise ProvenanceCaptureError("fallback price series is forbidden")
        raise ProvenanceCaptureError("empty captured response chunk")
    klines = series["qfqday"]
    if not isinstance(klines, list) or not klines:
        raise ProvenanceCaptureError("empty captured response chunk")
    rows: list[dict[str, object]] = []
    seen_dates: set[str] = set()
    for item in klines:
        if not isinstance(item, list) or len(item) < 3:
            raise ProvenanceCaptureError("truncated captured response row")
        date_value = _iso_date(str(item[0]))
        if date_value in seen_dates:
            raise ProvenanceCaptureError("duplicate date in captured response")
        seen_dates.add(date_value)
        try:
            close = float(item[2])
        except (TypeError, ValueError):
            raise ProvenanceCaptureError("invalid close in captured response") from None
        if not math.isfinite(close) or close <= 0:
            raise ProvenanceCaptureError("invalid non-finite or non-positive close in captured response")
        rows.append(
            {
                "date": date_value,
                "symbol": normalize_symbol(chunk.symbol),
                "close": close,
                "price_basis": "tencent_qfq",
            }
        )
    frame = pd.DataFrame(rows).sort_values("date").reset_index(drop=True)
    minimum = str(frame.iloc[0]["date"])
    maximum = str(frame.iloc[-1]["date"])
    if minimum < chunk.start_date or maximum > chunk.end_date:
        raise ProvenanceCaptureError("out-of-range date in captured response")
    start = pd.Timestamp(chunk.start_date)
    end = pd.Timestamp(chunk.end_date)
    start_complete = minimum == chunk.start_date or (
        start.weekday() >= 5 and 0 <= (pd.Timestamp(minimum) - start).days <= 3
    )
    end_complete = maximum == chunk.end_date or (
        end.weekday() >= 5 and 0 <= (end - pd.Timestamp(maximum)).days <= 3
    )
    if not start_complete or not end_complete:
        raise ProvenanceCaptureError("truncated captured response chunk")
    _validate_history_coverage(
        frame,
        symbol=chunk.symbol,
        start_date=pd.Timestamp(chunk.start_date),
        end_date=pd.Timestamp(chunk.end_date),
    )
    return frame


def capture_tencent_history_run(
    symbols: tuple[str, ...],
    *,
    start_date: str,
    end_date: str,
    http_get: Callable[..., object] | None = None,
    clock: Callable[[], datetime] | None = None,
    max_attempts: int = 3,
    retry_delay_seconds: float = 1.0,
) -> CaptureRun:
    """Capture exact Tencent response bodies; no fallback or partial success is allowed."""
    requested_symbols = tuple(_capture_symbol(symbol) for symbol in symbols)
    if len(requested_symbols) < 2 or len(set(requested_symbols)) != len(requested_symbols):
        raise ProvenanceCaptureError("requested symbols must be non-empty and unique")
    chunks = expected_tencent_chunks(start_date, end_date)
    if http_get is None:
        import requests

        http_get = requests.get
    clock = clock or (lambda: datetime.now(timezone.utc))
    captured: list[CapturedChunk] = []
    attempts = max(int(max_attempts), 1)
    for symbol in requested_symbols:
        for chunk_start, chunk_end in chunks:
            params = {
                "param": (
                    f"{tencent_symbol(symbol)},day,{chunk_start},{chunk_end},2000,qfq"
                )
            }
            response = None
            for attempt in range(1, attempts + 1):
                try:
                    response = http_get(
                        TENCENT_ENDPOINT,
                        params=params,
                        headers={"User-Agent": "Mozilla/5.0"},
                        timeout=30,
                    )
                    response.raise_for_status()
                    break
                except Exception as exc:
                    if attempt == attempts:
                        raise RuntimeError(f"HTTP capture failed for {symbol} {chunk_start}..{chunk_end}") from exc
                    time.sleep(max(float(retry_delay_seconds), 0.0) * attempt)
            if response is None:
                raise AssertionError("unreachable")
            raw_bytes = bytes(response.content)
            media_type = str(response.headers.get("Content-Type", "")).strip()
            received = clock()
            if received.tzinfo is None or received.utcoffset() is None:
                raise ProvenanceCaptureError("capture receipt timestamp must be timezone-aware")
            received_at = received.isoformat().replace("+00:00", "Z")
            capture = CapturedChunk(
                symbol=symbol,
                start_date=chunk_start,
                end_date=chunk_end,
                received_at=received_at,
                response_media_type=media_type,
                raw_bytes=raw_bytes,
                request_identity_bytes=canonical_tencent_request_identity(
                    symbol=symbol,
                    start_date=chunk_start,
                    end_date=chunk_end,
                    response_media_type=media_type,
                ),
                _capture_marker=_CAPTURE_MARKER,
            )
            parse_tencent_captured_chunk(capture)
            captured.append(capture)
    _validate_exact_session_consensus(captured)
    return CaptureRun(
        requested_symbols=requested_symbols,
        start_date=_iso_date(start_date),
        end_date=_iso_date(end_date),
        chunks=tuple(captured),
    )


def _validate_exact_session_consensus(chunks: list[CapturedChunk]) -> None:
    session_sets: dict[tuple[str, str], list[set[str]]] = {}
    for chunk in chunks:
        frame = parse_tencent_captured_chunk(chunk)
        session_sets.setdefault((chunk.start_date, chunk.end_date), []).append(
            set(frame["date"].astype(str))
        )
    for sets in session_sets.values():
        if len(sets) < 2 or any(sessions != sets[0] for sessions in sets[1:]):
            raise ProvenanceCaptureError("exact cross-symbol session coverage cannot be proven")


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
                    if not rows:
                        break
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
                    start_date=(
                        pd.Timestamp(chunk_frame["date"].min())
                        if not rows
                        else chunk_start
                    ),
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
