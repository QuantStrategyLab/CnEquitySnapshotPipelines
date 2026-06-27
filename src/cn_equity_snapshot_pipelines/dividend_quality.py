from __future__ import annotations

import argparse
from pathlib import Path

import pandas as pd

from cn_equity_strategies.strategies.cn_dividend_quality_snapshot import compute_signals, score_candidates

from .artifacts import SnapshotBuildResult, write_release_status_summary, write_snapshot_manifest
from .contracts import CN_DIVIDEND_QUALITY_SNAPSHOT_PROFILE, get_profile_contract


def _read_csv(path: str | Path) -> pd.DataFrame:
    return pd.read_csv(Path(path))


def build_and_write_snapshot(
    *,
    factor_snapshot_path: str | Path,
    output_dir: str | Path,
    min_adv20_cny: float = 0.0,
    min_market_cap_cny: float = 0.0,
) -> SnapshotBuildResult:
    contract = get_profile_contract(CN_DIVIDEND_QUALITY_SNAPSHOT_PROFILE)
    artifact_paths = contract.artifact_paths(output_dir)
    snapshot = _read_csv(factor_snapshot_path)
    ranking = score_candidates(
        snapshot,
        min_adv20_cny=float(min_adv20_cny),
        min_market_cap_cny=float(min_market_cap_cny),
    )

    for path in artifact_paths.values():
        path.parent.mkdir(parents=True, exist_ok=True)
    snapshot.to_csv(artifact_paths["snapshot"], index=False)
    ranking.to_csv(artifact_paths["ranking"], index=False)
    write_snapshot_manifest(
        contract=contract,
        snapshot_path=artifact_paths["snapshot"],
        snapshot=snapshot,
        manifest_path=artifact_paths["manifest"],
    )
    weights, signal_description, _is_hard_defense, status_description, diagnostics = compute_signals(
        snapshot,
        current_holdings=set(),
        min_adv20_cny=float(min_adv20_cny),
        min_market_cap_cny=float(min_market_cap_cny),
    )
    write_release_status_summary(
        contract=contract,
        snapshot_path=artifact_paths["snapshot"],
        manifest_path=artifact_paths["manifest"],
        ranking_path=artifact_paths["ranking"],
        summary_path=artifact_paths["release_summary"],
        snapshot=snapshot,
        signal_description=signal_description,
        status_description=status_description,
        diagnostics={**diagnostics, "target_weights": weights},
    )
    return SnapshotBuildResult(
        snapshot=snapshot,
        ranking=ranking,
        artifact_paths=artifact_paths,
        signal_description=signal_description,
        status_description=status_description,
        diagnostics=dict(diagnostics),
    )


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--factor-snapshot", required=True, help="CSV with cn_dividend_quality_snapshot factor columns")
    parser.add_argument("--output-dir", default="data/output/dividend_quality")
    parser.add_argument("--min-adv20-cny", type=float, default=0.0)
    parser.add_argument("--min-market-cap-cny", type=float, default=0.0)
    args = parser.parse_args(argv)

    result = build_and_write_snapshot(
        factor_snapshot_path=args.factor_snapshot,
        output_dir=args.output_dir,
        min_adv20_cny=args.min_adv20_cny,
        min_market_cap_cny=args.min_market_cap_cny,
    )
    print(f"snapshot={result.artifact_paths['snapshot']}")
    print(f"manifest={result.artifact_paths['manifest']}")
    print(f"ranking={result.artifact_paths['ranking']}")
    print(f"release_summary={result.artifact_paths['release_summary']}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
