"""Fail-closed publication of immutable CN ETF research-input packages."""

from __future__ import annotations

import argparse
import csv
from dataclasses import dataclass
from datetime import datetime, timezone
from hashlib import sha256
import importlib.metadata
import io
import json
import os
from pathlib import Path
import shutil
import stat
import subprocess
import tempfile
from typing import Any

import pandas as pd
from quant_platform_kit.data.research_input import (
    InvalidResearchInputEvidence,
    canonical_research_input_manifest_bytes,
    read_research_input_manifest_json,
    research_input_manifest_sha256,
)

from .akshare_market_history import (
    CaptureRun,
    CapturedChunk,
    ProvenanceCaptureError,
    capture_tencent_history_run,
    canonical_tencent_request_identity,
    expected_tencent_chunks,
    parse_tencent_captured_chunk,
    tencent_symbol as tencent_symbol,
)


ProvenanceError = ProvenanceCaptureError
MANIFEST_FILENAME = "research_input_manifest.v1.json"
REPOSITORY = "QuantStrategyLab/CnEquitySnapshotPipelines"
TOOL = "cn_equity_snapshot_pipelines.research_input_publish"
_BARE_ACKS = frozenset({b"1", b"ack", b"approved", b"false", b"ok", b"true", b"yes"})


@dataclass(frozen=True)
class OfficialLicenseEvidence:
    content: bytes
    media_type: str
    source_identity: str
    revision: str
    retention_scope: str


@dataclass(frozen=True)
class OfficialAdjustmentEvidence:
    content: bytes
    media_type: str
    source_identity: str
    revision: str
    policy: str


@dataclass(frozen=True)
class PublishReceipt:
    manifest_sha256: str
    primary_path: Path
    backup_path: Path


def _canonical_json_bytes(value: object) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False).encode("utf-8")


def _sha256(payload: bytes) -> str:
    return sha256(payload).hexdigest()


def _git(repo: Path, *args: str) -> str:
    try:
        return subprocess.check_output(
            ["git", "-C", str(repo), *args],
            stderr=subprocess.DEVNULL,
            text=True,
        ).strip()
    except (OSError, subprocess.CalledProcessError):
        raise ProvenanceError("producer checkout identity cannot be proven") from None


def _derive_producer_identity() -> dict[str, str]:
    """Derive immutable producer identity only from this module's clean checkout."""
    module_path = Path(__file__)
    if module_path.is_symlink() or not module_path.is_file():
        raise ProvenanceError("producer module path is untrusted")
    module_path = module_path.resolve()
    expected_root = module_path.parents[2]
    root = Path(_git(expected_root, "rev-parse", "--show-toplevel")).resolve()
    if root != expected_root.resolve() or root.is_symlink():
        raise ProvenanceError("producer checkout path is untrusted")
    origin = _git(root, "remote", "get-url", "origin")
    accepted_origins = {
        "https://github.com/QuantStrategyLab/CnEquitySnapshotPipelines.git",
        "git@github.com:QuantStrategyLab/CnEquitySnapshotPipelines.git",
    }
    if origin not in accepted_origins:
        raise ProvenanceError("producer checkout repository is untrusted")
    if _git(root, "status", "--porcelain", "--untracked-files=all"):
        raise ProvenanceError("producer checkout is dirty")
    relative_module = module_path.relative_to(root).as_posix()
    _git(root, "cat-file", "-e", f"HEAD:{relative_module}")
    commit_sha = _git(root, "rev-parse", "HEAD")
    tree_sha = _git(root, "rev-parse", "HEAD^{tree}")
    if len(commit_sha) != 40 or len(tree_sha) != 40:
        raise ProvenanceError("producer checkout identity cannot be proven")
    try:
        tool_version = importlib.metadata.version("cn-equity-snapshot-pipelines")
    except importlib.metadata.PackageNotFoundError:
        raise ProvenanceError("producer tool version cannot be proven") from None
    return {
        "repository": REPOSITORY,
        "commit_sha": commit_sha,
        "tree_sha": tree_sha,
        "tool": TOOL,
        "tool_version": tool_version,
    }


