from __future__ import annotations

from cn_equity_snapshot_pipelines.contracts import (
    CN_DIVIDEND_QUALITY_SNAPSHOT_PROFILE,
    get_profile_contract,
    list_profile_contracts,
)


def test_contract_surface_keeps_only_dividend_quality_snapshot():
    contracts = list_profile_contracts()

    assert [contract.profile for contract in contracts] == [CN_DIVIDEND_QUALITY_SNAPSHOT_PROFILE]
    contract = get_profile_contract("cn-dividend-quality-snapshot")
    assert contract.contract_version == "cn_dividend_quality_snapshot.factor_snapshot.v1"
    assert contract.snapshot_filename == "cn_dividend_quality_snapshot_factor_snapshot_latest.csv"
    assert contract.manifest_required_by_runtime is True
