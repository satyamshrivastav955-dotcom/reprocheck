# Supported platforms

## P0 support

| Area | Supported | Boundary |
|---|---|---|
| Python | Python 3.12 projects with `pyproject.toml`/`setup.py`; uv lockfile static checks | Other Python package managers are detected conservatively; only the explicit profiles are executable |
| Node.js/TypeScript | `package.json`, npm lockfile, TypeScript markers | pnpm and Yarn are detected but are not silently treated as npm verification |
| Operating system | Linux is the primary tested platform | Windows/macOS execution behavior depends on available runtimes and Docker |
| Container | Docker CLI backend when available | Docker daemon, kernel, image availability, and resource flags are external prerequisites |
| Other ecosystems | Reported as unsupported/partial markers | ReproCheck does not guess commands or package-manager behavior |

The static engine is designed to remain useful without Docker, Node.js, or a
package registry. Verification is narrower and reports blocked or unsupported
phases explicitly.
