"""Synthetic local-export fixtures; no market data or provider requests."""

from dataclasses import replace
from hashlib import sha256
import json
from pathlib import Path

import pytest

from cn_equity_snapshot_pipelines import index_etf_input_publish as module
from cn_equity_snapshot_pipelines.research_input_publish import OfficialLicenseEvidence, ProvenanceError
from cn_equity_strategies.backtest.index_etf_strict_runner import load_index_etf_input


def setup_input(tmp_path, monkeypatch):
    days = ["2024-01-02", "2024-01-03", "2024-01-04"]
    rows = [
        dict(
            date=day,
            symbol=symbol,
            open=10,
            high=11,
            low=9,
            close=10,
            volume=100000,
            suspended=False,
            limit_up=11,
            limit_down=9,
            status_known_at=day + "T09:00:00+08:00",
            available_at=day + "T15:01:00+08:00",
        )
        for day in days
        for symbol in ("510300", "510500")
    ]
    values = {
        "daily": rows,
        "calendar": {"start_date": days[0], "end_date": days[-1], "sessions": days},
        "actions": {"complete_from": days[0], "complete_through": days[-1], "events": []},
    }
    paths = {}
    for name, value in values.items():
        paths[name] = tmp_path / (name + ".json")
        paths[name].write_text(json.dumps(value))
    primary, backup = tmp_path / "primary", tmp_path / "backup"
    for p in (primary, backup):
        p.mkdir(mode=0o700)
    monkeypatch.setattr(module.storage, "_device_id", lambda p: 1 if Path(p) == primary else 2)
    monkeypatch.setattr(
        module.storage,
        "_derive_producer_identity",
        lambda: {
            "repository": "QuantStrategyLab/CnEquitySnapshotPipelines",
            "commit_sha": "a" * 40,
            "tree_sha": "b" * 40,
            "tool": "synthetic-fixture",
            "tool_version": "1",
        },
    )
    args = dict(
        daily_path=paths["daily"],
        calendar_path=paths["calendar"],
        actions_path=paths["actions"],
        primary_root=primary,
        backup_root=backup,
        source_id="synthetic-fixture",
        source_revision="fixture-v1",
        source_observed_at="2024-01-04T16:00:00+08:00",
        license_evidence=OfficialLicenseEvidence(
            b"Synthetic permission fixture, never used as a real data license.",
            "text/plain",
            "official:synthetic-fixture",
            "fixture-v1",
            "private-retention-permitted",
        ),
    )
    return args, paths


def test_real_consumer_reads_published_synthetic_package_and_exact_raw_files(tmp_path, monkeypatch):
    args, paths = setup_input(tmp_path, monkeypatch)
    receipt = module.publish_index_etf_input(**args)
    files = module.storage._package_bytes(receipt.primary_path)
    manifest = files.pop(module.storage.MANIFEST_FILENAME)
    data = load_index_etf_input(manifest, files, expected_manifest_sha256=receipt.manifest_sha256)
    assert data.evidence_kind == "synthetic" and len(data.sessions) == 3
    assert files["raw/daily.json"] == paths["daily"].read_bytes()
    assert module.storage._package_bytes(receipt.primary_path) == module.storage._package_bytes(receipt.backup_path)
    decoded = json.loads(manifest)
    assert all(item["source_id"].startswith("local-import:") for item in decoded["sources"])
    assert decoded["producer"]["tool"] == "cn_equity_snapshot_pipelines.index_etf_input_publish"


@pytest.mark.parametrize("failure", ["bare_license", "missing_session", "duplicate_key", "future_observed", "symlink"])
def test_bad_local_input_stops_before_publication(tmp_path, monkeypatch, failure):
    args, paths = setup_input(tmp_path, monkeypatch)
    if failure == "bare_license":
        args["license_evidence"] = replace(args["license_evidence"], content=b"approved")
    elif failure == "missing_session":
        rows = json.loads(paths["daily"].read_text())
        rows.pop()
        paths["daily"].write_text(json.dumps(rows))
    elif failure == "duplicate_key":
        paths["calendar"].write_text('{"sessions":[],"sessions":[]}')
    elif failure == "future_observed":
        args["source_observed_at"] = "2999-01-01T00:00:00Z"
    else:
        link = tmp_path / "linked.json"
        link.symlink_to(paths["daily"])
        args["daily_path"] = link
    with pytest.raises(ProvenanceError):
        module.publish_index_etf_input(**args)
    assert list(args["primary_root"].iterdir()) == [] and list(args["backup_root"].iterdir()) == []


def test_repeat_import_is_identical_and_tampered_destination_rejected(tmp_path, monkeypatch):
    args, _ = setup_input(tmp_path, monkeypatch)
    first = module.publish_index_etf_input(**args)
    second = module.publish_index_etf_input(**args)
    assert first == second
    (first.primary_path / "raw/daily.json").write_bytes(b"changed synthetic fixture")
    with pytest.raises(ProvenanceError):
        module.publish_index_etf_input(**args)


def test_historical_declaration_remains_unverified_without_approved_job_root(tmp_path, monkeypatch):
    args, _ = setup_input(tmp_path, monkeypatch)
    receipt = module.publish_index_etf_input(**args, evidence_kind="historical")
    manifest = json.loads((receipt.primary_path / module.storage.MANIFEST_FILENAME).read_bytes())
    assert manifest["artifact_type"] == "cn_index_etf_execution_history"
    assert not any(key in manifest for key in ("promotion_eligible", "live_ready", "trusted"))
    assert (
        receipt.manifest_sha256
        == sha256((receipt.primary_path / module.storage.MANIFEST_FILENAME).read_bytes()).hexdigest()
    )


def test_write_failure_cannot_return_a_successful_receipt(tmp_path, monkeypatch):
    args, _ = setup_input(tmp_path, monkeypatch)

    def fail(*args):
        raise OSError("synthetic failure")

    monkeypatch.setattr(module.storage, "_fsync_tree", fail)
    with pytest.raises(ProvenanceError, match="publication_failed"):
        module.publish_index_etf_input(**args)
    assert not list(args["primary_root"].rglob("research_input_manifest.v1.json"))
