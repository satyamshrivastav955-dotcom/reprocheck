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
