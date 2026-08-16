# Contributing

Thank you for improving Reading Pack. The repository is bilingual and offline-first; code, tests, specifications, and public documentation should remain consistent across those boundaries.

## Development setup

```sh
python3 -m venv .venv
.venv/bin/python -m pip install --no-deps --no-build-isolation -e .
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m unittest discover -s tests -v
```

Runtime code uses the Python standard library plus the bounded `jsonschema` dependency that enforces the published Draft 2020-12 schemas. Propose any further dependency only with a documented offline, security, packaging, and maintenance rationale.

## Change requirements

- Add tests for behavior changes and failure cases.
- Keep Japanese and English requirement IDs and section structures aligned within every standards-suite and reference-profile document.
- Use synthetic fixtures. Never contribute an unpublished manuscript, book-specific private evaluation answer, credential, local absolute path, or copyrighted book passage.
- Importers must extract structure only and document archive, encoding, entity, path, and size limits.
- Keep generated files deterministic; do not use current time, random values, locale-dependent ordering, or network results during build.
- Never automate author approval, rights approval, non-reconstruction judgment, or publication decision.
- Update both READMEs and both workflow/quickstart documents when public behavior changes.
- Write public documentation for a person encountering the project for the first time. Lead with the governing idea, define necessary technical terms on first use, and keep command or JSON names in code formatting.

## Tests before a change

```sh
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m compileall -q src tests
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m unittest discover -s tests -v
PYTHONDONTWRITEBYTECODE=1 PYTHONPATH=src python3 -m reading_pack check --project examples/clockwork-garden --lang all --release
git diff --check
```

Do not include confidential red-team wording in a public issue or pull request. Follow `SECURITY.md` for vulnerabilities.

Documentation contributions are CC BY 4.0; code and tests are MIT. By contributing, you agree that your contribution may be distributed under the license mapped to its path.
