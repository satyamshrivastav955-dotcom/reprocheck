# Security model

ReproCheck has two intentionally different trust boundaries.

## Static scan

`reprocheck scan` is read-only. It does not execute README commands, package
scripts, CI workflows, Dockerfiles, build backends, or dependency installers.
Filesystem traversal does not follow repository symlinks. Files are opened with
bounded reads and path-containment checks where the platform supports them.

Static scanning can still be exposed to malformed files and denial-of-service
inputs. It is not a complete malware, secret, or vulnerability scanner.

## Verification

`reprocheck verify` prints an allow-listed argv plan before any command runs. No
command runs unless `--approve` is supplied. The command planner does not turn
arbitrary README text into shell code.

Dependency acquisition is a separate network-enabled phase because package
managers and build backends can execute lifecycle hooks. Later verification
commands are requested with networking disabled when the Docker backend is used.

The executor applies:

- `shell=False` and argument arrays;
- a fresh, allow-listed environment with likely credentials omitted;
- bounded stdout/stderr and redaction before receipt persistence;
- process-group termination on timeout;
- temporary regular-file-only workspaces with symlink rejection;
- best-effort CPU/address-space limits on Unix;
- Docker `--network none` for non-acquisition commands when Docker is available;
- Docker capability dropping, `no-new-privileges`, CPU/memory/process limits, and
  a bounded `/tmp` tmpfs when the daemon supports those options.

Docker is not a complete sandbox. The Docker daemon, host kernel, container
runtime, image, resource configuration, and dependency acquisition phase remain
in the trust boundary. Host execution is blocked unless both `--backend host` and
`--allow-host-exec` are explicit, and its residual risk is recorded in the
receipt. The backend does not force a universal non-root UID because package
acquisition and project workspaces have incompatible ownership requirements
across images; it does not claim non-root isolation. If Docker is unavailable,
ReproCheck returns `NOT_VERIFIED` rather than claiming an isolated pass.

Never use ReproCheck to run a repository that has access to credentials or a
production Docker socket. Review every proposed command and receipt limitation.

## Independent experiments

The experiment workflow captures the source once before either run and materializes
two separate workspaces from that snapshot. It rejects symlinks and source changes
that make the captured manifest inconsistent. Each run gets separate disposable
containers and writable state; dependency caches are not shared.

Artifact comparison accepts only explicit workspace-relative paths. Absolute paths,
parent traversal, symlinks, and non-regular entries are rejected. Artifact hashes
and bounded redacted command evidence are stored in the receipt. A passing test with
a different selected artifact is reported as drift, while missing or incomplete
evidence is reported as incomplete rather than as a pass.

Experiment reports expose Run A and Run B outcomes independently from the
pair-level classification. Therefore `drift_detected` can legitimately contain
two `verified_pass` runs: the verification commands passed, but the selected
artifact evidence did not match. The process exits nonzero for that negative
reproducibility conclusion without relabeling either run as a test failure.
