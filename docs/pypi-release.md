# PyPI Release Guide

## Goal

This repository now contains an installable package named `autoresearch-agent`.

The release flow is designed around:

- local verification
- GitHub Actions package checks
- TestPyPI dry runs
- PyPI trusted publishing

## What Is Already Wired

- package metadata in `pyproject.toml`
- package data inclusion for built-in packs and templates
- `MANIFEST.in` for `sdist`
- CI workflow:
  - `.github/workflows/package-check.yml`
- publish workflow:
  - `.github/workflows/publish-pypi.yml`

## One-Time Setup

### 1. Create the package on PyPI and TestPyPI

Create:

- `autoresearch-agent` on [PyPI](https://pypi.org/)
- `autoresearch-agent` on [TestPyPI](https://test.pypi.org/)

### 2. Configure Trusted Publishing

In both PyPI and TestPyPI, add a trusted publisher that matches:

- Owner: `DESONGs`
- Repository: `loomiai-autoresearch`
- Workflow name: `publish-pypi`

Recommended environments:

- `testpypi`
- `pypi`

### 3. Protect the publish environments

In GitHub repository settings, add environment protection rules:

- `testpypi`
- `pypi`

Recommended:

- require manual approval for `pypi`
- optional manual approval for `testpypi`

## Local Verification Before Release

Install build tooling:

```bash
python3 -m pip install --upgrade build twine
```

Build artifacts:

```bash
python3 -m build
```

Check package metadata:

```bash
python3 -m twine check dist/*
```

Run tests:

```bash
python3 -m unittest discover tests
```

## Release Paths

### Path A: TestPyPI dry run

Use the GitHub Actions workflow:

- Workflow: `publish-pypi`
- Trigger: `workflow_dispatch`
- Branch: `main`
- Input: `repository = testpypi`

Then verify install from TestPyPI:

```bash
python3 -m pip install --index-url https://test.pypi.org/simple/ autoresearch-agent
```

### Path B: Production PyPI release

1. Bump version in `pyproject.toml`
2. Update docs or changelog if needed
3. Merge to the target branch
4. Create and push a tag:

```bash
git tag v0.1.0
git push origin v0.1.0
```

The workflow will:

- run tests
- build `sdist` and `wheel`
- run `twine check`
- publish to PyPI through trusted publishing

You can also manually dispatch the workflow from `main` with:

- `repository = pypi`

## Recommended Release Checklist

- version bumped in `pyproject.toml`
- tests pass locally
- `python3 -m build` succeeds
- `python3 -m twine check dist/*` succeeds
- README reflects current install and usage flow
- pack templates are included in the built wheel
- TestPyPI publish succeeds before first public PyPI publish

## Notes

- The current package includes built-in pack resources through package data.
- The current release flow assumes GitHub Actions is the publishing source of truth.
- If you plan to publish publicly, decide on the repository license before broad distribution.
