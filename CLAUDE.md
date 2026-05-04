# Project Rules

## Changelog & README — mandatory after every change

After **every** fix, improvement, or feature — no matter how small — you MUST:

1. **Update `CHANGELOG.md`** — add the change under the correct version section. If the change is a bug fix or patch, bump to the next patch version (e.g. 2.1.1 → 2.1.2) and open a new section. If it is a new feature, bump the minor version.
2. **Update `README.md`** — if the change affects anything user-visible (features, configuration, behaviour, troubleshooting), reflect it in the relevant section.
3. **Bump `pyproject.toml` version** to match the new CHANGELOG version.

This applies to every commit, including small fixes. Do not batch changelog updates — write them at the time of the change.
