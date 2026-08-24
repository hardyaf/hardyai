# Security Policy

Do not report credentials in a public issue. Use the repository host's private security-advisory
channel or contact the maintainer privately.

Never commit `.env`, SQLite databases, OAuth client files, access or refresh tokens, Discord IDs,
real email addresses, session logs, household profiles, or files under `secrets/` and `data/`.
Only reserved example values belong in tests and documentation.

Before publishing a branch or tag, run:

```text
python scripts/check_public_tree.py --root .
python -m pytest -q
```

If a credential reaches Git, revoke or rotate it first. Deleting it in a later commit does not make
the credential safe; publish from a clean history after rotation.
