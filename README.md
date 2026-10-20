# ReproCheck

**Your repository works here. Measure whether it can be reproduced.**

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

Build and run the runtime image when Docker is available:

```bash
docker build --tag reprocheck:local .
docker run --rm --read-only reprocheck:local --version
```

The release candidate has not claimed a successful image build from environments
without Docker. CI includes a genuine `docker build` step on its Docker-capable
runner.

## Requirements

- Python 3.12 (pinned in `.python-version`).
- [uv](https://docs.astral.sh/uv/) 0.13 or a compatible current uv release.
- Linux, Windows (10/11), and macOS environments supported.
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

With no subcommand, ReproCheck opens with a compact terminal welcome showing the
real first steps:

```bash
reprocheck
```

The welcome is plain text when stdout is redirected or `NO_COLOR` is set. It is
never added to JSON, Markdown, receipt output, or detailed `--help` output, so
those interfaces remain safe for CI and scripts.

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

## Release and deployment

Registry publication and Docker image publication are separate release steps and
were not performed by this checkout. After reviewing the generated artifacts and
authenticating the intended registries, the release commands are:

```bash
# Build the Python artifacts locally.
uv build

# Publish the wheel and source distribution to the configured Python registry.
python -m twine upload dist/reprocheck-*.whl dist/reprocheck-*.tar.gz

# Build and publish a container image to the configured OCI registry.
docker build --tag <registry>/<namespace>/reprocheck:<version> .
docker push <registry>/<namespace>/reprocheck:<version>
```

Successful `uv build` or `docker build` commands do not prove registry access or
publication. No credentials are stored in this repository, and no deployment is
claimed until the registry command returns success.

## Scan reports

```bash
reprocheck scan .
reprocheck scan . --format json --output report.json --fail-on high
reprocheck scan . --format markdown --output report.md
```

Exit codes are stable:

| Code | Meaning |
|---:|---|
| 0 | No finding at or above `--fail-on`; an approved experiment is reproducible |
| 1 | A policy finding meets `--fail-on`, verification fails, or an experiment is drifted, mismatched, incomplete, or infrastructure-blocked |
| 2 | Invalid usage, path, or input |
| 3 | Unexpected internal error |
| 130 | Interrupted by the user |

JSON is the CI interface. Findings are ordered by stable ID, path, and
fingerprint. A score is a coverage-aware static readiness summary, not proof of
successful execution. Presence of a manifest, lockfile, or Dockerfile is never
reported as a verified pass.

The `verified_score` is intentionally separate: it scores only findings with
`VERIFIED_PASS` or `VERIFIED_FAIL` evidence. A successful verification run does
not automatically verify every static scoring group, so a report can honestly
show `verified_score: 0/<applicable weight>` when the executed commands have not
been mapped to individual static findings.

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
string. The backend also requests dropped capabilities, `no-new-privileges`,
CPU/memory/process limits, and a bounded temporary filesystem. The image's
default user is retained rather than claiming a universal non-root guarantee;
the daemon and image configuration remain part of the trust boundary. Logs are
bounded and redacted before they are stored.

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

## Independent reproducibility experiment

The Python-first experiment captures one source snapshot, creates two independent
workspaces, and runs the same approved `python-uv` plan twice. Select at least one
workspace-relative output artifact to make the comparison meaningful:

```bash
reprocheck experiment examples/reproducibility-drift-python \
  --profile python-uv \
  --backend docker \
  --artifact reprocheck-demo-artifact.txt \
  --receipt experiment-receipt.json \
  --approve
```

The plan is printed before either run. The experiment never shares writable
workspaces or dependency caches between runs. Acquisition is network-enabled;
later verification commands use Docker networking disabled. The receipt records
the common source fingerprint, plan, both run outcomes, selected artifact hashes,
classification, environment details, and limitations.

Experiment classifications are deliberately narrow:

| Classification | Meaning |
|---|---|
| `reproducible` | Both runs pass and every selected artifact matches |
| `drift_detected` | Both runs pass but a selected artifact differs |
| `pass_fail_disagreement` | One run passes and the other has a test/build failure |
| `both_failed` | Both runs reach verification and fail |
| `infrastructure_failure` | Startup, acquisition, backend, timeout, or executor failure prevents comparison |
| `comparison_invalid` | The source changes or the frozen comparison contract is inconsistent |
| `incomplete` | Approval, run, artifact, or other required evidence is missing |

An experiment report keeps per-run execution separate from the pair-level
reproducibility result. For example, `drift_detected` may show `Run A: pass` and
`Run B: pass` with `artifacts_match: false`; both verification runs passed, but
reproducibility was not established. Experiment exit code `1` reports that
pair-level result and does not mean both runs failed.

The controlled fixture in
[`examples/reproducibility-drift-python`](examples/reproducibility-drift-python)
intentionally writes a fresh artifact value on each passing run. It demonstrates
an observed artifact difference only; it is not a universal reproducibility claim.

Receipt operations are local and do not upload repository contents:

```bash
reprocheck receipt validate verification-receipt.json --repository .
reprocheck receipt compare before.json after.json
```

Receipts use canonical JSON and SHA-256 to detect changes relative to a trusted
copy. A hash stored alongside a receipt is not a tamper-proof attestation; see
[`docs/receipt-format.md`](docs/receipt-format.md).

The documented drift acceptance flow is:

```powershell
uv run --locked --offline reprocheck experiment examples/reproducibility-drift-python `
  --profile python-uv --backend docker `
  --artifact reprocheck-demo-artifact.txt `
  --receipt "$env:TEMP\reprocheck-experiment-receipt-final.json" `
  --format terminal --approve

reprocheck receipt validate "$env:TEMP\reprocheck-experiment-receipt-final.json" `
  --repository examples/reproducibility-drift-python
```

The fixture is expected to return exit code `1` because it demonstrates artifact
drift. The two verification runs should each pass. Copy the receipt, change an
integrity-relevant field, and run `receipt validate` again; the tampered copy must
be rejected.

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
├── verification/          # approval, snapshots, experiments, artifacts, and subprocesses
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
