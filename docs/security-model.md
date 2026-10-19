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
- Docker `--network none` for non-acquisition commands when Docker is available.

Docker is not a complete sandbox. The Docker daemon, host kernel, container
runtime, image, resource configuration, and dependency acquisition phase remain
in the trust boundary. Host execution is blocked unless both `--backend host` and
`--allow-host-exec` are explicit, and its residual risk is recorded in the
receipt. If Docker is unavailable, ReproCheck returns `NOT_VERIFIED` rather than
claiming an isolated pass.

Never use ReproCheck to run a repository that has access to credentials or a
production Docker socket. Review every proposed command and receipt limitation.
