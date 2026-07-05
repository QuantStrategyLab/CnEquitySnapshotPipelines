from __future__ import annotations

from pathlib import Path

from cn_equity_snapshot_pipelines.chinext_growth_momentum_quality import build_and_write_snapshot


def test_build_and_write_chinext_growth_momentum_quality_snapshot(tmp_path: Path) -> None:
    sample_path = (
        Path(__file__).resolve().parents[1]
        / "examples"
        / "chinext_growth_momentum_quality"
        / "factor_snapshot.sample.csv"
    )

    result = build_and_write_snapshot(factor_snapshot_path=sample_path, output_dir=tmp_path)

    assert result.artifact_paths["snapshot"].exists()
    assert result.artifact_paths["manifest"].exists()
    assert result.artifact_paths["ranking"].exists()
    assert result.artifact_paths["release_summary"].exists()
    assert result.diagnostics["snapshot_contract_version"] == (
        "cn_chinext_growth_momentum_quality_snapshot.factor_snapshot.v1"
    )
