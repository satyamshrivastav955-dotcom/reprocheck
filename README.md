# ReproCheck

**Your repository works here. Prove it works everywhere.**

ReproCheck is a local-first CLI for auditing reproducibility risks and collecting
bounded evidence from a clean verification run. It is deliberately explicit:
`scan` is read-only and never runs repository code; `verify` displays proposed
commands and requires `--approve` before execution.

## Status and scope

This MVP supports static discovery and checks for Python and Node.js/TypeScript
repositories. It recognizes other ecosystems as unsupported rather than
guessing their behavior. The clean-room profiles are:

- `python-uv`: `uv sync --locked`, followed by the discovered Python test path.
- `python-pip-editable`: an explicit, opt-in host-oriented fallback.
- `node-npm`: `npm ci`, followed by declared package test/build scripts.

Docker is the default verification backend. If Docker is unavailable, verification
is recorded as blocked; ReproCheck does not silently fall back to host execution.
Host execution requires both `--backend host` and `--allow-host-exec`.

## Requirements

- Python 3.12 (pinned in `.python-version`).
- [uv](https://docs.astral.sh/uv/) 0.13 or a compatible current uv release.
- Docker Engine is optional for static scans and required for the default isolated
  verification backend. Docker is not a complete security sandbox.

The repository currently has no runtime dependency beyond Python's standard
library. Development tools are locked in `uv.lock`.

## Install and run

From a checkout:

```bash
uv sync --locked --dev
uv run reprocheck scan .
```

The intended registry installation shape is:

```bash
uvx reprocheck scan <repository-path>
pipx install reprocheck
```

Those commands require a published package; this checkout does not claim that a
registry publication has occurred. For a local package build:

```bash
uv build
```

## Scan reports

```bash
reprocheck scan .
reprocheck scan . --format json --output report.json --fail-on high
reprocheck scan . --format markdown --output report.md
```

Exit codes are stable:

| Code | Meaning |
|---:|---|
| 0 | No finding at or above `--fail-on` |
| 1 | A policy finding meets `--fail-on` |
| 2 | Invalid usage, path, or input |
| 3 | Unexpected internal error |
| 130 | Interrupted by the user |

JSON is the CI interface. Findings are ordered by stable ID, path, and
fingerprint. A score is a coverage-aware static readiness summary, not proof of
successful execution. Presence of a manifest, lockfile, or Dockerfile is never
reported as a verified pass.

## Clean-room verification

First inspect the proposed plan without granting execution:

```bash
reprocheck verify . --profile python-uv --backend docker --format terminal
```

Then explicitly approve it:

```bash
reprocheck verify . \
  --profile python-uv \
  --backend docker \
  --approve \
  --receipt verification-receipt.json
```

The command plan is printed before execution. Dependency acquisition is marked
as network-enabled; verification commands are requested with networking disabled
by the Docker backend. Every command is passed as an argument array, not a shell
string. Logs are bounded and redacted before they are stored.

If Docker is unavailable, an explicit local-only demonstration can be run with:

```bash
reprocheck verify . \
  --profile python-uv \
  --backend host \
  --allow-host-exec \
  --approve \
  --receipt host-receipt.json
```

This is not a sandbox. The receipt records that residual risk.

Receipt operations are local and do not upload repository contents:

```bash
reprocheck receipt validate verification-receipt.json --repository .
reprocheck receipt compare before.json after.json
```

Receipts use canonical JSON and SHA-256 to detect changes relative to a trusted
copy. A hash stored alongside a receipt is not a tamper-proof attestation; see
[`docs/receipt-format.md`](docs/receipt-format.md).

## Development checks

The canonical offline check command is:

```bash
make test
```

It runs Ruff, mypy, formatting validation, and the complete pytest suite from the
locked uv environment. Other useful commands are:

```bash
uv run --locked python -m pytest
uv run --locked python -m pytest tests/unit/test_verification.py
uv build
```

The static scan itself is read-only. Tests use inert temporary fixtures and
mocked or explicitly approved disposable subprocesses; they do not run
destructive payloads on the developer's host.

## Architecture

```text
src/reprocheck/
├── cli.py                 # scan, verify, receipt commands and exit policy
├── discovery.py           # safe filesystem discovery and ecosystem markers
├── fingerprint.py         # deterministic filesystem-v1 content hash
├── command_parser.py      # conservative non-executing command classification
├── models.py              # typed, versioned domain contracts
├── checks/                # deterministic static check registry and checks
├── scoring.py             # applicability-aware static and verified summaries
├── reporting/             # terminal, JSON, and Markdown renderers
├── verification/          # approval, workspaces, limits, and subprocesses
└── receipts/              # canonical receipt build, validation, comparison
```

Checks are registered through a stable plugin interface and return findings with
IDs such as `RC-DEP-002`. The scanner never invokes a package script, CI step,
Dockerfile, README command, or dependency installer. Verification profiles are
separate from static checks and emit `VERIFIED_PASS`, `VERIFIED_FAIL`, or
`NOT_VERIFIED` outcomes.

## Security model and limitations

ReproCheck uses argument-array subprocesses, `shell=False`, explicit approval,
temporary workspaces, path containment checks, symlink rejection, bounded logs,
secret redaction, an environment allowlist, timeouts, process-group cleanup, and
best-effort Unix resource limits. Docker execution drops no claim that the
container is a complete sandbox: the Docker daemon and host kernel remain in the
trust boundary, and dependency installation can execute package hooks.

The static secret and path checks are conservative heuristics, not proof of
security. README/YAML/shell parsing is intentionally incomplete. ReproCheck
cannot prove that a passing test suite is correct, that all platforms will
behave identically, or that a receipt has an external provenance attestation.
There is no automatic upload, automatic file modification, AI API, hosted model,
or required cloud account.

See:

- [`docs/checks.md`](docs/checks.md) — check scope and limitations.
- [`docs/receipt-format.md`](docs/receipt-format.md) — receipt fields and integrity.
- [`docs/security-model.md`](docs/security-model.md) — execution risks and controls.
- [`docs/supported-platforms.md`](docs/supported-platforms.md) — support boundaries.
- [`docs/benchmark-methodology.md`](docs/benchmark-methodology.md) — honest future evaluation.

## Dogfooding and contribution

The CI workflow runs the locked test suite and a ReproCheck self-scan. Genuine
self-scan findings are fixed or documented; they are not suppressed merely to
make the repository appear clean. Contributions should add a fixture and a
regression test for every new check or security behavior.

## License

ReproCheck is distributed under the license in [`LICENSE`](LICENSE).
