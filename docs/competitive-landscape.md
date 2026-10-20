# Competitive landscape and product positioning

**Review date:** 2026-10-20

## Purpose and scope

ReproCheck is an evidence-first reproducibility experiment engine for developers
working with supported repositories. It combines repository inspection, explicit
verification planning, approval-gated execution, two independent runs, selected
artifact comparison, readable classification, and a portable receipt.

That combination is useful, but it is not a claim that ReproCheck invented
repeated builds, artifact comparison, reproducible-build testing, containers,
CI, or evidence records. Several established tools overlap with important parts
of this workflow. This document explains the overlap and the narrower problem
ReproCheck is choosing to make approachable.

## The defensible product thesis

> ReproCheck connects repository inspection, explicit verification planning,
> independent execution, artifact comparison, and portable evidence into one
> developer-controlled reproducibility experiment for supported repositories.

The qualification matters. A completed `reproducible` experiment establishes
that the selected comparison criteria matched in the recorded runs. It does not
prove universal reproducibility, complete hermeticity, semantic equivalence of
all outputs, or trusted provenance about who produced the receipt.

## Nearest direct comparator: Debian `reprotest`

[`reprotest(1)`](https://manpages.debian.org/testing/reprotest/reprotest.1.en.html)
is the closest direct comparison. Its documented purpose is to build the same
source code twice in different environments and check the resulting binaries
for differences. It supports source roots, build commands, artifact patterns,
virtual servers, and variations such as time, locale, hostname, file ordering,
CPU count, paths, and environment. It can use `diffoscope` or `diff` to show
differences.

The overlap is real:

- both use repeated execution against a source input;
- both compare resulting artifacts;
- both can expose nondeterministic build results;
- both are useful evidence rather than a universal proof of reproducibility.

ReproCheck does **not** claim to replace or improve `reprotest`. Its narrower
developer-facing contribution is the surrounding workflow for a supported
repository: static discovery before execution, recognizable profile planning,
an argv-level approval boundary, explicit separation of run status from pair
classification, selected artifact manifests, and a versioned receipt that can
be validated locally. ReproCheck does not provide `reprotest`'s environment
variation matrix, Debian/RPM presets, virtual-server choices, or built-in
diffoscope analysis.

## Investigating drift: `diffoscope`

[`diffoscope`](https://diffoscope.org/) is an in-depth comparison tool for
files, directories, and archives. It recursively unpacks many formats and
transforms binary formats into more readable representations so a developer can
investigate *why* two outputs differ.

ReproCheck currently records selected artifact bytes, file hashes, sizes, and an
aggregate manifest digest. It answers “did this selected evidence differ?”; it
does not explain the internal binary or archive difference. A practical combined
workflow is:

1. Run ReproCheck and inspect the per-run outcomes and artifact manifests.
2. If the classification is `drift_detected`, preserve the two artifact copies
   or reconstruct them from the same workflow.
3. Use diffoscope to investigate the differing files or archives.

The tools are complementary: ReproCheck supplies the experiment context and
classification; diffoscope supplies deep difference analysis.

## CI and pipeline tools

### GitHub Actions and conventional CI

[GitHub Actions](https://docs.github.com/en/actions/about-github-actions/understanding-github-actions)
is a CI/CD platform. Repository workflows respond to events and run jobs and
steps on hosted or self-hosted runners. A workflow can certainly run tests more
than once, use a matrix, compare files, and upload artifacts when configured to
do so.

ReproCheck is not a hosted runner service, event-driven workflow platform, or
deployment system. It is intended to complement CI in two ways:

- a developer can begin locally with a target repository, preview the proposed
  plan, and approve an experiment before adding CI configuration;
- CI can invoke ReproCheck and retain its JSON report or receipt alongside the
  ordinary job artifacts.

An ordinary green CI job and a ReproCheck `reproducible` experiment answer
different questions. Neither should be presented as proof beyond its recorded
inputs and comparison criteria.

### Dagger

[Dagger](https://docs.dagger.io/getting-started/introduction/) describes itself
as a programmable CI stack with real code, a portable DAG execution engine,
multi-container environments, caching, tracing, and local or cloud execution.

Dagger is appropriate when a team wants to model and operate a reusable,
programmable pipeline. ReproCheck is intentionally narrower: it recognizes a
small set of repository profiles, executes a displayed plan, compares two runs,
and writes evidence. It is not a DAG scheduler, cache, cloud execution service,
or pipeline-tracing system.

## Environment builders and build systems

### repo2docker

[`repo2docker`](https://repo2docker.readthedocs.io/en/stable/) reproducibly
builds and runs user-environment container images from repository configuration,
especially for interactive computing and data workflows. It can inspect
configuration, build an image, run it interactively, and optionally push it to a
registry.

The overlap is repository-to-environment preparation and container execution.
The distinction is the next question: ReproCheck uses an execution backend to
run a supported verification plan twice and compare selected outcomes and
artifacts. It is not a repo2docker buildpack system, Jupyter/Binder environment
builder, or image-publishing tool.

### Bazel and hermetic build systems

[Bazel's hermeticity guidance](https://bazel.build/basics/hermeticity) describes
builds isolated from host changes, with managed tools and dependencies,
identified inputs, action graphs, and caching. A mature hermetic build system is
the right choice when a project is ready to model its build architecture and
input closure explicitly.

ReproCheck does not implement hermetic builds, remote execution, action graphs,
remote caching, or Bazel-compatible toolchain management. Its clean workspaces
and Docker controls can expose some host-dependence, but the documented Docker
boundary is not complete hermeticity. ReproCheck's accessibility trade-off is
that a developer can test a supported existing workflow without first migrating
the project to a new build architecture.

## Package and code-quality tools

ReproCheck reuses established tools rather than competing with them:

- [uv](https://docs.astral.sh/uv/) manages Python versions, projects,
  environments, and lockfiles. ReproCheck's `python-uv` profile plans
  `uv sync --locked`; uv itself does not compare independent artifact outputs.
- [pre-commit](https://pre-commit.com/) manages and runs multi-language Git
  hooks. ReproCheck does not install hooks or replace staged-file checks.
- [Ruff](https://docs.astral.sh/ruff/) is a Python linter and formatter. It is
  part of ReproCheck's own development quality gate, not the purpose of a target
  repository scan.

A passing package-manager sync, hook run, lint, or formatter check is useful
evidence for that tool's question. It is not by itself an artifact
reproducibility result.

## Security and supply-chain tools

These tools are adjacent rather than direct competitors:

- [Trivy](https://trivy.dev/docs/latest/) scans images, filesystems, and
  repositories for vulnerabilities, dependencies/SBOM data, misconfigurations,
  secrets, licenses, and related security findings. ReproCheck's static checks
  are narrower heuristics and are not a vulnerability scanner, SBOM generator,
  or complete secret scanner.
- [OpenSSF Scorecard](https://github.com/ossf/scorecard) evaluates open-source
  security posture using heuristic checks and per-check scores. ReproCheck does
  not produce a Scorecard score or assess the same repository-host controls.
- [SLSA Build Provenance v1.2](https://slsa.dev/spec/v1.2/build-provenance)
  defines provenance describing how artifacts were produced, including build
  definitions, resolved dependencies, builder identity, invocation metadata,
  and artifact subjects. ReproCheck's receipt records local observations and a
  canonical SHA-256 integrity value; it is not a signed attestation, trusted
  builder statement, or SLSA provenance document.

Use these tools where their security or provenance questions apply. ReproCheck's
central question remains whether a selected verification workflow produced
matching evidence in two recorded runs.

## Evidence-backed differentiation

| Product claim | Implementation evidence | Demonstration or test evidence | Overlap and qualification |
|---|---|---|---|
| Inspect before execution | `scan_repository()` and the static check registry; scan does not call the executor | `tests/unit/test_cli.py`, `tests/unit/test_checks.py` | CI and security scanners also inspect repositories; this is not unique. |
| Show an explicit plan and require approval | `_show_plan()`, `execute_plan(... approved=...)`, `SubprocessExecutor.execute()` | `test_verify_requires_approval_and_emits_a_receipt`, `test_approval_and_host_policy_gate_execution` | Similar approval or policy controls can be built around CI or reprotest. |
| Repeat one plan in independent workspaces | `run_experiment()`, `materialize_workspace()`, two run IDs | `tests/unit/test_experiment.py` and the Docker drift run | Reprotest already performs repeated builds; ReproCheck does not claim invention here. |
| Keep run status separate from pair classification | `_experiment_verification_status()`, experiment summary fields, terminal/JSON/Markdown renderers | `test_drift_reports_passing_runs_and_reproducibility_not_established` and observed `pass/pass + drift_detected` | The value is an explicit product contract, not a claim that other tools cannot represent it. |
| Compare selected artifacts by bytes and hashes | `collect_artifacts()`, `artifacts_match()`, `artifacts-v1` manifests | artifact unit tests and the drift fixture | This is a manifest equality check, not semantic or deep archive analysis. |
| Preserve portable receipt evidence | `build_receipt()`, canonical hash, strict loader and validator | receipt round-trip, tamper, and validation tests | The receipt is locally hash-protected, not signed or externally trusted. |
| Recognize supported repository profiles | `available_profiles()` and `plan_verification()` for `python-uv`, `python-pip-editable`, and `node-npm` | planner tests and CLI help | Profile recognition is bounded; Node profile availability does not establish a Node two-run acceptance result. |

## Supported scope and limitations

### Profiles, planning, and execution

Static discovery may identify Python, Node, or other markers. Full execution is
narrower and requires a recognized profile's manifests and lockfiles. The
current profile set is:

- `python-uv`: `pyproject.toml` plus `uv.lock`; plans `uv sync --locked` and
  `uv run python -m pytest` when tests are present.
- `python-pip-editable`: explicit opt-in editable pip installation; it is not an
  automatic fallback.
- `node-npm`: `package.json` plus `package-lock.json`; plans `npm ci` and only
  declared test/build scripts.

The two-run engine is implemented around the same plan abstraction, but the
verified acceptance workflow, fixture, receipt mode, and end-to-end evidence in
this repository are Python-first. The presence of `node-npm` in help means that
the planner recognizes that profile; it is not a claim that every Node project
or the two-run experiment has been validated.

### Source capture and workspace independence

The experiment computes a `filesystem-v1` fingerprint over discovered regular
files, using canonical relative paths and file bytes. Ignored discovery
directories, symlinks, and symlink targets are excluded. It materializes a
regular-file-only snapshot, then creates independent temporary workspaces from
that snapshot for the two runs. It checks that the original repository's final
fingerprint still matches the initial fingerprint.

This is a bounded source snapshot and workspace boundary, not a claim that every
external input, registry response, daemon behavior, or host condition is frozen.

### Artifacts and number of runs

Artifact comparison is explicit: the caller selects workspace-relative files or
directories. ReproCheck records each selected regular file's path, size, and
SHA-256 plus an aggregate manifest digest. Equal digests establish byte equality
for the selected manifests in these runs. They do not prove semantic equality,
that all outputs were selected, or that unselected side effects did not occur.

The main experiment compares exactly **two** runs. It does not estimate a
nondeterminism rate, provide statistical confidence, or establish long-term
stability.

### Docker, network, and receipts

Docker is an execution backend, not a complete hermetic sandbox. The daemon,
host kernel, image contents, image user, resource controls, and dependency hooks
remain part of the trust boundary. Acquisition is marked network-enabled because
dependency installation may contact registries; verification commands are
requested with networking disabled by the Docker backend.

Receipt validation checks supported structure, canonical JSON integrity, and an
optional repository content comparison. A plain canonical hash does not prove
who created the receipt, prevent a malicious producer from rewriting both the
receipt and hash, or satisfy SLSA provenance requirements.

Static findings are bounded heuristics. They are not a complete security audit,
dependency-resolution proof, or guarantee that a recognized command is correct.

## When ReproCheck is useful

ReproCheck is a reasonable fit when a developer wants to:

- evaluate an unfamiliar repository before trusting its documented test/build
  path;
- check whether a supported workflow remains reproducible before merging;
- investigate whether a coding agent or automation produced stable selected
  outputs; or
- attach readable local evidence to a review while retaining existing CI,
  reproducible-build, security, or provenance tooling.

Teams with a mature `reprotest`, Bazel, Dagger, or signed-provenance workflow may
reasonably continue using it. ReproCheck is intended to complement those systems
where its explicit, smaller workflow is easier to start or easier to explain.

## References

Primary sources reviewed on 2026-10-20:

- [`reprotest(1)`](https://manpages.debian.org/testing/reprotest/reprotest.1.en.html)
- [`diffoscope`](https://diffoscope.org/)
- [GitHub Actions](https://docs.github.com/en/actions/about-github-actions/understanding-github-actions)
- [Dagger introduction](https://docs.dagger.io/getting-started/introduction/)
- [`repo2docker`](https://repo2docker.readthedocs.io/en/stable/)
- [Bazel hermeticity](https://bazel.build/basics/hermeticity)
- [`uv`](https://docs.astral.sh/uv/)
- [`pre-commit`](https://pre-commit.com/)
- [Ruff](https://docs.astral.sh/ruff/)
- [Trivy documentation](https://trivy.dev/docs/latest/)
- [OpenSSF Scorecard](https://github.com/ossf/scorecard)
- [SLSA Build Provenance v1.2](https://slsa.dev/spec/v1.2/build-provenance)

The sources support purpose-level comparisons only. This document does not rank
tools by speed, accuracy, adoption, reliability, or market share.
