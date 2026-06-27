from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

SOURCE_PROJECT = "CnEquitySnapshotPipelines"
CN_DIVIDEND_QUALITY_SNAPSHOT_PROFILE = "cn_dividend_quality_snapshot"


@dataclass(frozen=True)
class SnapshotProfileContract:
    profile: str
    display_name: str
    contract_version: str
    snapshot_filename: str
    manifest_filename: str
    ranking_filename: str
    release_summary_filename: str = "release_status_summary.json"
    legacy_aliases: tuple[str, ...] = ()
    neutral_gcs_prefix_hint: str | None = None
    manifest_required_by_runtime: bool = True

    def artifact_paths(self, output_dir: str | Path) -> dict[str, Path]:
        root = Path(output_dir)
        return {
            "snapshot": root / self.snapshot_filename,
            "manifest": root / self.manifest_filename,
            "ranking": root / self.ranking_filename,
            "release_summary": root / self.release_summary_filename,
        }


_PROFILE_CONTRACTS = {
    CN_DIVIDEND_QUALITY_SNAPSHOT_PROFILE: SnapshotProfileContract(
        profile=CN_DIVIDEND_QUALITY_SNAPSHOT_PROFILE,
        display_name="CN Dividend Quality Snapshot",
        contract_version="cn_dividend_quality_snapshot.factor_snapshot.v1",
        snapshot_filename="cn_dividend_quality_snapshot_factor_snapshot_latest.csv",
        manifest_filename="cn_dividend_quality_snapshot_factor_snapshot_latest.csv.manifest.json",
        ranking_filename="cn_dividend_quality_snapshot_ranking_latest.csv",
        legacy_aliases=(),
        neutral_gcs_prefix_hint=(
            "gs://qsl-runtime-logs-shared/strategy-artifacts/cn_equity/cn_dividend_quality_snapshot"
        ),
        manifest_required_by_runtime=True,
    ),
}

_ALIAS_TO_PROFILE = {
    alias: contract.profile
    for contract in _PROFILE_CONTRACTS.values()
    for alias in (contract.profile, *contract.legacy_aliases)
}


def get_profile_contract(profile: str) -> SnapshotProfileContract:
    normalized = str(profile or "").strip().lower().replace("-", "_")
    canonical = _ALIAS_TO_PROFILE.get(normalized)
    if canonical is None:
        known = ", ".join(sorted(_PROFILE_CONTRACTS))
        raise ValueError(f"Unknown snapshot profile {profile!r}; known profiles: {known}")
    return _PROFILE_CONTRACTS[canonical]


def list_profile_contracts() -> tuple[SnapshotProfileContract, ...]:
    return tuple(_PROFILE_CONTRACTS.values())
