# Benchmark methodology

The full public-repository benchmark is intentionally deferred until the P0
verifier is stable. No benchmark result is claimed by this repository yet.

Before collecting outcomes, select a fixed seeded sample of 20–30 public Python
and Node.js/TypeScript repositories, stratified by ecosystem, size, age,
popularity, packaging style, and presence of CI/container configuration. Commit
the URL, license, selected commit SHA, and exclusion rules before running the
tool. Exclude repositories requiring credentials, private registries,
destructive infrastructure, or prohibited data access.

For each selected commit, record the ReproCheck version, check-catalog version,
container image/runtime, resource limits, command plan, network policy, start/end
timestamps, bounded outcome, and limitations. Repeat a subset of failures to
measure flakiness. Do not repair a repository before its initial run.

Measure install/build/test success, failure categories, duration, resource use
where available, unsupported/blocked phases, timeout and flake rates, and a
reviewer's true-positive/actionability labels. Publish the selection manifest,
methodology, result table, limitations, and false-positive review method. Never
publish credentials or unbounded repository output.

For the buildathon, a local fixture suite and a small pilot are acceptable only
if the core verifier is already stable. A partial pilot must be labeled partial;
missing results must never be invented.
