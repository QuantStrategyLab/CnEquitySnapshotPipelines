"""P3-0 producer provenance contract traceability and offline acceptance tests.

Traceability matrix:
- §6.1 exact five-file/QPK pin/CLI owner surface: dependency and CLI tests.
- §6.2 private two-device roots/append-only layout: root and collision tests.
- §6.3 raw/request/normalized/calendar/license/adjustment members: manifest/member tests.
- §6.4 staging -> fsync -> atomic final -> backup: publication and failure-injection tests.
- §6.5 capture/coverage/identity/negative stops: capture and rejection tests below.
- Prior P1 dependency pin: pyproject/lock unique-pin test.
- Prior P1 truncated/range coverage: truncated/out-of-range/missing-chunk tests.
- Prior P1 producer identity: clean-checkout-derived identity tests and public API check.
- Prior P2 observed media type: exact response Content-Type test.
- Prior P2 staging readback: first-readback failure injection test.
- Prior P2 primary/backup final readback: second/fourth-readback failure tests.

All HTTP responses are synthetic producer-behaviour fixtures. They are never P3 input.
"""

from __future__ import annotations

from dataclasses import replace
from datetime import datetime, timezone
from hashlib import sha256
import inspect
import json
import re
from pathlib import Path
import subprocess
from typing import Callable

import pytest

from cn_equity_snapshot_pipelines import research_input_publish as module
from cn_equity_snapshot_pipelines.research_input_publish import (
    CaptureRun,
    OfficialAdjustmentEvidence,
    OfficialLicenseEvidence,
    ProvenanceError,
    capture_tencent_history_run,
    publish_research_input,
)
from quant_platform_kit.data.research_input import (
    canonical_research_input_manifest_bytes,
    read_research_input_manifest_json,
    research_input_manifest_sha256,
)


NOW = datetime(2026, 8, 1, 9, 30, tzinfo=timezone.utc)


class FakeResponse:
    def __init__(
        self,
        content: bytes,
        *,
        content_type: str = "application/json; charset=utf-8",
        status_error: Exception | None = None,
    ) -> None:
        self.content = content
        self.headers = {"Content-Type": content_type}
        self._status_error = status_error

    def raise_for_status(self) -> None:
        if self._status_error is not None:
            raise self._status_error

    def json(self) -> dict[str, object]:
        raise AssertionError("capture must parse the exact response.content bytes")


def _raw(
    symbol: str = "510300",
    *,
    dates: tuple[str, ...] = ("2026-07-30", "2026-07-31", "2026-08-01"),
    key: str = "qfqday",
    extra_symbol: str | None = None,
) -> bytes:
    provider_symbol = module.tencent_symbol(symbol)
    data: dict[str, object] = {
        provider_symbol: {key: [[date, "10", str(11 + index)] for index, date in enumerate(dates)]}
    }
    if extra_symbol:
        data[module.tencent_symbol(extra_symbol)] = {key: [[dates[0], "10", "11"]]}
    return json.dumps({"data": data}, separators=(",", ":")).encode()


def _capture(
    *,
    raw: bytes | None = None,
    content_type: str = "application/json; charset=utf-8",
    start_date: str = "20260730",
    end_date: str = "20260801",
) -> CaptureRun:
    def get_response(*args, **kwargs):
        provider_symbol = kwargs["params"]["param"].split(",", 1)[0]
        symbol = provider_symbol[2:]
        payload = raw if raw is not None and symbol == "510300" else _raw(symbol)
        return FakeResponse(payload, content_type=content_type)

    return capture_tencent_history_run(
        ("510300", "510500"),
        start_date=start_date,
        end_date=end_date,
        http_get=get_response,
        clock=lambda: NOW,
        retry_delay_seconds=0,
    )


def _license() -> OfficialLicenseEvidence:
    return OfficialLicenseEvidence(
        content=b"Official terms permit private local retention for this account.",
        media_type="text/plain",
        source_identity="official:tencent-market-data-terms",
        revision="terms-observed-2026-08-01",
        retention_scope="private-retention-permitted",
    )


