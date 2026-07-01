# Contributing

Thanks for contributing to `CnEquitySnapshotPipelines`.

## Ground Rules

- Prefer small pull requests with one clear purpose.
- Keep artifact-contract changes separate from workflow or documentation-only changes.
- Preserve this repository boundary as a snapshot/artifact pipeline; do not move broker execution, live-allocation decisions, or private credentials into it.
- Add or update tests, docs, or reproducible evidence commands when changing builders, manifests, or contracts.

## Local Verification

```bash
python -m pip install -e '.[test]'
python -m pip check
ruff check .
python -m pytest -q
python -m build
```

## Branching and Pull Requests

- Create a topic branch for each change.
- Open a pull request with a concise summary, scope boundary, and concrete validation notes.
