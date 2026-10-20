# Supported platforms

## P0 support

| Area | Supported | Boundary |
|---|---|---|
| Python | Python 3.12 projects with `pyproject.toml`/`setup.py`; uv lockfile static checks | Other Python package managers are detected conservatively; only the explicit profiles are executable |
| Node.js/TypeScript | `package.json`, npm lockfile, TypeScript markers | pnpm and Yarn are detected but are not silently treated as npm verification |
| Operating system | Linux and Windows are supported and tested platforms | Windows/macOS execution behavior uses native path handling and cross-platform process adapters |
| Container | Docker CLI backend when available; Python uses the uv image and Node uses Node 22 slim | Docker daemon, kernel, image availability, resource flags, and image user are external prerequisites |
| Experiment | Python `python-uv` two-run workflow with explicit artifact comparison | Docker is required for the isolated experiment; host mode is an explicitly acknowledged local-only fallback |
| Other ecosystems | Reported as unsupported/partial markers | ReproCheck does not guess commands or package-manager behavior |

### Cross-platform compatibility

The unit and verification test suite supports native Windows execution without requiring WSL. Process execution harnesses and stub launchers adapt to Windows batch/cmd conventions while enforcing POSIX path safety inside Docker containers.

The static engine is designed to remain useful without Docker, Node.js, or a
package registry. Verification is narrower and reports blocked or unsupported
phases explicitly.
