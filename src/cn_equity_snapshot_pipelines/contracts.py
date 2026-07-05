from __future__ import annotations

from dataclasses import dataclass
from pathlib import Path

SOURCE_PROJECT = "CnEquitySnapshotPipelines"
CN_DIVIDEND_QUALITY_SNAPSHOT_PROFILE = "cn_dividend_quality_snapshot"
CN_CHINEXT_GROWTH_MOMENTUM_QUALITY_SNAPSHOT_PROFILE = "cn_chinext_growth_momentum_quality_snapshot"

# Index metadata shared by contracts and index_membership.
INDEX_METADATA: dict[str, dict[str, str]] = {
    "000300": {"name": "CSI300", "display": "沪深300"},
    "000905": {"name": "CSI500", "display": "中证500"},
    "000852": {"name": "CSI1000", "display": "中证1000"},
}


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
    CN_CHINEXT_GROWTH_MOMENTUM_QUALITY_SNAPSHOT_PROFILE: SnapshotProfileContract(
        profile=CN_CHINEXT_GROWTH_MOMENTUM_QUALITY_SNAPSHOT_PROFILE,
        display_name="CN ChiNext Growth Momentum Quality Snapshot",
        contract_version="cn_chinext_growth_momentum_quality_snapshot.factor_snapshot.v1",
        snapshot_filename="cn_chinext_growth_momentum_quality_snapshot_factor_snapshot_latest.csv",
        manifest_filename="cn_chinext_growth_momentum_quality_snapshot_factor_snapshot_latest.csv.manifest.json",
        ranking_filename="cn_chinext_growth_momentum_quality_snapshot_ranking_latest.csv",
        legacy_aliases=(),
        neutral_gcs_prefix_hint=(
            "gs://qsl-runtime-logs-shared/strategy-artifacts/cn_equity/cn_chinext_growth_momentum_quality_snapshot"
        ),
        manifest_required_by_runtime=False,
    ),
}

# Index membership timeline profiles (data-pipeline contracts, not runtime strategies).
_INDEX_MEMBERSHIP_PROFILES: dict[str, SnapshotProfileContract] = {
    f"cn_{meta['name'].lower()}_membership": SnapshotProfileContract(
        profile=f"cn_{meta['name'].lower()}_membership",
        display_name=f"CN {meta['name']} Index Membership Timeline",
        contract_version=f"cn_{meta['name'].lower()}_membership.timeline.v1",
        snapshot_filename=f"cn_{meta['name'].lower()}_membership_timeline.csv",
        manifest_filename=f"cn_{meta['name'].lower()}_membership_timeline.csv.manifest.json",
        ranking_filename="",
        release_summary_filename="release_status_summary.json",
        manifest_required_by_runtime=False,
    )
    for code, meta in INDEX_METADATA.items()
}

# Merge index membership profiles into the lookup (but keep them separate
# from strategy profiles for manifest/artifact dispatching).
_PROFILE_CONTRACTS.update(_INDEX_MEMBERSHIP_PROFILES)

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
