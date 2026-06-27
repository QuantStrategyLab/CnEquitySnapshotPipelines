from __future__ import annotations

from pathlib import Path

import pandas as pd

from cn_equity_snapshot_pipelines.akshare_staging import (
    FACTOR_SNAPSHOT_COLUMNS,
    build_factor_snapshot_from_akshare,
    write_staging_factor_snapshot,
)


def test_build_factor_snapshot_falls_back_to_sample():
    sample_path = Path(__file__).resolve().parents[1] / "examples" / "dividend_quality" / "factor_snapshot.sample.csv"
    frame, diagnostics = build_factor_snapshot_from_akshare(
        symbols=("999999",),
        sample_fallback_path=sample_path,
        min_rows=1,
    )
    assert diagnostics["source"] == "sample_fallback"
    assert list(frame.columns) == list(FACTOR_SNAPSHOT_COLUMNS)
    assert len(frame) >= 1


def test_write_staging_factor_snapshot(tmp_path):
    sample_path = Path(__file__).resolve().parents[1] / "examples" / "dividend_quality" / "factor_snapshot.sample.csv"
    output_path = tmp_path / "factor_snapshot.latest.csv"
    diagnostics = write_staging_factor_snapshot(
        output_path=output_path,
        symbols=("999999",),
        sample_fallback_path=sample_path,
    )
    assert output_path.exists()
    frame = pd.read_csv(output_path)
    assert list(frame.columns) == list(FACTOR_SNAPSHOT_COLUMNS)
    assert diagnostics["output_path"] == str(output_path)
