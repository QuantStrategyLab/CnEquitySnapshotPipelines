#!/usr/bin/env python3
from __future__ import annotations

from pathlib import Path

from cn_equity_snapshot_pipelines.dividend_quality import build_and_write_snapshot

ROOT = Path(__file__).resolve().parents[1]
SAMPLE = ROOT / "examples" / "dividend_quality" / "factor_snapshot.sample.csv"
OUTPUT = ROOT / "data" / "output" / "dividend_quality"


def main() -> int:
    result = build_and_write_snapshot(
        factor_snapshot_path=SAMPLE,
        output_dir=OUTPUT,
    )
    print(f"built snapshot rows={len(result.snapshot)} ranking rows={len(result.ranking)}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
