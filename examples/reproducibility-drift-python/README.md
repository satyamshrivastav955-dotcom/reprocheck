# Reproducibility drift fixture

This Python fixture is intentionally used to demonstrate a real observed
artifact difference. Its test suite passes, but each run writes a fresh random
value to `reprocheck-demo-artifact.txt`.

The experiment should therefore report:

- Run A: `verified_pass`
- Run B: `verified_pass`
- selected artifact hashes: different
- experiment: `drift_detected`
- reproducibility: not established

The command exits `1` because the pair-level reproducibility result is negative;
that exit code does not mean either test run failed. The result demonstrates only
this fixture, profile, and Docker environment; it is not a universal
reproducibility claim.
