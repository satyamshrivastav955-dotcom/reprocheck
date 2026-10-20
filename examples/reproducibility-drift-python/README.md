# Reproducibility drift fixture

This Python fixture is intentionally used to demonstrate a real observed
artifact difference. Its test suite passes, but each run writes a fresh random
value to `reprocheck-demo-artifact.txt`.

The experiment should therefore report two passing runs with
`drift_detected` for that explicitly selected artifact. The result demonstrates
only this fixture, profile, and Docker environment; it is not a universal
reproducibility claim.
