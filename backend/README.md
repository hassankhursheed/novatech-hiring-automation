# NovaTech backend (FastAPI)

Pure computation service for the n8n workflows: validation and normalisation, rule-based scoring, advisory AI
analysis, and CV upload/extraction. It never writes business data directly. n8n persists results through the
database `api.*` functions.

```
app/core/          settings, logging, errors (problem+json), DB pool, auth, fault injection, middleware
app/domain/        contracts (Pydantic), normalisation, validation, scoring engine, screening policy
app/ai/            schemas, prompts, PII redaction, provider-agnostic LangChain client, analyzer
app/repositories/  read-only SQL
app/services/      storage (claim check), CV extraction
app/api/routes/    health, intake, screening
tests/             unit, api, integration (DB)
```

Development runs in Docker (see the root README). Dependencies are locked with `uv` (`uv.lock`); to change them,
edit `pyproject.toml` and run `uv lock` (or run it in the `python:3.12` image as the project does).
