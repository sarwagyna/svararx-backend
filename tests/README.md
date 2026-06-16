# Backend tests

Integration tests use a **separate PostgreSQL database** (`svararx_test`). Never point tests at production.

## Quick start

```bash
# 1. Start test Postgres (port 5433)
docker compose -f docker-compose.test.yml up -d

# 2. Install deps
pip install -r requirements.txt

# 3. Run tests with coverage
export TEST_DATABASE_URL=postgresql+asyncpg://test:test@localhost:5433/svararx_test
export DATABASE_URL=$TEST_DATABASE_URL
export SECRET_KEY=pytest-secret-key-minimum-32-characters-long
export SARVAM_API_KEY=test
export GROQ_API_KEY=test

pytest tests/ -v --cov=app --cov-fail-under=70
```

On Windows PowerShell:

```powershell
$env:TEST_DATABASE_URL = "postgresql+asyncpg://test:test@localhost:5433/svararx_test"
$env:DATABASE_URL = $env:TEST_DATABASE_URL
python -m pytest tests/ -v --cov=app --cov-fail-under=70
```

## Layout

| File | Purpose |
|------|---------|
| `conftest.py` | DB, client, factories, JWT, AI/Redis mocks |
| `fixtures/transcripts.json` | Five voice pipeline scenarios |
| `test_auth.py` | JWT security (P0) |
| `test_voice_pipeline.py` | STT → structure → PDF (< 35s SLA) |
| `test_patients.py` | CRUD, phone search, duplicate 409 |
| `test_prescriptions.py` | Approve, history ordering, PDF download |

## Notes

- ReportLab A5 prescription PDFs are typically **~4–8 KB** (valid `%PDF` documents). Tests assert a minimum size of 3.5 KB, not 20 KB — 20 KB would require embedded images/fonts beyond the current generator.
- If Postgres is unavailable locally, integration tests **skip**; CI always runs against the GitHub Actions Postgres service.
