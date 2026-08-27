# Project Rules

## Changelog & README — mandatory after every change

After **every** fix, improvement, or feature — no matter how small — you MUST:

1. **Update `CHANGELOG.md`** — add the change under the correct version section. If the change is a bug fix or patch, bump to the next patch version (e.g. 2.1.1 → 2.1.2) and open a new section. If it is a new feature, bump the minor version.
2. **Update `README.md`** — if the change affects anything user-visible (features, configuration, behaviour, troubleshooting), reflect it in the relevant section.
3. **Bump `pyproject.toml` version** to match the new CHANGELOG version.
4. **Keep `requirements.txt` (and `requirements-dev.txt`) aligned** — if the change adds, removes, or version-bumps a dependency, update the requirements files and confirm they match the dependencies declared in `pyproject.toml`.

Before finishing any change, verify `README.md`, `CHANGELOG.md`, `pyproject.toml`, and `requirements.txt` are all mutually consistent — no drift between what the code needs and what the docs/manifests declare. This applies to every commit, including small fixes. Do not batch changelog updates — write them at the time of the change.