def _validate_official_evidence(
    license_evidence: OfficialLicenseEvidence,
    adjustment_evidence: OfficialAdjustmentEvidence,
) -> None:
    if not isinstance(license_evidence, OfficialLicenseEvidence) or not isinstance(
        adjustment_evidence, OfficialAdjustmentEvidence
    ):
        raise ProvenanceError("exact official evidence is required")
    for evidence in (license_evidence, adjustment_evidence):
        if (
            not isinstance(evidence.content, bytes)
            or not evidence.content.strip()
            or evidence.content.strip().lower() in _BARE_ACKS
            or not isinstance(evidence.media_type, str)
            or not evidence.media_type.strip()
            or not isinstance(evidence.source_identity, str)
            or not evidence.source_identity.startswith("official:")
            or not isinstance(evidence.revision, str)
            or not evidence.revision.strip()
        ):
            raise ProvenanceError("exact official evidence is required")
    if license_evidence.retention_scope != "private-retention-permitted":
        raise ProvenanceError("exact official evidence is required for private retention")
    if adjustment_evidence.policy not in {"split_adjusted", "total_return_adjusted"}:
        raise ProvenanceError("exact official evidence is required for adjustment mapping")


def _validate_capture_accounting(capture: CaptureRun) -> list[tuple[CapturedChunk, pd.DataFrame]]:
    if not isinstance(capture, CaptureRun):
        raise ProvenanceError("capture run is invalid")
    if len(capture.requested_symbols) < 2:
        raise ProvenanceError("capture chunk accounting requires cross-symbol session proof")
    expected = {
        (symbol, chunk_start, chunk_end)
        for symbol in capture.requested_symbols
        for chunk_start, chunk_end in expected_tencent_chunks(capture.start_date, capture.end_date)
    }
    actual = [(chunk.symbol, chunk.start_date, chunk.end_date) for chunk in capture.chunks]
    if len(actual) != len(set(actual)) or set(actual) != expected:
        raise ProvenanceError("capture chunk accounting is incomplete")
    parsed: list[tuple[CapturedChunk, pd.DataFrame]] = []
    for chunk in capture.chunks:
        expected_identity = canonical_tencent_request_identity(
            symbol=chunk.symbol,
            start_date=chunk.start_date,
            end_date=chunk.end_date,
            response_media_type=chunk.response_media_type,
        )
        if chunk.request_identity_bytes != expected_identity:
            raise ProvenanceError("canonical request identity mismatch")
        parsed.append((chunk, parse_tencent_captured_chunk(chunk)))
    rows = pd.concat([frame for _, frame in parsed], ignore_index=True)
    if rows.duplicated(["symbol", "date"]).any():
        raise ProvenanceError("duplicate normalized symbol/date row")
    observed_symbols = set(rows["symbol"].astype(str))
    if observed_symbols != set(capture.requested_symbols):
        raise ProvenanceError("capture chunk accounting has wrong symbols")
    session_sets: dict[tuple[str, str], list[set[str]]] = {}
    for chunk, frame in parsed:
        session_sets.setdefault((chunk.start_date, chunk.end_date), []).append(
            set(frame["date"].astype(str))
        )
    if any(
        len(sets) != len(capture.requested_symbols)
        or any(sessions != sets[0] for sessions in sets[1:])
        for sets in session_sets.values()
    ):
        raise ProvenanceError("exact cross-symbol session coverage cannot be proven")
    return parsed


def _normalized_csv(parsed: list[tuple[CapturedChunk, pd.DataFrame]]) -> bytes:
    frame = pd.concat([value for _, value in parsed], ignore_index=True)
    frame = frame.sort_values(["symbol", "date"], kind="stable")
    buffer = io.StringIO(newline="")
    writer = csv.writer(buffer, lineterminator="\n")
    writer.writerow(("date", "symbol", "close", "price_basis"))
    for row in frame.itertuples(index=False):
        writer.writerow((row.date, row.symbol, format(float(row.close), ".17g"), row.price_basis))
    return buffer.getvalue().encode("utf-8")


def _calendar_csv(parsed: list[tuple[CapturedChunk, pd.DataFrame]]) -> bytes:
    sessions = sorted(
        {
            str(date_value)
            for _, frame in parsed
            for date_value in frame["date"].astype(str).tolist()
        }
    )
    return ("session_date\n" + "".join(f"{session}\n" for session in sessions)).encode("utf-8")


def _parse_timestamp(value: str) -> datetime:
    try:
        parsed = datetime.fromisoformat(value.replace("Z", "+00:00"))
    except ValueError:
        raise ProvenanceError("invalid capture receipt timestamp") from None
    if parsed.tzinfo is None or parsed.utcoffset() is None:
        raise ProvenanceError("capture receipt timestamp must be timezone-aware")
    return parsed.astimezone(timezone.utc)


