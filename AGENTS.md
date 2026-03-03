# AGENTS.md - fanctrl

## Purpose
Core `fanctrl` repository (service code, compose files, and package metadata).

## Release Rules
For every release version `<version>`:
1. Update `pyproject.toml` (`[project].version`).
2. Update `README.md` top line:
   `**Version:** \`<version>\``
3. Ensure example status JSON in README uses the same version.

## Consistency (Required Before Commit)
- `pyproject.toml` version == `README.md` version line.
- No old release strings (`0.3.*`, `0.4.*`, etc.) left in code/docs unless intentional historical reference.

## Branching
- Use release branches as requested by project convention.
- Do not merge to `main` without explicit approval.
