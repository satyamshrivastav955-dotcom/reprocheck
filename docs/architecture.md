# Architecture

ReproCheck is a local-first Python CLI with a deliberately separated scan,
verification, experiment, and receipt pipeline.

```text
repository path
      |
      v
discovery + fingerprint + static checks  --->  report renderers
      |
      v
explicit verification plan
      |
      +--> approval gate --> clean workspace --> host or Docker executor
      |                                           |
      |                                           v
      |                                    run outcomes + evidence
      |
      +--> two independent workspaces --> artifact manifests --> experiment classification
                                                               |
                                                               v
                                                        canonical receipt
```

## Main packages

- `src/reprocheck/cli.py` owns command parsing, exit policy, plan display, and
  report orchestration.
- `src/reprocheck/discovery.py` and `fingerprint.py` inspect repository files
  without executing repository-controlled commands.
- `src/reprocheck/checks/` contains the static check registry and bounded
  ecosystem checks.
- `src/reprocheck/verification/` contains profile planning, approval-gated
  execution, workspace materialization, experiments, and artifact manifests.
- `src/reprocheck/receipts/` builds canonical JSON receipts, validates their
  integrity metadata, and compares receipts.
- `src/reprocheck/reporting/` renders terminal, JSON, and Markdown reports.

## Trust boundaries

`scan` is read-only and does not invoke package managers, test commands, CI
steps, or Dockerfiles. `verify` and `experiment` execute only an explicit plan
after approval. Docker provides backend isolation and disabled verification
networking, but the Docker daemon, host kernel, image, and package-install hooks
remain part of the trust boundary. Host execution is an explicit, non-sandboxed
fallback.

The receipt records what was observed. It does not provide a signature or an
external provenance attestation.
