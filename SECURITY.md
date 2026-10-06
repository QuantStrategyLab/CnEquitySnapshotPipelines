# Security Policy

Thanks for helping keep `CnEquitySnapshotPipelines` safe.

This repository builds A-share factor-snapshot artifacts, manifests, and release evidence; it does not place broker orders or store broker credentials. Please do **not** open a public issue for vulnerabilities involving CI/CD secrets, data-source credentials, or issues that could let an attacker tamper with published artifacts or manifests consumed by downstream strategy repositories.

## Reporting a Vulnerability

- Contact the maintainer directly at GitHub: `@Pigbibi`.
- If private vulnerability reporting is enabled for this repository, prefer that channel.
- Include the repository name, affected commit or branch, environment details, and exact reproduction steps.

## Secret and Credential Exposure

If you suspect tokens, API keys, or other CI/CD secrets were exposed:

1. Rotate the exposed secrets immediately.
2. Pause the affected scheduled workflow if the exposure could affect automated artifact publishing.
3. Share only the minimum evidence needed to reproduce the issue.

## Artifact Integrity

If you find a way to make this pipeline produce a snapshot, manifest, or ranking preview that misrepresents its inputs (for example, bypassing point-in-time safety or validation checks described in [`docs/artifact_contract.md`](docs/artifact_contract.md)), please report it the same way as a security issue, since downstream repositories consume these artifacts without re-deriving them.

## Scope Notes

Security fixes should stay minimal and focused. Please avoid bundling unrelated refactors with a security report or patch.
