# Supported platforms

## P0 support

| Area | Supported | Boundary |
|---|---|---|
| Python | Python 3.12 projects with `pyproject.toml`/`setup.py`; uv lockfile static checks | Other Python package managers are detected conservatively; only the explicit profiles are executable |
| Node.js/TypeScript | `package.json`, npm lockfile, TypeScript markers | pnpm and Yarn are detected but are not silently treated as npm verification |
| Operating system | Linux and Windows are supported and tested platforms | Windows/macOS execution behavior uses native path handling and cross-platform process adapters |
| Container | Docker CLI backend when available; Python uses the uv image and Node uses Node 22 slim | Docker daemon, kernel, image availability, resource flags, and image user are external prerequisites |
| Experiment | Python `python-uv` two-run workflow with explicit artifact comparison | Docker is required for the isolated experiment; host mode is an explicitly acknowledged local-only fallback. Drift keeps passing per-run results separate from the pair-level result |
| Other ecosystems | Reported as unsupported/partial markers | ReproCheck does not guess commands or package-manager behavior |

### Cross-platform compatibility

The unit and verification test suite supports native Windows execution without requiring WSL. Process execution harnesses and stub launchers adapt to Windows batch/cmd conventions while enforcing POSIX path safety inside Docker containers.

The static engine is designed to remain useful without Docker, Node.js, or a
package registry. Verification is narrower and reports blocked or unsupported
phases explicitly.

## Python verification layouts

`python-uv` requires regular root-level `pyproject.toml` and `uv.lock`. It keeps
the locked `uv sync --locked` acquisition step and the existing pytest-based
verification when a `tests/` tree is discovered. It does not generate a lockfile
or silently use editable pip when the lockfile is absent. A project using another
dependency manager should review the explicit `python-pip-editable` profile.

The editable-pip profile constructs its acquisition command from bounded static
metadata, without importing `setup.py` or executing any repository code:

- Root `requirements-dev.txt` and `requirements-test.txt` are installed with
  `-r` in the same command as `pip install -e .`.
- Declared `project.optional-dependencies` or legacy `setup.cfg` extras named
  `dev`, `development`, `test`, `tests`, or `testing` are selected as actual
  extras. Multiple selected extras and requirement files are combined into one
  pip invocation so the resolver can handle overlaps together.
- Local requirements `-r`/`-c` includes are bounded, acyclic, contained, and
  symlink-free. Constraints alone do not install pytest. URLs, local editable
  requirements, installer options, conditional-only runner declarations, and
  dynamic dependency metadata cannot establish a reliable test environment.
- PEP 735 `[dependency-groups]` are not distribution extras. Test/dev groups
  currently stop editable-pip planning with an actionable unsupported message;
  use supported requirements/extras, or the uv profile with a real lockfile.
  The profile does not assume that every image's pip supports `--group`.
- An unconditional declared pytest dependency establishes the conservative
  pytest default. Recognized pytest configuration without installable pytest
  stops planning instead of running a known-incomplete command.
- A literal `tox.ini` `[testenv]` command of
  `python -m unittest discover -s tests` selects the standard-library runner.
  Only allowlisted pytest/unittest command forms are interpreted; arbitrary tox
  commands are never executed by the planner. Tox dependencies can repeat
  already-acquired literal requirements/includes; unknown or unselected tox
  dependencies, extras, substitutions, or groups stop planning.
- Missing or conflicting test-environment evidence is a `ProfileError` (CLI
  input error, exit `2`) before any execution, even if `--approve` was supplied.
  A target without a discovered test directory has no test command planned,
  which is recorded as a limitation rather than proof of test success.

For example, a supported target containing `requirements-dev.txt` with pytest
has this plan:

```text
phase=acquisition network=true argv=python -m pip install -e . -r requirements-dev.txt
phase=verification network=false argv=python -m pytest
```

All acquisition remains network-required. Verification networking remains
disabled by the Docker backend; host mode neither isolates the process nor
enforces network isolation. Installing unpinned requirements can work, but it
does not establish locked dependency resolution or universal reproducibility.
