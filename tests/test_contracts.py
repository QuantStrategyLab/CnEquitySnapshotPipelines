from __future__ import annotations

from cn_equity_snapshot_pipelines.contracts import (
    CN_DIVIDEND_QUALITY_SNAPSHOT_PROFILE,
    get_profile_contract,
    list_profile_contracts,
)


def test_contract_surface_includes_strategy_and_index_membership():
    contracts = list_profile_contracts()
    profiles = [c.profile for c in contracts]

    assert CN_DIVIDEND_QUALITY_SNAPSHOT_PROFILE in profiles
    assert "cn_csi500_membership" in profiles
    assert "cn_csi300_membership" in profiles
    assert "cn_csi1000_membership" in profiles
    assert len(profiles) == 4

    contract = get_profile_contract("cn-dividend-quality-snapshot")
    assert contract.contract_version == "cn_dividend_quality_snapshot.factor_snapshot.v1"
    assert contract.snapshot_filename == "cn_dividend_quality_snapshot_factor_snapshot_latest.csv"
    assert contract.manifest_required_by_runtime is True

    csi500 = get_profile_contract("cn_csi500_membership")
    assert csi500.contract_version == "cn_csi500_membership.timeline.v1"
    assert csi500.manifest_required_by_runtime is False
