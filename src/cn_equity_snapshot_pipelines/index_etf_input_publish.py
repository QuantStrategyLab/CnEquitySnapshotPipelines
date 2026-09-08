"""Publish local CN execution exports without claiming provider authentication.

This consumes already exported JSON; it never fetches data, fills missing bars,
or grants promotion authority. Historical source approval belongs to the job's
preselected input root. The default evidence kind is synthetic.
"""

from __future__ import annotations

import argparse
from datetime import datetime, timezone
from hashlib import sha256
import json
import os
from pathlib import Path
import re
import shutil
import tempfile

from quant_platform_kit.data.research_input import canonical_research_input_manifest_bytes

from . import research_input_publish as storage
from .research_input_publish import OfficialLicenseEvidence, ProvenanceError, PublishReceipt


def _canonical(value) -> bytes:
    return json.dumps(value, sort_keys=True, separators=(",", ":"), ensure_ascii=False, allow_nan=False).encode()


def _json(content: bytes):
    def pairs(items):
        result = {}
        for key, value in items:
            if key in result:
                raise ProvenanceError("cn_execution_json_invalid")
            result[key] = value
        return result

    return json.loads(
        content,
        object_pairs_hook=pairs,
        parse_constant=lambda _: (_ for _ in ()).throw(ProvenanceError("cn_execution_json_invalid")),
    )


def _read(path: Path) -> bytes:
    if (
        not isinstance(path, Path)
        or not path.is_absolute()
        or not path.is_file()
        or any(part.is_symlink() for part in (path, *path.parents))
        or path.stat().st_size > 64 * 1024 * 1024
    ):
        raise ProvenanceError("cn_execution_source_path_invalid")
    return path.read_bytes()


def _validate(files: dict[str, bytes], digest: str):
    # Use the actual strategy consumer so publication cannot drift into a
    # second, weaker definition of a complete execution input.
    from cn_equity_strategies.backtest.index_etf_strict_runner import load_index_etf_input

    manifest = files[storage.MANIFEST_FILENAME]
    members = {name: content for name, content in files.items() if name != storage.MANIFEST_FILENAME}
    return load_index_etf_input(manifest, members, expected_manifest_sha256=digest)


def _publish(root: Path, files: dict[str, bytes], digest: str) -> Path:
    parent = storage._package_parent(root, digest)
    final = parent / digest
    staging = Path(tempfile.mkdtemp(prefix=".staging-", dir=parent))
    try:
        storage._write_files(staging, files)
        _validate(storage._package_bytes(staging), digest)
        storage._fsync_tree(staging)
        if final.exists():
            if storage._package_bytes(final) != files:
                raise ProvenanceError("cn_execution_destination_collision")
        else:
            os.rename(staging, final)
            storage._fsync_directory(parent)
        _validate(storage._package_bytes(final), digest)
        return final
    finally:
        if staging.exists():
            shutil.rmtree(staging)


