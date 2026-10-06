# Contributing to wattnet-home-assistant

Thank you for your interest in contributing. This document covers how to set up your environment, the code standards we follow, and the process for submitting changes.

## Prerequisites

- Python 3.14 (the version required by current Home Assistant releases)
- Git

## Getting Started

1. Fork the repository and clone your fork:

   ```bash
   git clone https://github.com/<your-username>/wattnet-home-assistant.git
   cd wattnet-home-assistant
   ```

2. Create a virtual environment and install the development dependencies:

   ```bash
   python3.14 -m venv .venv && source .venv/bin/activate
   pip install -r requirements_dev.txt
   ```

3. Install the pre-commit hooks:

   ```bash
   pre-commit install --hook-type pre-commit --hook-type commit-msg
   ```

## Running the Tests

Tests use [pytest-homeassistant-custom-component](https://github.com/MatthewFlamm/pytest-homeassistant-custom-component) and a fake Wattnet client, so no network access or credentials are needed:

```bash
pytest
```

The tests must keep the integration at **100 % line and branch coverage**: `pytest --cov` fails below it, and Codecov checks it on every PR. A new branch of code needs a test, including the error paths.

Or run the whole tox suite (tests, lint, format, type-check and security scan):

```bash
tox
```

CI also runs `hassfest` and the HACS validation action, which check the integration metadata (`manifest.json`, `hacs.json`, translations).

## Trying it in a real Home Assistant

The `dev/` folder holds a ready-made Docker setup, with Home Assistant, the integration mounted from this checkout and the local `wattnet-client` checkout preinstalled (it is not on PyPI yet, so it is taken from `../wattnet-client-python`, uncommitted changes included):

```bash
./dev/up.sh
```

It creates the user `dev` with password `dev` and skips the onboarding wizard, so you can open <http://localhost:8123> right away. Then go to **Settings > Devices & services > Add integration > Wattnet** and enter the e-mail and password of your Wattnet account in the integration's own form.

After editing code under `custom_components/wattnet`, restart the container to apply it:

```bash
docker compose -f dev/docker-compose.yml restart homeassistant
```

Debug logs of the integration and of the client are enabled in `dev/config/configuration.yaml`: `docker compose -f dev/docker-compose.yml logs -f homeassistant`. To inspect the imported statistics, use **Developer tools > Statistics**; ids look like `wattnet:es_carbon_footprint_life_cycle`. Stop everything with `docker compose -f dev/docker-compose.yml down` (the `dev/config` folder keeps your state; delete its contents except `configuration.yaml` for a clean start).

## Code Style

We enforce a consistent style automatically. Before opening a PR, run:

```bash
# Format code
black custom_components tests
isort custom_components tests

# Lint
flake8 custom_components

# Type-check
mypy custom_components

# Security scan
bandit -r custom_components
```

All of these also run via pre-commit on every commit and are verified in CI.

Key rules:
- Line length: 88 characters (Black default).
- Import order: standard library, third-party, first-party (`isort` with Black profile).
- Docstrings are required on all public modules, classes and functions of `custom_components`.
- Commit messages must follow [Conventional Commits](https://www.conventionalcommits.org/) (`feat`, `fix`, `docs`, `style`, `refactor`, `perf`, `test`, `build`, `ci`, `chore`, `revert`).
- User-facing text goes through translations: update both `translations/en.json` and `translations/es.json`.

## Project layout

- `custom_components/wattnet/parsing.py` holds the pure logic that turns API payloads into hourly statistics and computes the watermark. It does not import Home Assistant and is the first place to look when changing how provisional data is consolidated.
- `coordinator.py` fetches data, imports statistics and persists the watermark.
- `config_flow.py`, `sensor.py` and `api.py` hold the Home Assistant glue.

## Releases

Versions and the changelog come from the conventional commits, through [release-please](https://github.com/googleapis/release-please). The bot opens a release PR on `main`, and merging it creates the tag and the GitHub release that HACS installs. The version is also written into `manifest.json` and `pyproject.toml`.

The first release is **1.0.0**. For that, `release-please-config.json` has `"release-as": "1.0.0"` and the manifest starts at `0.0.0`, so the bot does not read the `feat` commits as a bump on top of an already published 1.0.0, which would give 1.1.0. Do not create the `v1.0.0` tag by hand. **After the 1.0.0 release is merged, remove the `release-as` line**, or every later release would be proposed as 1.0.0 again.

## Submitting a Pull Request

1. Create a branch from `main` with a descriptive name:

   ```bash
   git checkout -b feat/my-new-feature
   ```

2. Make your changes, ensuring all tests pass and the linter is clean.

3. Push your branch and open a PR against `main`. Fill in the PR template.

4. A maintainer will review your PR. Please address any requested changes promptly.

## Reporting Issues

Please use the GitHub issue templates for bug reports and feature requests. For security vulnerabilities, see [SECURITY.md](SECURITY.md).

## License

By contributing, you agree that your contributions will be licensed under the [Apache License 2.0](LICENSE).
