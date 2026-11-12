# ScriptaLocal offline semantic validator handover

For Registers (integration coordination) and Builder (future application implementation). This package contains contract source/documentation only, not an OCR server, inference model or production adapter. Review `docs/registers-contract-clarification.md` first. It answers the seven contract questions and identifies three fixture warning-scope fixes.

Use a disposable Python 3.11 environment. Dependencies must be provisioned once; subsequent validation requires no network, model files, database or service credentials:

```bash
python3.11 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m scripts.validate_contract --model JobResult /path/to/result.json
```

The requirements use an included transitive constraint snapshot. The validated runtime is Pydantic 2.11.4 and Pillow 11.2.1. Do not alter the existing application environment merely to run this reference tool. The command exits 0 on validity and 1 on invalidity, reports semantic error locations, and never echoes the supplied transcription body. Its default 2 MiB input cap is a configurable local tool limit, not an implemented service response guarantee.

Read `docs/api/semantic-validation.md` for geometry, ordering, state/null and warning rules. `docs/api/openapi.json` remains the structural wire contract. Neither structural nor semantic validation proves actual column assignment, handwriting truth, authorization, cancellation/revision fencing or durable application publication.

`docs/api/fixture-warning-patch.json` contains three proposed JSON Patch operations for the original Registers fixtures. Their original archive is not modified by this package. With a local original ZIP, validate it using:

```bash
.venv/bin/python -m scripts.validation.app_fixture_probe --archive /path/to/integration-fixtures.zip
```

The original ZIP is expected to exit 1: 18/21 fixtures pass service semantics. To evaluate the proposed warning fixes in memory:

```bash
.venv/bin/python -m scripts.validation.app_fixture_probe \
  --archive /path/to/integration-fixtures.zip \
  --warning-patch docs/api/fixture-warning-patch.json
```

That run should validate 21/21 fixtures and reject all four deliberately invalid semantic examples. No original fixture files are rewritten. Use `python -m unittest discover -s tests -p test_contract_cli.py -v` for CLI acceptance checks. The package manifest records byte counts/SHA-256 of every included file except the manifest itself. File transfer uses separate expiring scoped tickets; never substitute an intake URL/token for a future OCR origin/credential.
