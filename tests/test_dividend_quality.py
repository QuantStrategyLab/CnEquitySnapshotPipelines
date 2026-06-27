from __future__ import annotations

from pathlib import Path

from cn_equity_snapshot_pipelines.dividend_quality import build_and_write_snapshot


def test_build_dividend_quality_snapshot_writes_artifact_pack(tmp_path: Path):
    sample = Path(__file__).resolve().parents[1] / "examples" / "dividend_quality" / "factor_snapshot.sample.csv"
    result = build_and_write_snapshot(
        factor_snapshot_path=sample,
        output_dir=tmp_path,
    )

    assert len(result.snapshot) == 4
    assert not result.ranking.empty
    for key in ("snapshot", "manifest", "ranking", "release_summary"):
        assert result.artifact_paths[key].exists()
    assert result.diagnostics["snapshot_contract_version"] == "cn_dividend_quality_snapshot.factor_snapshot.v1"