def _member(path: str, media_type: str, content: bytes) -> dict[str, object]:
    return {
        "path": path,
        "media_type": media_type,
        "size_bytes": len(content),
        "sha256": _sha256(content),
    }


def _build_package(
    capture: CaptureRun,
    parsed: list[tuple[CapturedChunk, pd.DataFrame]],
    *,
    producer: dict[str, str],
    license_evidence: OfficialLicenseEvidence,
    adjustment_evidence: OfficialAdjustmentEvidence,
) -> tuple[dict[str, bytes], str]:
    files: dict[str, bytes] = {}
    sources: list[dict[str, object]] = []
    request_digests: list[str] = []
    for chunk, _ in sorted(parsed, key=lambda item: (item[0].symbol, item[0].start_date, item[0].end_date)):
        stem = f"{chunk.symbol}-{chunk.start_date}-{chunk.end_date}"
        raw_path = f"raw/{stem}.bin"
        request_path = f"requests/{stem}.json"
        files[raw_path] = chunk.raw_bytes
        files[request_path] = chunk.request_identity_bytes
        raw_digest = _sha256(chunk.raw_bytes)
        request_digests.append(_sha256(chunk.request_identity_bytes))
        sources.append(
            {
                "source_id": f"tencent:{chunk.symbol}:{chunk.start_date}:{chunk.end_date}",
                "revision": f"producer-observed-response-sha256:{raw_digest}",
                "observed_at": chunk.received_at,
                "content_sha256": raw_digest,
            }
        )
    normalized = _normalized_csv(parsed)
    calendar = _calendar_csv(parsed)
    files["normalized/history.csv"] = normalized
    files["calendar/observed_sessions.csv"] = calendar
    files["evidence/license.bin"] = license_evidence.content
    files["evidence/license_identity.json"] = _canonical_json_bytes(
        {
            "content_sha256": _sha256(license_evidence.content),
            "media_type": license_evidence.media_type,
            "retention_scope": license_evidence.retention_scope,
            "revision": license_evidence.revision,
            "source_identity": license_evidence.source_identity,
        }
    )
    files["evidence/adjustment_semantics.bin"] = adjustment_evidence.content
    members = sorted(
        (
            _member(path, _media_type_for(path, parsed, license_evidence, adjustment_evidence), content)
            for path, content in files.items()
        ),
        key=lambda value: str(value["path"]),
    )
    observed = sorted(_parse_timestamp(chunk.received_at) for chunk, _ in parsed)
    effective_date = max(str(value) for _, frame in parsed for value in frame["date"].astype(str))
    effective_at = datetime.fromisoformat(f"{effective_date}T00:00:00+00:00")
    if effective_at > observed[-1]:
        raise ProvenanceError("market effective time is after capture completion")
    request_set_digest = _sha256("\n".join(sorted(request_digests)).encode("ascii"))
    adjustment_digest = _sha256(adjustment_evidence.content)
    calendar_digest = _sha256(calendar)
    identity_material = _canonical_json_bytes(
        {
            "members": [(member["path"], member["sha256"]) for member in members],
            "producer": producer,
            "sources": sources,
        }
    )
    manifest: dict[str, Any] = {
        "schema_version": "research_input_manifest.v1",
        "manifest_id": f"cn-industry-etf-{_sha256(identity_material)}",
        "research_input_contract_id": "qsl.cn_industry_etf_rotation.research_input.v1",
        "domain": "cn_equity",
        "profile": "cn_industry_etf_rotation",
        "artifact_type": "cn_industry_etf_historical_market_data",
        "observed_at": observed[0].isoformat().replace("+00:00", "Z"),
        "effective_at": effective_at.isoformat().replace("+00:00", "Z"),
        "as_of": observed[-1].isoformat().replace("+00:00", "Z"),
        "producer": producer,
        "calendar": {
            "calendar_id": "CN_ETF_OBSERVED_SESSIONS",
            "timezone": "Asia/Shanghai",
            "session_date": effective_date,
            "source": "producer-derived-observed-sessions",
            "source_revision": f"sha256:{calendar_digest}",
        },
        "adjustment": {
            "policy": adjustment_evidence.policy,
            "source": adjustment_evidence.source_identity,
            "source_revision": (
                f"{adjustment_evidence.revision};evidence-sha256:{adjustment_digest};"
                f"request-set-sha256:{request_set_digest}"
            ),
        },
        "sources": sorted(sources, key=lambda value: str(value["source_id"])),
        "members": members,
    }
    try:
        manifest_bytes = canonical_research_input_manifest_bytes(manifest)
        manifest_digest = research_input_manifest_sha256(manifest)
    except InvalidResearchInputEvidence:
        raise ProvenanceError("QPK rejected research-input manifest") from None
    files[MANIFEST_FILENAME] = manifest_bytes
    return files, manifest_digest


