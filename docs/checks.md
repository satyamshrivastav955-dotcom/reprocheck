# ReproCheck check catalog

Checks are static unless a report explicitly contains an executed verification
run. A static check reads bounded repository files and returns evidence; it does
not prove that a command will succeed.

| ID | Scope | Severity | Static result | Evidence | Main limitation |
|---|---|---:|---|---|---|
| `RC-DISC-001` | Ecosystem detection and applicability | info | `LOOKS_CORRECT` or `NOT_VERIFIED` | Discovered markers | Marker presence does not identify framework semantics |
| `RC-DEP-001` | Python/Node dependency manifest | info | `PRESENT`, `STATIC_FAIL`, or `NOT_VERIFIED` | Manifest path | Does not resolve dependencies |
| `RC-DEP-002` | Recognized lockfile | high | `PRESENT`, `STATIC_FAIL`, or `NOT_VERIFIED` | Lockfile path and bounded parse | Presence does not prove lock integrity or installability |
| `RC-RUN-001` | Python/Node runtime pin/declaration | medium | `LOOKS_CORRECT`, `STATIC_FAIL`, or `NOT_VERIFIED` | `.python-version`, `pyproject.toml`, `.nvmrc`, or engines | Ranges are not resolved and platform availability is unknown |
| `RC-CMD-001` | Test/build command discoverability | medium | `LOOKS_CORRECT`, `STATIC_FAIL`, or `NOT_VERIFIED` | README, scripts, or CI | Recognized commands are not executed during scan |
| `RC-CMD-002` | Command parity across sources | medium | `LOOKS_CORRECT`, `STATIC_FAIL`, or `NOT_VERIFIED` | Source paths and command plan | Complex shell/YAML is conservatively unsupported |
| `RC-ENV-001` | Environment template/documentation | low | `PRESENT`, `STATIC_FAIL`, or `NOT_VERIFIED` | `.env.example` or docs | Cannot infer every variable from application code |
| `RC-CI-001` | CI setup and test workflow | medium | `LOOKS_CORRECT`, `STATIC_FAIL`, or `NOT_VERIFIED` | Workflow path and bounded heuristics | YAML is not fully parsed |
| `RC-CON-001` | Dockerfile/compose definition | medium | `LOOKS_CORRECT`, `STATIC_FAIL`, or `NOT_VERIFIED` | Container definition | No image build or compose execution in scan |
| `RC-PATH-001` | Machine-specific absolute paths | medium | `STATIC_FAIL` or `LOOKS_CORRECT` | Redacted source path and excerpt | A path can be intentional |
| `RC-SEC-001` | High-confidence committed secret patterns | high | `STATIC_FAIL` or `LOOKS_CORRECT` | Redacted source path/excerpt | Not a complete secret scanner; values are not retained |
| `RC-GIT-001` | Generated/local artifact hygiene | low | `LOOKS_CORRECT`, `STATIC_FAIL`, or `NOT_VERIFIED` | `.gitignore` | Does not inspect the Git index |
| `RC-PKG-001` | Package metadata and entry consistency | medium | `LOOKS_CORRECT`, `STATIC_FAIL`, or `NOT_VERIFIED` | Manifest and scripts | Does not build or import the package |

### Result rules

- `PRESENT` means an expected artifact was detected only.
- `LOOKS_CORRECT` means the bounded static check found no issue within its scope.
- `STATIC_FAIL` means static evidence identifies a missing, malformed, inconsistent,
  or suspicious condition.
- `VERIFIED_PASS` and `VERIFIED_FAIL` are reserved for commands that actually ran.
- `NOT_VERIFIED` means the result was unsupported, inaccessible, denied, timed out,
  or otherwise could not be established.

Every finding includes a stable ID, severity, state, assessment, evidence,
remediation when actionable, and the check's false-positive policy. Findings are
ordered deterministically and use relative repository paths in evidence.

The static readiness score and verified execution score are independent. A
successful executed command does not promote unrelated static findings to
`VERIFIED_PASS`. Supported profile test/build execution is explicitly attributed
to `RC-CMD-001` by its check ID, verification phase, actual command outcome, and
membership in the run's evidence IDs. This can earn the commands group (15
points) only; it cannot establish documentation parity (`RC-CMD-002`), lockfile
integrity, CI correctness, or portability. All command runs in a report must
provide passing linked verification evidence for that positive contribution.

The static score still uses the original static findings, even when commands
execute successfully. The verified denominator is the same applicable-group
weight, not just the tested groups; unverified groups score zero with explicit
limitations. A `0/<applicable weight>` verified score can therefore mean failed,
unlinked, acquisition-only, denied, or incomplete execution evidence. Older
receipts without explicit check attribution remain valid but do not receive
retroactively inferred credit. Neither score establishes artifact equality.

## Bounded workflow and applicability rules

- CI coverage reads recognizable GitHub job steps, not workflow names, comments,
  or action input text. Runtime/dependency setup must precede tests in the same
  job. Checkout alone is not setup evidence. Other providers or complex YAML may
  remain `NOT_VERIFIED`.
- Literal root Make targets can delegate to recognized pytest, unittest, npm test,
  or build commands through bounded prerequisites/recipes. Simple declared
  variable defaults (`=`, `:=`, `?=`) are supported and recorded in evidence;
  runtime overrides are not evaluated. Dynamic functions, includes, cycles,
  shell controls, and unresolved targets remain uncertain. Scanning never runs
  Make or a workflow.
- Command consistency compares per-intent command identities and established
  wrappers across independent sources. Explicitly labeled fallback/alternative
  snippets are not the canonical workflow. Tables describing supported tool
  profiles are not project invocations. Genuine divergent runner/target
  instructions remain conflicts; unresolvable wrappers do not earn a pass.
- `RC-DEP-002` accepts recognized lock formats (including `pylock.toml`) or
  bounded exact requirements pins without claiming dependency closure. Ranges,
  wildcard pins, unrelated dev pins, and missing locks remain visible. A tool-only
  `pyproject.toml` is not by itself a dependency-lock obligation; no check requires
  a library to adopt uv specifically.
- `RC-ENV-001` distinguishes explicit required variables from unknown
  applicability. No template and no requirements evidence is uncertain, not a
  pass or an automatic library defect. Required variables missing template/docs
  entries fail; explicit no-required-env documentation is positive static
  documentation evidence, not proof from executing the application.
- `.gitignore` inspection accepts common equivalent bytecode, cache, coverage,
  build, and local-environment patterns, with bounded ordered negation handling.
  It does not query the Git index or implement all Git pattern semantics.