def _adjustment() -> OfficialAdjustmentEvidence:
    return OfficialAdjustmentEvidence(
        content=b"Official qfq semantics unambiguously map to split-adjusted prices.",
        media_type="text/plain",
        source_identity="official:tencent-qfq-semantics",
        revision="semantics-observed-2026-08-01",
        policy="split_adjusted",
    )


def _roots(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> tuple[Path, Path]:
    primary = tmp_path / "primary"
    backup = tmp_path / "backup"
    primary.mkdir(mode=0o700, exist_ok=True)
    backup.mkdir(mode=0o700, exist_ok=True)
    primary.chmod(0o700)
    backup.chmod(0o700)
    monkeypatch.setattr(module, "_device_id", lambda path: 1 if Path(path) == primary else 2)
    return primary, backup


def _producer() -> dict[str, str]:
    return {
        "repository": "QuantStrategyLab/CnEquitySnapshotPipelines",
        "commit_sha": "a" * 40,
        "tree_sha": "b" * 40,
        "tool": "cn_equity_snapshot_pipelines.research_input_publish",
        "tool_version": "0.1.0",
    }


def _publish(
    tmp_path: Path,
    monkeypatch: pytest.MonkeyPatch,
    *,
    capture: CaptureRun | None = None,
):
    primary, backup = _roots(tmp_path, monkeypatch)
    monkeypatch.setattr(module, "_derive_producer_identity", _producer)
    receipt = publish_research_input(
        capture or _capture(),
        primary_root=primary,
        backup_root=backup,
        license_evidence=_license(),
        adjustment_evidence=_adjustment(),
    )
    return receipt, primary, backup


def test_capture_preserves_exact_bytes_and_observed_media_type() -> None:
    raw = _raw()
    capture = _capture(raw=raw, content_type="application/vnd.tencent+json; charset=utf-8")
    chunk = capture.chunks[0]

    assert chunk.raw_bytes == raw
    assert chunk.response_media_type == "application/vnd.tencent+json; charset=utf-8"
    identity = json.loads(chunk.request_identity_bytes)
    assert identity["response_media_type"] == chunk.response_media_type
    assert identity["method"] == "GET"
    assert identity["symbol"] == "510300"
    assert identity["date_chunk"] == {"end": "2026-08-01", "start": "2026-07-30"}


def test_request_identity_is_canonical_deterministic_and_non_sensitive() -> None:
    first = _capture().chunks[0].request_identity_bytes
    second = _capture().chunks[0].request_identity_bytes
    assert first == second
    assert first == json.dumps(json.loads(first), sort_keys=True, separators=(",", ":")).encode()
    lowered = first.lower()
    for forbidden in (b"header", b"cookie", b"authorization", b"token", b"secret", b"user-agent"):
        assert forbidden not in lowered


def test_capture_rejects_http_json_empty_fallback_and_media_type_failures() -> None:
    with pytest.raises(RuntimeError, match="HTTP"):
        capture_tencent_history_run(
            ("510300", "510500"),
            start_date="20260730",
            end_date="20260801",
            http_get=lambda *args, **kwargs: FakeResponse(b"{}", status_error=RuntimeError("HTTP 500")),
            clock=lambda: NOW,
            max_attempts=1,
            retry_delay_seconds=0,
        )
    with pytest.raises(ProvenanceError, match="JSON"):
        _capture(raw=b"not-json")
    with pytest.raises(ProvenanceError, match="empty"):
        _capture(raw=_raw(dates=()))
    with pytest.raises(ProvenanceError, match="fallback"):
        _capture(raw=_raw(key="day"))
    with pytest.raises(ProvenanceError, match="Content-Type"):
        _capture(content_type=" ")


@pytest.mark.parametrize("close", ["NaN", "Infinity", '"NaN"', '"-Infinity"', "0", "-1"])
def test_capture_rejects_nonfinite_or_nonpositive_close(close: str) -> None:
    raw = (
        '{"data":{"sh510300":{"qfqday":'
        f'[["2026-07-30","10",{close}],["2026-07-31","10",11],["2026-08-01","10",12]]'
        "}}}"
    ).encode()
    with pytest.raises(ProvenanceError, match="close|JSON"):
        _capture(raw=raw)


def test_capture_requires_canonical_symbol_before_http_and_identity() -> None:
    calls = 0

    def forbidden_http(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("invalid symbol must stop before HTTP")

    with pytest.raises(ProvenanceError, match="canonical") as exc:
        capture_tencent_history_run(
            ("TOKEN-SECRET", "510300"),
            start_date="20260730",
            end_date="20260801",
            http_get=forbidden_http,
            clock=lambda: NOW,
        )
    assert calls == 0
    assert "TOKEN-SECRET" not in str(exc.value)


def test_capture_rejects_interior_missing_session_by_exact_consensus() -> None:
    complete = ("2026-07-27", "2026-07-28", "2026-07-29", "2026-07-30", "2026-07-31")

    def response(*args, **kwargs):
        symbol = kwargs["params"]["param"].split(",", 1)[0][2:]
        dates = complete if symbol == "510300" else complete[:2] + complete[3:]
        return FakeResponse(_raw(symbol, dates=dates))

    with pytest.raises(ProvenanceError, match="session coverage"):
        capture_tencent_history_run(
            ("510300", "510500"),
            start_date="20260727",
            end_date="20260731",
            http_get=response,
            clock=lambda: NOW,
            retry_delay_seconds=0,
        )


@pytest.mark.parametrize(
    "raw,match",
    [
        (_raw(dates=("2026-07-31", "2026-08-01")), "truncated"),
        (_raw(dates=("2026-07-29", "2026-07-30", "2026-07-31", "2026-08-01")), "out-of-range"),
        (_raw(extra_symbol="510500"), "wrong symbol"),
        (_raw(dates=("2026-07-30", "2026-07-30", "2026-08-01")), "duplicate"),
    ],
)
def test_capture_rejects_truncated_out_of_range_wrong_and_duplicate_rows(raw: bytes, match: str) -> None:
    with pytest.raises(ProvenanceError, match=match):
        _capture(raw=raw)


def test_publish_rejects_missing_or_duplicate_chunk_and_partial_symbol_run(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    capture = _capture()
    primary, backup = _roots(tmp_path, monkeypatch)
    monkeypatch.setattr(module, "_derive_producer_identity", _producer)

    for invalid in (
        replace(capture, chunks=()),
        replace(capture, chunks=(capture.chunks[0], capture.chunks[0])),
        replace(capture, requested_symbols=("510300", "510500", "159915")),
    ):
        with pytest.raises(ProvenanceError, match="chunk accounting"):
            publish_research_input(
                invalid,
                primary_root=primary,
                backup_root=backup,
                license_evidence=_license(),
                adjustment_evidence=_adjustment(),
            )


@pytest.mark.parametrize("kind", ["synthetic_fixture", "expiring_artifact", "summary_metrics"])
def test_publish_rejects_non_provider_input_kinds(
    kind: str, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    capture = _capture()
    invalid_chunk = replace(capture.chunks[0], input_kind=kind)
    with pytest.raises(ProvenanceError, match="provider HTTP response"):
        _publish(
            tmp_path,
            monkeypatch,
            capture=replace(capture, chunks=(invalid_chunk, *capture.chunks[1:])),
        )


def test_publish_rejects_fallback_and_tampered_request_identity(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    capture = _capture()
    for invalid_chunk in (
        replace(capture.chunks[0], fallback_used=True),
        replace(capture.chunks[0], request_identity_bytes=b'{"token":"sensitive"}'),
    ):
        with pytest.raises(ProvenanceError):
            _publish(
                tmp_path,
                monkeypatch,
                capture=replace(capture, chunks=(invalid_chunk, *capture.chunks[1:])),
            )


def test_manifest_binds_all_members_digests_sources_and_qpk_canonical_bytes(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    receipt, _, _ = _publish(tmp_path, monkeypatch)
    manifest_bytes = (receipt.primary_path / module.MANIFEST_FILENAME).read_bytes()
    manifest = read_research_input_manifest_json(manifest_bytes)

    assert manifest_bytes == canonical_research_input_manifest_bytes(manifest)
    assert receipt.manifest_sha256 == research_input_manifest_sha256(manifest)
    paths = {member["path"] for member in manifest["members"]}
    assert any(path.startswith("raw/") for path in paths)
    assert any(path.startswith("requests/") for path in paths)
    assert {
        "normalized/history.csv",
        "calendar/observed_sessions.csv",
        "evidence/license.bin",
        "evidence/adjustment_semantics.bin",
    } <= paths
    for member in manifest["members"]:
        payload = (receipt.primary_path / member["path"]).read_bytes()
        assert member["size_bytes"] == len(payload)
        assert member["sha256"] == sha256(payload).hexdigest()
    raw_member = next(member for member in manifest["members"] if member["path"].startswith("raw/"))
    assert manifest["sources"][0]["content_sha256"] == raw_member["sha256"]
    assert manifest["sources"][0]["revision"] == f"producer-observed-response-sha256:{raw_member['sha256']}"
    assert manifest["producer"] == _producer()
    assert manifest["adjustment"]["policy"] == "split_adjusted"
    assert "request-set-sha256:" in manifest["adjustment"]["source_revision"]


def test_sources_and_members_are_strictly_sorted_and_unique(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    receipt, _, _ = _publish(tmp_path, monkeypatch)
    manifest = read_research_input_manifest_json((receipt.primary_path / module.MANIFEST_FILENAME).read_bytes())
    source_ids = [source["source_id"] for source in manifest["sources"]]
    member_paths = [member["path"] for member in manifest["members"]]
    assert source_ids == sorted(set(source_ids))
    assert member_paths == sorted(set(member_paths))


def test_publish_is_append_only_and_existing_digest_requires_byte_equivalence(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    receipt, _, _ = _publish(tmp_path, monkeypatch)
    second = publish_research_input(
        _capture(),
        primary_root=receipt.primary_path.parents[3],
        backup_root=receipt.backup_path.parents[3],
        license_evidence=_license(),
        adjustment_evidence=_adjustment(),
    )
    assert second.primary_path == receipt.primary_path

    raw_path = next(receipt.primary_path.glob("raw/*"))
    raw_path.write_bytes(b"collision")
    with pytest.raises(ProvenanceError, match="collision"):
        publish_research_input(
            _capture(),
            primary_root=receipt.primary_path.parents[3],
            backup_root=receipt.backup_path.parents[3],
            license_evidence=_license(),
            adjustment_evidence=_adjustment(),
        )


def _inject_readback_failure(
    monkeypatch: pytest.MonkeyPatch, call_number: int
) -> Callable[[Path, str], dict[str, object]]:
    original = module._readback_package
    calls = 0

    def failing(path: Path, expected_digest: str):
        nonlocal calls
        calls += 1
        if calls == call_number:
            raise ProvenanceError(f"injected readback failure {call_number}")
        return original(path, expected_digest)

    monkeypatch.setattr(module, "_readback_package", failing)
    return failing


@pytest.mark.parametrize("call_number", [1, 2, 4])
def test_staging_primary_final_and_backup_final_readback_fail_closed(
    call_number: int, tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    _inject_readback_failure(monkeypatch, call_number)
    with pytest.raises(ProvenanceError, match="injected readback failure"):
        _publish(tmp_path, monkeypatch)


def test_fsync_failure_is_fatal(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    monkeypatch.setattr(module.os, "fsync", lambda fd: (_ for _ in ()).throw(OSError("fsync failed")))
    with pytest.raises(OSError, match="fsync failed"):
        _publish(tmp_path, monkeypatch)


def test_roots_must_preexist_be_absolute_nonsymlink_outside_repo_and_different_device(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    primary = tmp_path / "primary"
    backup = tmp_path / "backup"
    primary.mkdir()
    backup.mkdir()
    primary.chmod(0o700)
    backup.chmod(0o700)
    monkeypatch.setattr(module, "_derive_producer_identity", _producer)

    cases = [
        (Path("relative-primary"), backup, "absolute"),
        (tmp_path / "missing", backup, "pre-existing"),
        (primary, primary, "distinct"),
    ]
    for left, right, match in cases:
        with pytest.raises(ProvenanceError, match=match):
            publish_research_input(
                _capture(),
                primary_root=left,
                backup_root=right,
                license_evidence=_license(),
                adjustment_evidence=_adjustment(),
            )

    symlink = tmp_path / "linked"
    symlink.symlink_to(primary, target_is_directory=True)
    with pytest.raises(ProvenanceError, match="symlink"):
        publish_research_input(
            _capture(),
            primary_root=symlink,
            backup_root=backup,
            license_evidence=_license(),
            adjustment_evidence=_adjustment(),
        )

    monkeypatch.setattr(module, "_device_id", lambda path: 1)
    with pytest.raises(ProvenanceError, match="devices"):
        publish_research_input(
            _capture(),
            primary_root=primary,
            backup_root=backup,
            license_evidence=_license(),
            adjustment_evidence=_adjustment(),
        )

    primary.chmod(0o755)
    monkeypatch.setattr(module, "_device_id", lambda path: 1 if Path(path) == primary else 2)
    with pytest.raises(ProvenanceError, match="private"):
        publish_research_input(
            _capture(),
            primary_root=primary,
            backup_root=backup,
            license_evidence=_license(),
            adjustment_evidence=_adjustment(),
        )


def test_root_rejects_symlink_ancestor_and_repository_path(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    real_parent = tmp_path / "real"
    real_parent.mkdir(mode=0o700)
    primary = real_parent / "primary"
    primary.mkdir(mode=0o700)
    linked_parent = tmp_path / "linked-parent"
    linked_parent.symlink_to(real_parent, target_is_directory=True)
    backup = tmp_path / "backup"
    backup.mkdir(mode=0o700)
    monkeypatch.setattr(module, "_derive_producer_identity", _producer)
    monkeypatch.setattr(module, "_device_id", lambda path: 1 if "primary" in str(path) else 2)

    with pytest.raises(ProvenanceError, match="symlink"):
        publish_research_input(
            _capture(),
            primary_root=linked_parent / "primary",
            backup_root=backup,
            license_evidence=_license(),
            adjustment_evidence=_adjustment(),
        )
    with pytest.raises(ProvenanceError, match="outside"):
        publish_research_input(
            _capture(),
            primary_root=module._repo_root(),
            backup_root=backup,
            license_evidence=_license(),
            adjustment_evidence=_adjustment(),
        )


def test_license_and_adjustment_require_exact_official_evidence_not_boolean_ack(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    primary, backup = _roots(tmp_path, monkeypatch)
    monkeypatch.setattr(module, "_derive_producer_identity", _producer)
    for license_evidence, adjustment_evidence in (
        (None, _adjustment()),
        (True, _adjustment()),
        (_license(), None),
        (_license(), True),
        (replace(_license(), content=b"true"), _adjustment()),
        (_license(), replace(_adjustment(), content=b"ack")),
        (_license(), replace(_adjustment(), policy="raw")),
    ):
        with pytest.raises(ProvenanceError, match="official evidence"):
            publish_research_input(
                _capture(),
                primary_root=primary,
                backup_root=backup,
                license_evidence=license_evidence,  # type: ignore[arg-type]
                adjustment_evidence=adjustment_evidence,  # type: ignore[arg-type]
            )


def test_public_publish_api_has_no_caller_supplied_digest_or_producer_identity() -> None:
    parameters = set(inspect.signature(publish_research_input).parameters)
    assert not parameters & {
        "producer_commit",
        "producer_tree",
        "producer_identity",
        "raw_sha256",
        "manifest_sha256",
    }
    assert not inspect.signature(module._derive_producer_identity).parameters


def test_producer_identity_is_derived_from_clean_trusted_checkout_and_dirty_fails(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    repo = tmp_path / "checkout"
    source = repo / "src/cn_equity_snapshot_pipelines/research_input_publish.py"
    source.parent.mkdir(parents=True)
    source.write_text("# committed producer\n", encoding="utf-8")
    subprocess.run(["git", "init", "-q", "-b", "main", repo], check=True)
    subprocess.run(["git", "-C", repo, "config", "user.email", "test@example.invalid"], check=True)
    subprocess.run(["git", "-C", repo, "config", "user.name", "test"], check=True)
    subprocess.run(
        [
            "git",
            "-C",
            repo,
            "remote",
            "add",
            "origin",
            "https://github.com/QuantStrategyLab/CnEquitySnapshotPipelines.git",
        ],
        check=True,
    )
    subprocess.run(["git", "-C", repo, "add", "."], check=True)
    subprocess.run(["git", "-C", repo, "commit", "-q", "-m", "fixture"], check=True)
    monkeypatch.setattr(module, "__file__", str(source))

    identity = module._derive_producer_identity()
    assert identity["commit_sha"] == subprocess.check_output(
        ["git", "-C", repo, "rev-parse", "HEAD"], text=True
    ).strip()
    assert identity["tree_sha"] == subprocess.check_output(
        ["git", "-C", repo, "rev-parse", "HEAD^{tree}"], text=True
    ).strip()

    source.write_text("# dirty producer\n", encoding="utf-8")
    with pytest.raises(ProvenanceError, match="dirty"):
        module._derive_producer_identity()


def test_pyproject_lock_resolve_one_canonical_qpk_pin() -> None:
    root = Path(__file__).resolve().parents[1]
    expected = "ff70b162ac8e50e1ece617e570dab76b6740d41e"
    pyproject = (root / "pyproject.toml").read_text(encoding="utf-8")
    lock = (root / "uv.lock").read_text(encoding="utf-8")
    assert f"QuantPlatformKit.git@{expected}" in pyproject
    assert f"QuantPlatformKit.git?rev={expected}" in lock
    assert pyproject.count("QuantPlatformKit.git@") == 1
    assert lock.count('\n[[package]]\nname = "quant-platform-kit"\n') == 1
    revisions = re.findall(r"QuantPlatformKit\.git\?rev=([0-9a-f]{40})", lock)
    assert revisions and set(revisions) == {expected}
    assert "01877daef888376238337d7dc56873fdbac4a92a" not in pyproject + lock


def test_no_sensitive_values_are_logged_or_stored(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch, capsys: pytest.CaptureFixture[str]
) -> None:
    secret = "super-secret-token-value"
    receipt, _, _ = _publish(tmp_path, monkeypatch)
    assert secret not in capsys.readouterr().out
    for path in receipt.primary_path.rglob("*"):
        if path.is_file():
            assert secret.encode() not in path.read_bytes()


def test_cli_help_is_offline_and_requires_explicit_roots(capsys: pytest.CaptureFixture[str]) -> None:
    with pytest.raises(SystemExit) as exc:
        module.main(["--help"])
    assert exc.value.code == 0
    help_text = capsys.readouterr().out
    assert "--vault-root" in help_text
    assert "--backup-vault-root" in help_text
    assert "--license-evidence" in help_text
    assert "--adjustment-evidence" in help_text


def test_cli_preflight_failures_stop_before_provider_call(
    tmp_path: Path, monkeypatch: pytest.MonkeyPatch
) -> None:
    primary, backup = _roots(tmp_path, monkeypatch)
    license_path = tmp_path / "license.txt"
    adjustment_path = tmp_path / "adjustment.txt"
    license_path.write_bytes(_license().content)
    adjustment_path.write_bytes(_adjustment().content)
    calls = 0

    def forbidden_capture(*args, **kwargs):
        nonlocal calls
        calls += 1
        raise AssertionError("provider capture must not run")

    monkeypatch.setattr(module, "capture_tencent_history_run", forbidden_capture)
    monkeypatch.setattr(
        module,
        "_derive_producer_identity",
        lambda: (_ for _ in ()).throw(ProvenanceError("producer checkout is dirty")),
    )
    argv = [
        "--vault-root", str(primary),
        "--backup-vault-root", str(backup),
        "--symbols", "510300,510500",
        "--start-date", "20260730",
        "--end-date", "20260801",
        "--license-evidence", str(license_path),
        "--license-media-type", "text/plain",
        "--license-source", "official:tencent-market-data-terms",
        "--license-revision", "terms-observed-2026-08-01",
        "--license-scope", "private-retention-permitted",
        "--adjustment-evidence", str(adjustment_path),
        "--adjustment-media-type", "text/plain",
        "--adjustment-source", "official:tencent-qfq-semantics",
        "--adjustment-revision", "semantics-observed-2026-08-01",
        "--adjustment-policy", "split_adjusted",
    ]
    with pytest.raises(ProvenanceError, match="dirty"):
        module.main(argv)
    assert calls == 0