def _media_type_for(
    path: str,
    parsed: list[tuple[CapturedChunk, pd.DataFrame]],
    license_evidence: OfficialLicenseEvidence,
    adjustment_evidence: OfficialAdjustmentEvidence,
) -> str:
    if path.startswith("raw/"):
        stem = Path(path).stem
        chunk = next(
            chunk
            for chunk, _ in parsed
            if stem == f"{chunk.symbol}-{chunk.start_date}-{chunk.end_date}"
        )
        return chunk.response_media_type
    if path.startswith("requests/"):
        return "application/json"
    if path == "evidence/license.bin":
        return license_evidence.media_type
    if path == "evidence/license_identity.json":
        return "application/json"
    if path == "evidence/adjustment_semantics.bin":
        return adjustment_evidence.media_type
    return "text/csv"


def _repo_root() -> Path:
    return Path(__file__).resolve().parents[2]


def _device_id(path: Path) -> int:
    return path.stat().st_dev


def _validate_root(path: Path, *, label: str) -> Path:
    if not isinstance(path, Path):
        path = Path(path)
    if not path.is_absolute():
        raise ProvenanceError(f"{label} root must be absolute")
    current = Path(path.anchor)
    for component in path.parts[1:]:
        current /= component
        if current.is_symlink():
            raise ProvenanceError(f"{label} root must not have a symlink ancestor")
    if not path.exists() or not path.is_dir():
        raise ProvenanceError(f"{label} root must be a pre-existing directory")
    if path.is_symlink():
        raise ProvenanceError(f"{label} root must not be a symlink")
    resolved = path.resolve()
    repo = _repo_root()
    if resolved == repo or repo in resolved.parents:
        raise ProvenanceError(f"{label} root must be outside the repository")
    metadata = path.stat()
    if metadata.st_uid != os.getuid() or stat.S_IMODE(metadata.st_mode) & 0o077:
        raise ProvenanceError(f"{label} root must be private and owned by the current user")
    return resolved


def _validate_roots(primary_root: Path, backup_root: Path) -> tuple[Path, Path]:
    primary = _validate_root(primary_root, label="primary")
    backup = _validate_root(backup_root, label="backup")
    if primary == backup or primary in backup.parents or backup in primary.parents:
        raise ProvenanceError("primary and backup roots must be distinct paths")
    if _device_id(primary) == _device_id(backup):
        raise ProvenanceError("primary and backup roots must be on different devices")
    return primary, backup


def _ensure_directory(parent: Path, name: str) -> Path:
    child = parent / name
    if child.exists():
        if child.is_symlink() or not child.is_dir():
            raise ProvenanceError("vault package path is unsafe")
    else:
        child.mkdir()
        _fsync_directory(parent)
    return child


def _package_parent(root: Path, digest: str) -> Path:
    current = root
    for name in ("packages", "sha256", digest[:2]):
        current = _ensure_directory(current, name)
    return current


def _write_files(directory: Path, files: dict[str, bytes]) -> None:
    for relative, payload in sorted(files.items()):
        target = directory / relative
        target.parent.mkdir(parents=True, exist_ok=True)
        if any(part.is_symlink() for part in (target, *target.parents) if part != directory.parent):
            raise ProvenanceError("staging path is unsafe")
        with target.open("xb") as handle:
            handle.write(payload)


def _fsync_directory(path: Path) -> None:
    descriptor = os.open(path, os.O_RDONLY)
    try:
        os.fsync(descriptor)
    finally:
        os.close(descriptor)


def _fsync_tree(directory: Path) -> None:
    files = sorted(path for path in directory.rglob("*") if path.is_file())
    directories = sorted(
        (path for path in directory.rglob("*") if path.is_dir()),
        key=lambda path: len(path.parts),
        reverse=True,
    )
    for path in files:
        with path.open("rb") as handle:
            os.fsync(handle.fileno())
    for path in (*directories, directory):
        _fsync_directory(path)


