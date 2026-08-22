# Doctors Atlas — Backend

FastAPI backend: auth (admin/doctor/staff roles), daily-log data entry,
stats analysis, and a Gemini-powered AI Advisor.

## Local setup

```bash
pip install -r requirements.txt
cp .env.example .env   # then fill in real values
uvicorn app.main:app --reload
```

Visit http://localhost:8000/health to confirm it's running, and
http://localhost:8000/docs for interactive API docs.

## Environment variables

See `.env.example`. You'll need:

- `DATABASE_URL` — your Postgres connection string (e.g. from Neon.tech)
- `JWT_SECRET` — a long random string for signing login tokens
- `GEMINI_API_KEY` — from https://aistudio.google.com/apikey
- `CORS_ORIGINS` — comma-separated list of frontend URLs allowed to call this API

## Deploy on Render

This repo includes `render.yaml`. On render.com: New → Blueprint →
connect this repo → it reads `render.yaml` automatically. Fill in the
four environment variables above when prompted (they're marked
`sync: false` so Render asks for them rather than guessing).

## Database schema

Run `sql/schema.sql` once against a fresh Postgres database before
first use.
