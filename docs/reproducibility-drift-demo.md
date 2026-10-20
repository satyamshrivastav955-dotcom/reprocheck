# Reproducibility drift demo

This controlled Python fixture demonstrates why two passing test runs are not
the same as reproducible output.

## What the fixture does

`examples/reproducibility-drift-python/tests/test_artifact.py` writes a fresh
random value to `reprocheck-demo-artifact.txt` and then passes its test. The
experiment selects that file from each independent workspace and compares its
bytes and hash.

The expected conclusion is therefore:

- Run A: `pass / verified_pass`
- Run B: `pass / verified_pass`
- selected artifact: different
- experiment: `drift_detected`
- reproducibility: not established
- process exit code: `1`

This is an observed result for this fixture, profile, backend, and source
snapshot. It is not a universal claim about Python or Docker.

## Run it

From the ReproCheck checkout, first install the locked environment and make sure
Docker is running:

```bash
uv python install 3.12
uv sync --locked --dev
docker --version
```

POSIX shell:

```bash
set +e
uv run --locked reprocheck experiment \
  examples/reproducibility-drift-python \
  --profile python-uv \
  --backend docker \
  --artifact reprocheck-demo-artifact.txt \
  --receipt experiment-receipt.json \
  --format terminal \
  --approve
status=$?
set -e
printf 'experiment exit code: %s\n' "$status"
```

PowerShell:

```powershell
uv run --locked reprocheck experiment `
  examples/reproducibility-drift-python `
  --profile python-uv `
  --backend docker `
  --artifact reprocheck-demo-artifact.txt `
  --receipt experiment-receipt.json `
  --format terminal `
  --approve
$status = $LASTEXITCODE
Write-Output "experiment exit code: $status"
```

The experiment may acquire dependencies during its network-enabled acquisition
phase. Its verification commands are requested with Docker networking disabled.

## Read the result

The important terminal lines look like this:

```text
verification: pass
experiment: drift_detected
verification_runs: 2 (passed: 2, failed: 0)
comparison_valid: true
reproducibility: not established
artifacts_match: false
experiment_exit_code: 1
```

`verification: pass` summarizes the two command executions. `drift_detected`
and `artifacts_match: false` summarize the pair-level comparison. The exit code
is nonzero because reproducibility was not established, not because both test
runs failed.

## Validate the receipt

```bash
uv run --locked --offline reprocheck receipt validate \
  experiment-receipt.json \
  --repository examples/reproducibility-drift-python
```

```powershell
uv run --locked --offline reprocheck receipt validate `
  experiment-receipt.json `
  --repository examples/reproducibility-drift-python
```

A valid result confirms that the receipt is structurally supported, its
canonical SHA-256 matches the stored integrity metadata, and the optional
repository comparison matches. It does not turn the receipt into a signed
attestation.

If validation reports `invalid`, preserve the original file and inspect the
specific error. Common causes are an edited or truncated receipt, an unsupported
schema, a changed integrity field, or a repository checkout that no longer
matches the recorded source fingerprint.