def _readback_package(path: Path, expected_digest: str) -> dict[str, object]:
    if path.is_symlink() or not path.is_dir():
        raise ProvenanceError("package readback path is unsafe")
    manifest_path = path / MANIFEST_FILENAME
    if manifest_path.is_symlink() or not manifest_path.is_file():
        raise ProvenanceError("manifest is missing during package readback")
    manifest_bytes = manifest_path.read_bytes()
    try:
        manifest = read_research_input_manifest_json(manifest_bytes)
        if canonical_research_input_manifest_bytes(manifest) != manifest_bytes:
            raise ProvenanceError("manifest readback is not canonical")
        if research_input_manifest_sha256(manifest) != expected_digest:
            raise ProvenanceError("manifest digest mismatch during readback")
    except InvalidResearchInputEvidence:
        raise ProvenanceError("QPK strict manifest readback failed") from None
    expected_files = {MANIFEST_FILENAME}
    member_digests: dict[str, str] = {}
    raw_digests: set[str] = set()
    for member in manifest["members"]:
        relative = str(member["path"])
        expected_files.add(relative)
        member_path = path / relative
        if member_path.is_symlink() or not member_path.is_file():
            raise ProvenanceError("manifest member is missing or unsafe")
        payload = member_path.read_bytes()
        digest = _sha256(payload)
        if len(payload) != member["size_bytes"] or digest != member["sha256"]:
            raise ProvenanceError("manifest member digest or size mismatch")
        member_digests[relative] = digest
        if relative.startswith("raw/"):
            raw_digests.add(digest)
    actual_files: set[str] = set()
    for candidate in path.rglob("*"):
        if candidate.is_symlink():
            raise ProvenanceError("symlink found during package readback")
        if candidate.is_file():
            actual_files.add(candidate.relative_to(path).as_posix())
    if actual_files != expected_files:
        raise ProvenanceError("package contains missing or undeclared files")
    if {source["content_sha256"] for source in manifest["sources"]} != raw_digests:
        raise ProvenanceError("source digests do not match exact raw members")
    required = {
        "normalized/history.csv",
        "calendar/observed_sessions.csv",
        "evidence/license.bin",
        "evidence/license_identity.json",
        "evidence/adjustment_semantics.bin",
    }
    if not required <= set(member_digests):
        raise ProvenanceError("required provenance member is missing")
    if manifest["calendar"]["source_revision"] != (
        f"sha256:{member_digests['calendar/observed_sessions.csv']}"
    ):
        raise ProvenanceError("calendar provenance digest mismatch")
    try:
        license_identity = json.loads((path / "evidence/license_identity.json").read_bytes())
    except (UnicodeDecodeError, json.JSONDecodeError):
        raise ProvenanceError("license provenance identity is invalid") from None
    if (
        _canonical_json_bytes(license_identity)
        != (path / "evidence/license_identity.json").read_bytes()
        or license_identity.get("content_sha256") != member_digests["evidence/license.bin"]
        or license_identity.get("retention_scope") != "private-retention-permitted"
        or not str(license_identity.get("source_identity", "")).startswith("official:")
    ):
        raise ProvenanceError("license provenance digest mismatch")
    adjustment_revision = str(manifest["adjustment"]["source_revision"])
    if f"evidence-sha256:{member_digests['evidence/adjustment_semantics.bin']}" not in adjustment_revision:
        raise ProvenanceError("adjustment provenance digest mismatch")
    return manifest


def _package_bytes(path: Path) -> dict[str, bytes]:
    if path.is_symlink() or not path.is_dir():
        raise ProvenanceError("package collision path is unsafe")
    result: dict[str, bytes] = {}
    for candidate in path.rglob("*"):
        if candidate.is_symlink():
            raise ProvenanceError("package collision contains symlink")
        if candidate.is_file():
            result[candidate.relative_to(path).as_posix()] = candidate.read_bytes()
    return result


