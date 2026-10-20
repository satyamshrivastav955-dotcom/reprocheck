# Evidence receipt format

Receipts use the versioned `receipt-v1` JSON shape in
[`schemas/receipt-v1.json`](../schemas/receipt-v1.json).

The receipt records:

- portable repository identity (`root` is recorded as `.` rather than the
  producer's absolute checkout path) and `filesystem-v1` content fingerprint;
- selected verification profile;
- non-secret environment fingerprint and tool/backend information;
- planned and executed argv commands, phases, network requirement, timestamps,
  exit codes, timeout status, and bounded redacted output;
- static findings, executed verification runs, and explicit limitations;
- integrity metadata with a canonical SHA-256 hash.

`integrity.details.canonical_sha256` is required for `receipt-v1` and must be a
lowercase 64-character hexadecimal SHA-256 value. The loader rejects unsupported
schema versions, malformed field types, non-finite numeric values, missing
required fields, and invalid integrity metadata before validation proceeds.

An experiment receipt keeps the existing `receipt-v1` top-level shape for
compatibility. Its `integrity.details.experiment` object uses the nested
`experiment-v1` contract and records the common source fingerprint, frozen plan
fingerprint, backend/image information, two run IDs, selected artifact manifests,
the pair-level classification, comparison details, and limitations. The two full
run records remain in `verification_runs`.

Canonicalization sorts JSON object keys and removes timestamps from the hashed
payload. The integrity hash field itself is excluded from the payload before
hashing. `reprocheck receipt validate` recomputes the hash and can also compare
the repository content fingerprint.

Validation reports structural or integrity errors separately from a repository
content mismatch where possible. A valid receipt can still fail the optional
repository comparison if the checkout has changed since the receipt was made.

This integrity model detects accidental corruption and changes relative to a
trusted receipt. It is not a signature or external attestation: an actor who can
rewrite both the receipt and its adjacent hash can forge a new receipt. Signed
receipts, transparency logs, and external attestations are future work.

`reprocheck receipt compare` ignores timestamps and reports changes in repository
content, commands, findings, outcomes, and environment. It does not claim that
two different environments are equivalent.

Experiment artifact manifests use `artifacts-v1`. They hash explicitly selected
workspace-relative files or directories using sorted relative paths, byte sizes,
and raw file bytes. Missing, empty, or unselected artifact evidence is incomplete;
it is not treated as an equal empty result.
