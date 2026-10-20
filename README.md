# ReproCheck

<div align="center">

<pre>
R E P R O C H E C K
</pre>

<strong>Run the same verification workflow twice. Compare the evidence. Detect artifact drift.</strong>

<p>
  <a href="https://github.com/satyamshrivastav955-dotcom/reprocheck/actions/workflows/ci.yml"><img src="https://github.com/satyamshrivastav955-dotcom/reprocheck/actions/workflows/ci.yml/badge.svg" alt="CI status"></a>
  <a href="https://www.python.org/downloads/release/python-3120/"><img src="https://img.shields.io/badge/python-3.12-3776AB?logo=python&logoColor=white" alt="Python 3.12"></a>
  <a href="LICENSE"><img src="https://img.shields.io/badge/license-MIT-750014" alt="MIT license"></a>
</p>

<p>
  <a href="#quick-start">Quick start</a> ·
  <a href="docs/reproducibility-drift-demo.md">Guided demo</a> ·
  <a href="docs/supported-platforms.md">Supported profiles</a> ·
  <a href="docs/security-model.md">Security model</a>
</p>

</div>

ReproCheck audits a repository, proposes an explicit verification plan, and can
run that plan in a clean backend with approval. Its two-run experiment uses one
source snapshot, two independent workspaces, and selected artifact hashes to
answer a narrower question than “did the tests pass?”

> Passing tests in two executions do not prove that the generated artifacts are
> identical. ReproCheck keeps those facts separate and reports artifact drift
> when the selected evidence differs.

The result applies only to the selected source snapshot, verification profile,
execution backend, environment, and artifacts. ReproCheck does not claim that a
repository is reproducible everywhere.

## What it does

- **Scan** — inspect repository structure and reproducibility signals without
  executing repository commands.
- **Verify** — show an argv-level plan, wait for explicit approval, then collect
  bounded evidence from a clean verification workspace.
- **Experiment** — run the approved plan twice in independent workspaces and
  compare selected files or directories.
- **Receipt** — write a portable JSON receipt with source fingerprints, run
  outcomes, artifact manifests, limitations, and canonical SHA-256 integrity.

## Why two passing runs can still drift

A test suite can pass while a build writes a timestamp, a random identifier, a
platform-specific file, or another changing output. If both verification commands
return success but a selected artifact differs, the experiment is:

```text
Run A: pass / verified_pass
Run B: pass / verified_pass
Artifacts: different
Experiment: drift_detected
Reproducibility: not established
```

That is not a contradiction. The run-level result says the commands passed; the
experiment-level result says the selected outputs were not equal.

## How it works

1. Inspect the repository with a read-only static scan.
2. Construct an explicit, allow-listed verification plan.
3. Show the plan and require `--approve` before executing commands.
4. Materialize an independent clean workspace for each run.
5. Collect command outcomes, environment metadata, and selected artifact hashes.
6. Compare the evidence and classify the pair of runs.
7. Write a receipt that another process can validate locally.

## Quick start

### Prerequisites

- Python **3.12**. The supported range is declared in `pyproject.toml` and
  `.python-version`.
