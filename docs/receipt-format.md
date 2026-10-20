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

Canonicalization sorts JSON object keys and removes timestamps from the hashed
payload. The integrity hash field itself is excluded from the payload before
hashing. `reprocheck receipt validate` recomputes the hash and can also compare
the repository content fingerprint.

This integrity model detects accidental corruption and changes relative to a
trusted receipt. It is not a signature or external attestation: an actor who can
rewrite both the receipt and its adjacent hash can forge a new receipt. Signed
receipts, transparency logs, and external attestations are future work.

`reprocheck receipt compare` ignores timestamps and reports changes in repository
content, commands, findings, outcomes, and environment. It does not claim that
two different environments are equivalent.
