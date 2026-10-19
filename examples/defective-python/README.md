# Defective Python demo fixture

This intentionally incomplete fixture is used in the ReproCheck demonstration.
It has a manifest and a documented test command, but no runtime pin, lockfile,
environment template, CI workflow, or container definition.

```bash
python -m pytest
```

The command is documentation only until a user explicitly asks ReproCheck to
verify a supported profile.