def publish_index_etf_input(
    *,
    daily_path: Path,
    calendar_path: Path,
    actions_path: Path,
    primary_root: Path,
    backup_root: Path,
    source_id: str,
    source_revision: str,
    source_observed_at: str,
    license_evidence: OfficialLicenseEvidence,
    evidence_kind: str = "synthetic",
) -> PublishReceipt:
    """Append an exact local export to the existing private two-vault layout.

    The source observation time must come from the export's source record;
    importing it does not turn that declared timestamp into independently
    verified historical availability. Re-imports preserve the same identity.
    """
    try:
        if evidence_kind not in {"synthetic", "historical"} or not all(
            isinstance(value, str) and re.fullmatch(r"[A-Za-z0-9._:/-]{1,180}", value)
            for value in (source_id, source_revision)
        ):
            raise ProvenanceError("cn_execution_source_identity_invalid")
        observed = storage._parse_timestamp(source_observed_at)
        if observed > datetime.now(timezone.utc):
            raise ProvenanceError("cn_execution_source_observation_invalid")
        if (
            not isinstance(license_evidence, OfficialLicenseEvidence)
            or not isinstance(license_evidence.content, bytes)
            or len(license_evidence.content.strip()) < 20
            or license_evidence.content.strip().lower() in storage._BARE_ACKS
            or not license_evidence.media_type.strip()
            or not license_evidence.source_identity.startswith("official:")
            or not license_evidence.revision.strip()
            or license_evidence.retention_scope != "private-retention-permitted"
        ):
            raise ProvenanceError("cn_execution_license_evidence_invalid")
        primary, backup = storage._validate_roots(primary_root, backup_root)
        raw = {"daily": _read(daily_path), "calendar": _read(calendar_path), "actions": _read(actions_path)}
        daily, calendar, actions = (_json(raw[name]) for name in ("daily", "calendar", "actions"))
        files = {f"raw/{name}.json": content for name, content in raw.items()}
        files.update(
            {
                "normalized/daily.json": _canonical(daily),
                "calendar/sessions.json": _canonical(calendar),
                "corporate_actions/events.json": _canonical(actions),
                "evidence/license.bin": license_evidence.content,
                "evidence/license_identity.json": _canonical(
                    {
                        "content_sha256": sha256(license_evidence.content).hexdigest(),
                        "media_type": license_evidence.media_type,
                        "source_identity": license_evidence.source_identity,
                        "revision": license_evidence.revision,
                        "retention_scope": license_evidence.retention_scope,
                    }
                ),
            }
        )
        producer = dict(storage._derive_producer_identity())
        producer["tool"] = "cn_equity_snapshot_pipelines.index_etf_input_publish"
        members = [
            storage._member(
                name, "application/json" if name.endswith(".json") else license_evidence.media_type, content
            )
            for name, content in sorted(files.items())
        ]
        sources = [
            {
                "source_id": f"local-import:{source_id}:{name}",
                "revision": source_revision,
                "observed_at": source_observed_at,
                "content_sha256": sha256(content).hexdigest(),
            }
            for name, content in sorted(raw.items())
        ]
        identity = sha256(
            _canonical({"members": members, "sources": sources, "producer": producer, "evidence_kind": evidence_kind})
        ).hexdigest()
        manifest = {
            "schema_version": "research_input_manifest.v1",
            "manifest_id": "cn-index-etf-" + identity,
            "research_input_contract_id": "qsl.cn_index_etf.execution_input.v1",
            "domain": "cn_equity",
            "profile": "cn_index_etf_tactical_rotation",
            "artifact_type": "cn_index_etf_synthetic_execution_history"
            if evidence_kind == "synthetic"
            else "cn_index_etf_execution_history",
            "observed_at": source_observed_at,
            "as_of": source_observed_at,
            "effective_at": calendar["end_date"] + "T15:00:00+08:00",
            "producer": producer,
            "calendar": {
                "calendar_id": "CN_INDEX_ETF_EXPORTED_SESSIONS",
                "timezone": "Asia/Shanghai",
                "session_date": calendar["end_date"],
                "source": f"local-import:{source_id}:calendar",
                "source_revision": "sha256:" + sha256(files["calendar/sessions.json"]).hexdigest(),
            },
            "adjustment": {
                "policy": "raw",
                "source": f"local-import:{source_id}:daily",
                "source_revision": source_revision,
            },
            "sources": sources,
            "members": members,
        }
        files[storage.MANIFEST_FILENAME] = canonical_research_input_manifest_bytes(manifest)
        digest = sha256(files[storage.MANIFEST_FILENAME]).hexdigest()
        _validate(files, digest)
        primary_path = _publish(primary, files, digest)
        backup_path = _publish(backup, storage._package_bytes(primary_path), digest)
        return PublishReceipt(digest, primary_path, backup_path)
    except ProvenanceError:
        raise
    except (OSError, ValueError, TypeError, KeyError, AttributeError, ImportError):
        raise ProvenanceError("cn_execution_publication_failed") from None


def main(argv=None) -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    for name in ("daily", "calendar", "actions", "primary-root", "backup-root", "license-evidence"):
        parser.add_argument("--" + name, type=Path, required=True)
    for name in ("source-id", "source-revision", "source-observed-at", "license-source", "license-revision"):
        parser.add_argument("--" + name, required=True)
    parser.add_argument("--license-media-type", default="text/plain")
    parser.add_argument("--license-scope", choices=["private-retention-permitted"], required=True)
    parser.add_argument("--evidence-kind", choices=["synthetic", "historical"], default="synthetic")
    args = parser.parse_args(argv)
    try:
        receipt = publish_index_etf_input(
            daily_path=args.daily,
            calendar_path=args.calendar,
            actions_path=args.actions,
            primary_root=args.primary_root,
            backup_root=args.backup_root,
            source_id=args.source_id,
            source_revision=args.source_revision,
            source_observed_at=args.source_observed_at,
            evidence_kind=args.evidence_kind,
            license_evidence=OfficialLicenseEvidence(
                _read(args.license_evidence),
                args.license_media_type,
                args.license_source,
                args.license_revision,
                args.license_scope,
            ),
        )
    except (ProvenanceError, OSError):
        print(json.dumps({"status": "unavailable", "reason": "cn_execution_publication_failed"}))
        return 2
    print(
        json.dumps(
            {
                "status": "published",
                "manifest_sha256": receipt.manifest_sha256,
                "source_kind": "local-import",
                "promotion_authority_granted": False,
            }
        )
    )
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