def _publish_to_root(root: Path, files: dict[str, bytes], digest: str) -> Path:
    parent = _package_parent(root, digest)
    final = parent / digest
    staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=parent))
    try:
        _write_files(staging, files)
        _readback_package(staging, digest)
        _fsync_tree(staging)
        if final.exists():
            if _package_bytes(staging) != _package_bytes(final):
                raise ProvenanceError("content-addressed destination collision")
            shutil.rmtree(staging)
        else:
            os.rename(staging, final)
            _fsync_directory(parent)
        _readback_package(final, digest)
        return final
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def publish_research_input(
    capture: CaptureRun,
    *,
    primary_root: Path,
    backup_root: Path,
    license_evidence: OfficialLicenseEvidence,
    adjustment_evidence: OfficialAdjustmentEvidence,
) -> PublishReceipt:
    """Publish one complete capture to primary and independent backup vault roots."""
    _validate_official_evidence(license_evidence, adjustment_evidence)
    primary, backup = _validate_roots(primary_root, backup_root)
    parsed = _validate_capture_accounting(capture)
    producer = _derive_producer_identity()
    files, digest = _build_package(
        capture,
        parsed,
        producer=producer,
        license_evidence=license_evidence,
        adjustment_evidence=adjustment_evidence,
    )
    primary_path = _publish_to_root(primary, files, digest)
    primary_files = _package_bytes(primary_path)
    backup_path = _publish_to_root(backup, primary_files, digest)
    return PublishReceipt(
        manifest_sha256=digest,
        primary_path=primary_path,
        backup_path=backup_path,
    )


def _preflight_publication(
    *,
    primary_root: Path,
    backup_root: Path,
    license_evidence: OfficialLicenseEvidence,
    adjustment_evidence: OfficialAdjustmentEvidence,
) -> None:
    """Stop before provider I/O unless all local publication gates are proven."""
    _validate_official_evidence(license_evidence, adjustment_evidence)
    _validate_roots(primary_root, backup_root)
    _derive_producer_identity()


def _read_evidence(path: Path) -> bytes:
    if not path.is_absolute() or path.is_symlink() or not path.is_file():
        raise ProvenanceError("official evidence path must be an absolute non-symlink file")
    return path.read_bytes()


def main(argv: list[str] | None = None) -> int:
    parser = argparse.ArgumentParser(
        description="Capture and publish a complete Tencent CN ETF research-input package."
    )
    parser.add_argument("--vault-root", type=Path, required=True)
    parser.add_argument("--backup-vault-root", type=Path, required=True)
    parser.add_argument("--symbols", required=True)
    parser.add_argument("--start-date", required=True)
    parser.add_argument("--end-date", required=True)
    parser.add_argument("--license-evidence", type=Path, required=True)
    parser.add_argument("--license-media-type", required=True)
    parser.add_argument("--license-source", required=True)
    parser.add_argument("--license-revision", required=True)
    parser.add_argument(
        "--license-scope",
        choices=("private-retention-permitted",),
        required=True,
    )
    parser.add_argument("--adjustment-evidence", type=Path, required=True)
    parser.add_argument("--adjustment-media-type", required=True)
    parser.add_argument("--adjustment-source", required=True)
    parser.add_argument("--adjustment-revision", required=True)
    parser.add_argument(
        "--adjustment-policy",
        choices=("split_adjusted", "total_return_adjusted"),
        required=True,
    )
    args = parser.parse_args(argv)
    license_evidence = OfficialLicenseEvidence(
        content=_read_evidence(args.license_evidence),
        media_type=args.license_media_type,
        source_identity=args.license_source,
        revision=args.license_revision,
        retention_scope=args.license_scope,
    )
    adjustment_evidence = OfficialAdjustmentEvidence(
        content=_read_evidence(args.adjustment_evidence),
        media_type=args.adjustment_media_type,
        source_identity=args.adjustment_source,
        revision=args.adjustment_revision,
        policy=args.adjustment_policy,
    )
    _preflight_publication(
        primary_root=args.vault_root,
        backup_root=args.backup_vault_root,
        license_evidence=license_evidence,
        adjustment_evidence=adjustment_evidence,
    )
    capture = capture_tencent_history_run(
        tuple(symbol.strip() for symbol in args.symbols.split(",") if symbol.strip()),
        start_date=args.start_date,
        end_date=args.end_date,
    )
    receipt = publish_research_input(
        capture,
        primary_root=args.vault_root,
        backup_root=args.backup_vault_root,
        license_evidence=license_evidence,
        adjustment_evidence=adjustment_evidence,
    )
    print(
        json.dumps(
            {
                "manifest_sha256": receipt.manifest_sha256,
                "primary_path": str(receipt.primary_path),
                "backup_path": str(receipt.backup_path),
            },
            sort_keys=True,
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