- [uv](https://docs.astral.sh/uv/) for the locked development environment. CI
  currently uses uv `0.13.0`; a compatible current release is supported.
- Docker Engine or Docker Desktop for the documented isolated verification and
  experiment workflows. Static scans do not require Docker.
- GNU Make only if you want to use the full `make test` quality-gate wrapper.

No environment variables are required. `NO_COLOR` and `TERM` only control
optional terminal presentation; no cloud credentials are needed for the core CLI.

Install uv using the [official installation instructions](https://docs.astral.sh/uv/getting-started/installation/),
then verify the tools before continuing:

```bash
python3 --version
uv --version
docker --version  # required for verify/experiment with --backend docker
```

### Install this checkout for development

From the ReproCheck checkout:

```bash
uv python install 3.12
uv sync --locked --dev
uv run --locked reprocheck --help
```

This keeps dependencies in the project environment. It does not install a
published `reprocheck` command globally.

To see the adaptive first-run guide after installation:

```bash
uv run --locked reprocheck
```

Use `--help` for the script-friendly command list; the welcome is intentionally
kept out of help, version, JSON, Markdown, and receipt output.

### Build and smoke-test the runtime image

The repository also provides a small runtime image with `reprocheck` as its
entry point:

```bash
docker build --tag reprocheck:local .
docker run --rm --read-only reprocheck:local --version
```

PowerShell uses the same commands:

```powershell
docker build --tag reprocheck:local .
docker run --rm --read-only reprocheck:local --version
```

The Docker image is useful for the CLI runtime smoke test. The clean-room
verification backend uses its own profile images and remains subject to Docker
daemon and host-kernel boundaries.

### Run it against another repository

From the ReproCheck checkout, scan the current checkout:

```bash
uv run --locked reprocheck scan .
```

To invoke the source checkout from elsewhere, use uv's explicit project path:

```bash
# POSIX shell
uv run --project /path/to/reprocheck --locked reprocheck scan /path/to/target
```

```powershell
# PowerShell
uv run --project C:\src\reprocheck --locked reprocheck scan C:\src\target
```

The target path is the repository being inspected; the `--project` path is the
ReproCheck checkout that supplies the CLI.

### First scan and plan preview

```bash
uv run --locked reprocheck scan /path/to/target --format terminal
uv run --locked reprocheck verify /path/to/target \
  --profile python-uv \
  --backend docker \
  --format terminal
```

`scan` is read-only. The `verify` preview prints the acquisition and verification
commands but executes nothing without `--approve`; this intentional preview
returns exit code `1` because no verification has been completed yet.

### Approve one verification

After reviewing the displayed plan:

```bash
uv run --locked reprocheck verify /path/to/target \
  --profile python-uv \
  --backend docker \
  --approve \
  --receipt verification-receipt.json
```

The default Docker backend allows dependency acquisition to use the network, then
requests networking to be disabled for verification commands. If Docker is not
available, the result is blocked rather than silently falling back to host
execution. Host execution requires both `--backend host` and
`--allow-host-exec`, and is not a sandbox.

### Run the reproducibility experiment

The included Python fixture writes a fresh value to
`reprocheck-demo-artifact.txt` during its test. Run the plan twice and compare
that workspace-relative path:

```bash
uv run --locked reprocheck experiment \
  examples/reproducibility-drift-python \
  --profile python-uv \
  --backend docker \
  --artifact reprocheck-demo-artifact.txt \
  --receipt experiment-receipt.json \
  --format terminal \
  --approve
```

The command is expected to return exit code `1`: both runs should pass, but the
selected artifact should differ. The artifact path is resolved relative to each
materialized workspace root, not relative to this README or the host checkout.
See the [guided drift demo](docs/reproducibility-drift-demo.md) for POSIX and
PowerShell commands, expected output, and receipt validation.

Validate the receipt locally:

```bash
uv run --locked --offline reprocheck receipt validate \
  experiment-receipt.json \
  --repository examples/reproducibility-drift-python
```

For the same workflow in PowerShell, use the documented backtick continuation:

```powershell
uv run --locked reprocheck experiment `
  examples/reproducibility-drift-python `
  --profile python-uv --backend docker `
  --artifact reprocheck-demo-artifact.txt `
  --receipt experiment-receipt.json `
  --format terminal --approve

uv run --locked --offline reprocheck receipt validate `
  experiment-receipt.json `
  --repository examples/reproducibility-drift-python
```

## Interpreting the result

ReproCheck reports several deliberately different layers of evidence:

| Layer | Question answered | Example values |
|---|---|---|
| Static finding | What does the repository expose or omit? | `looks_correct`, `static_fail`, `unsupported` |
| Verification run | Did one approved command sequence execute successfully? | `pass`, `fail`, `blocked`, `denied`, `timeout` |
| Experiment classification | What does the pair of runs establish? | `reproducible`, `drift_detected`, `both_failed`, `incomplete` |
| Artifact comparison | Did every selected file or directory match? | `artifact_equal: true/false` |
| Receipt integrity | Does the receipt still match its canonical hash? | `valid` or `invalid` |
| CLI exit status | Should a script treat the result as acceptable? | `0` for an established pass; `1` for policy findings or negative/incomplete verification conclusions |

The experiment classifications are intentionally narrow:

- `reproducible` — both runs passed and every selected artifact matched.
- `drift_detected` — both runs passed, but a selected artifact differed.
- `pass_fail_disagreement` — one run passed and the other failed.
- `both_failed` — both runs reached verification and failed.
- `infrastructure_failure` — startup, acquisition, backend, or timeout evidence
  prevented a meaningful comparison.
- `comparison_invalid` — the source or frozen comparison contract was
  inconsistent.
- `incomplete` — approval, runs, artifacts, or other required evidence was
  missing.

An exit code of `1` for `drift_detected` does not mean both test runs failed. It
means the reproducibility conclusion is negative. The terminal, JSON, Markdown,
and receipt outputs retain the individual run outcomes.

## Reports and scripting

JSON is the stable machine-readable interface. Markdown is useful for a review
artifact, while terminal output is optimized for a human at a shell:

```bash
uv run --locked reprocheck scan /path/to/target \
  --format json \
  --output report.json \
  --fail-on high

uv run --locked reprocheck scan /path/to/target \
  --format markdown \
  --output report.md
```

Exit codes are stable: `0` means no selected policy finding and, for an
approved experiment, an established reproducible result; `1` means a policy
finding, verification failure, drift, disagreement, incompleteness, or blocked
infrastructure; `2` means invalid usage or input; `3` means an unexpected
internal error; and `130` means interruption.

### Static score versus verified score

`score` summarizes bounded static readiness checks, using the least optimistic
applicable finding in each weighted group. `verified_score` is separate execution
coverage, not a reproducibility percentage. Recognized profile test/build
outcomes are linked to `RC-CMD-001` and can contribute only the **commands** group
(15 points). Acquisition alone earns no verified credit. Failed, denied, missing,
or unlinked verification evidence earns no positive credit.

A passing suite does not verify CI, lockfiles, portability, environment
documentation, or repository hygiene, and does not erase static warnings. The
verified denominator retains all statically applicable groups; unverified groups
remain zero and are identified in the limitations. Unsupported/not-applicable
findings are excluded before weighting. Terminal, JSON, and Markdown show both
scores and their group breakdowns. Artifact equality and experiment classification
are still independent: two passing suites can earn command-execution credit
while the overall experiment reports `drift_detected`.

## Receipts and integrity

Receipts are local JSON evidence files. They contain the source fingerprint,
selected profile and backend, command outcomes, environment metadata, artifact
manifests, limitations, and a canonical SHA-256 value. Validation does not run
repository code:

```bash
uv run --locked --offline reprocheck receipt validate verification-receipt.json
uv run --locked --offline reprocheck receipt compare before.json after.json
```

`valid` means the receipt can be parsed and its canonical content still matches
the integrity metadata. It is not a signature or external provenance attestation;
an actor who can rewrite both the receipt and its hash can create a new valid
receipt. See [Receipt format](docs/receipt-format.md) for the versioned contract.

## Supported profiles and backends

The CLI exposes these verification profiles:

| Profile | Requirements | Commands planned |
|---|---|---|
| `python-uv` | `pyproject.toml` and `uv.lock` | `uv sync --locked`, then `uv run python -m pytest` when tests are present |
| `python-pip-editable` | Python project metadata and discoverable test environment; explicit opt-in | One editable install with recognized test/dev requirements and extras, then the recognized pytest or unittest command |
| `node-npm` | `package.json` and `package-lock.json` | `npm ci`, then declared `npm test`/`npm run build` scripts |

Aliases accepted by the CLI are `python`, `python-pip`, and `node`. ReproCheck
does not silently turn pnpm, Yarn, an unfamiliar lockfile, or another ecosystem
into an npm or Python verification.

For a non-uv Python project, explicitly choose `--profile python-pip-editable`.
It inspects conventional test/dev requirements and extras before approval. If
the runner or dependencies cannot be established, planning stops with actionable
guidance rather than attempting an incomplete test environment. PEP 735 groups
are **not** pip extras; this profile currently reports them as unsupported.
The `python-uv` profile still requires a real `uv.lock`. An unlocked editable
install does not establish dependency determinism. See
[supported layouts and profile limits](docs/supported-platforms.md).

Backends:

- **Docker** is the default and the required backend for the documented isolated
  experiment. The image, daemon, host kernel, resource controls, and image user
  remain part of the trust boundary.
- **Host** is an explicit local-only fallback. It requires
  `--backend host --allow-host-exec` and should only be used when its residual
  risk is acceptable.

Acquisition is marked network-enabled because dependency installation may contact
registries. Verification commands are requested with networking disabled by the
Docker backend. This is bounded isolation, not a claim of complete hermeticity.

## How ReproCheck fits into your toolchain

ReproCheck is complementary to established build, CI, comparison, security, and
provenance tools. Its intended contribution is a developer-controlled workflow
that connects repository inspection, approval-gated execution, two-run outcome
comparison, selected artifact hashes, and a portable receipt.

| Tool or category | Designed for | How it overlaps | When ReproCheck may help alongside it |
|---|---|---|---|
| Debian [`reprotest`](https://manpages.debian.org/testing/reprotest/reprotest.1.en.html) | Repeating builds under environment variations and checking artifacts | Directly overlaps on repeated execution and reproducibility comparison | Start with a supported repository profile and preserve readable run classifications and receipt evidence; ReproCheck does not replace reprotest's variation matrix or diffoscope integration. |
| [`diffoscope`](https://diffoscope.org/) | Deep, recursive investigation of file, archive, and binary differences | Both can be used after outputs differ | Use ReproCheck to detect and contextualize drift, then diffoscope to investigate why artifacts differ. |
| [GitHub Actions](https://docs.github.com/en/actions) and CI | Event-driven jobs, tests, builds, matrices, and deployments | Both can execute repository workflows and retain evidence | Run ReproCheck locally before CI or invoke it from CI; it is not a hosted runner or CI replacement. |
| [Dagger](https://docs.dagger.io/getting-started/introduction/) | Programmable, portable, cached pipeline execution | Both can execute explicit work in containers | Choose Dagger for reusable pipeline architecture; choose ReproCheck for its narrower profile-and-receipt experiment. |
| [`repo2docker`](https://repo2docker.readthedocs.io/en/stable/) | Building and running repository-derived user environment images | Both use repository inputs and containers | Use ReproCheck for repeated verification and artifact comparison after environment preparation. |
| [Bazel hermeticity](https://bazel.build/basics/hermeticity) and build systems | Hermetic inputs, action graphs, toolchains, and caching | Both address host sensitivity and reproducibility | Use ReproCheck without migrating an existing supported workflow; it does not provide hermetic build-system guarantees. |
| [uv](https://docs.astral.sh/uv/), [pre-commit](https://pre-commit.com/), and [Ruff](https://docs.astral.sh/ruff/) | Dependency management, hooks, linting, and formatting | ReproCheck orchestrates some of these through profiles and its own quality gate | Keep using the specialized tools; ReproCheck is not another package manager, hook manager, linter, or formatter. |
| [Trivy](https://trivy.dev/docs/latest/), [OpenSSF Scorecard](https://github.com/ossf/scorecard), and [SLSA](https://slsa.dev/spec/v1.2/build-provenance) | Security scanning, security posture, and trusted provenance | All can contribute evidence about a repository or artifact | Use them for their security/provenance questions; ReproCheck's receipt is not a signature, attestation, or SLSA provenance statement. |

See the [competitive landscape](docs/competitive-landscape.md) for the evidence,
qualifications, implementation mapping, and non-goals behind this table.

## Security safeguards and limitations

The execution path uses argument-array subprocesses, `shell=False`, explicit
approval, temporary workspaces, path containment checks, symlink rejection,
bounded and redacted output, an environment allowlist, timeouts, process-group
cleanup, and best-effort Unix resource limits. Static scanning never executes a
README command, package script, CI step, Dockerfile, or dependency installer.

Important limits:

- A passing test does not prove the test is correct or that every output was
  selected for comparison.
- Results are scoped to one source snapshot, profile, backend, environment, and
  artifact selection.
- Docker is not a complete security sandbox; package installation can execute
  hooks and the daemon/host kernel remain trusted infrastructure.
- Static checks are conservative heuristics and do not resolve dependency graphs
  or prove security.
- The experiment is currently a Python-first two-run workflow. Other supported
  profiles can be verified where their plan requirements are met.

Read the [security model](docs/security-model.md) and
[supported-platforms notes](docs/supported-platforms.md) before treating a
receipt as a release or deployment attestation.

## Troubleshooting

| Situation | Meaning and next step |
|---|---|
| `uv` is not found | Install uv from its official documentation, then run `uv python install 3.12` and `uv sync --locked --dev`. |
| Python version is outside 3.12 | Use uv-managed Python 3.12; the project intentionally requires `<3.13`. |
| Docker is unavailable | `scan` still works. Docker-backed `verify`/`experiment` report blocked infrastructure; do not assume host execution. |
| The preview says `verification: denied` | This is the safe no-execution path. Review the printed argv plan and rerun with `--approve`. |
| A profile is rejected | Run `reprocheck verify --help` and choose `python-uv`, `python-pip-editable`, or `node-npm`; check the target's required manifest and lockfile. |
| An artifact is missing | Ensure the selected path is written by the verification commands and is relative to the target workspace root. The drift fixture writes its artifact during `pytest`. |
| Receipt validation is `invalid` | The file is malformed, tampered with, unsupported, or no longer matches its canonical integrity hash. Keep the original receipt and regenerate evidence if needed. |
| Result is `drift_detected` | Both verification runs may have passed. Inspect the per-run outcomes and selected artifact manifests; the artifact bytes differ. |

## Development

Install the locked development environment, then run the complete quality gate:

```bash
uv sync --locked --dev
make test
```

`make test` runs Ruff, Linux-targeted-compatible mypy configuration, format
validation, and the full pytest suite with locked/offline uv execution. On a
machine without GNU Make, the test suite itself can be run without a third-party
runner:

```bash
PYTHONPATH=src python3 -m unittest discover -s tests -p 'test_*.py'
```

Other useful checks:

```bash
uv run --locked --offline ruff check src tests
uv run --locked --offline ruff format --check src tests
uv run --locked --offline mypy --platform linux src
uv build
```

The CI workflow runs the locked checks, a self-scan, a Docker image build, and
the image entry-point smoke test. Contributions should preserve the approval
boundary and add a focused regression test for changed security or evidence
semantics.

### Contributing

Keep changes focused on evidence quality, portable execution, and honest user
communication. Before opening a change, run the documented quality checks,
update the relevant receipt or platform documentation, and add a regression
test when behavior or a security boundary changes. Do not include receipts,
generated build output, local environments, credentials, or machine-specific
paths in a commit.

## Documentation map

- [Guided reproducibility-drift demo](docs/reproducibility-drift-demo.md)
- [Architecture and trust boundaries](docs/architecture.md)
- [Supported platforms and profiles](docs/supported-platforms.md)
- [Receipt format](docs/receipt-format.md)
- [Security model](docs/security-model.md)
- [Static checks](docs/checks.md)
- [Benchmark methodology](docs/benchmark-methodology.md)

## License

ReproCheck is distributed under the [MIT License](LICENSE).
