# Railway + Vercel Deployment

This project deploys as separate services:

- Vercel: `apps/web`
- Railway: FastAPI control plane
- Railway: always-on worker
- Railway cron: macro daily job
- Railway cron: weather forecast job
- Railway cron: weather daily outcome/train job

## Vercel

Create a Vercel project with root directory `apps/web`.

Set:

- `NEXT_PUBLIC_API_BASE_URL`: public Railway URL for the FastAPI service
- `SUPABASE_URL`
- `SUPABASE_SECRET_KEY`

The web project includes `apps/web/vercel.json` with the Next.js framework, install command, and build command.

## Railway Services

Create one Railway service per config file:

| Service | Config file | Start command | Schedule |
| --- | --- | --- | --- |
| API | `railway/api.toml` | `uvicorn api.main:app --host 0.0.0.0 --port $PORT` | always on |
| Worker | `railway/worker.toml` | `python -m worker.main` | always on |
| Macro Daily | `railway/macro-daily.toml` | `python scripts/jobs/macro_daily.py` | `30 12 * * 1-5` |
| Weather Forecasts | `railway/weather-forecasts.toml` | `python scripts/jobs/weather_forecasts.py` | `15 */6 * * *` |
| Weather Daily | `railway/weather-daily.toml` | `python scripts/jobs/weather_daily.py` | `45 10 * * *` |

Railway cron schedules are UTC. Cron services should finish and exit; the worker is the only long-running market stream.

For each Railway service, point config-as-code at the matching file path, for example `/railway/worker.toml`. In a monorepo, Railway's root directory and config-file path are separate settings.

## Required Variables

Set these on Railway services that need them:

- `SUPABASE_URL`
- `SUPABASE_SECRET_KEY` or `SUPABASE_SERVICE_KEY`
- `KALSHI_API_KEY`
- `KALSHI_PRIVATE_KEY_FILE` or equivalent mounted secret path
- `KALSHI_USE_DEMO=true`
- `KALSHI_LIVE_ENABLED=false`
- `FRED_API_KEY` for macro cron
- `JWT_SECRET`
- `OPERATOR_PASSWORD`
- `CORS_ALLOWED_ORIGINS=https://your-vercel-app.vercel.app`
- `WEATHER_OUTCOME_CITIES=NYC`

Keep live trading disabled until paper performance and kill-switch/risk checks are proven.

## CI/CD

GitHub Actions runs:

- Python editable installs and full `pytest`
- `pnpm install --frozen-lockfile`
- `pnpm --dir apps/web build`

Use branch protection so Railway/Vercel deploy from `dev` only after CI passes.
