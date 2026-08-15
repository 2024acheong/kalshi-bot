# Kalshi Trading System

A monorepo for researching, paper-trading, and operating automated Kalshi prediction-market strategies. The system ingests live Kalshi market data, normalizes it into shared schemas, evaluates strategy/risk logic, simulates execution in paper mode, persists orders/fills/positions to Supabase Postgres, and exposes an operator dashboard for monitoring performance.

## What It Does

- Discovers liquid, open Kalshi markets and streams/polls live market snapshots.
- Runs enabled strategy configurations as independent paper-trading runtimes.
- Applies shared risk gates for edge, liquidity, concentration, correlation, drawdown, and kill-switch state.
- Simulates paper execution, including marketable orders, resting limit orders, partial fills, fees, cash reservation, and paper account ledger entries.
- Stores market data, signals, orders, fills, positions, strategy runs, model artifacts, and system events in Postgres.
- Provides a web console for markets, strategies, models, orders, fills, and performance.
- Includes research/backtest tooling and scheduled macro/weather model jobs.

## Architecture

| Area | Path | Purpose |
| --- | --- | --- |
| Web app | `apps/web` | Next.js operator dashboard backed by Supabase queries |
| API | `apps/api` | FastAPI control plane for health, auth, and strategy commands |
| Worker | `services/worker` | Live ingestion, strategy orchestration, risk checks, and paper execution |
| Research | `services/research` | Replay, backtesting, experiments, and metrics |
| Models | `services/models` | Macro/weather feature pipelines, training, outcomes, and artifact registry |
| Core | `packages/core` | Shared schemas, feature computation, strategies, risk engine, and execution adapters |
| Contracts | `packages/contracts` | Shared TypeScript contracts for app/API boundaries |
| Infra | `infra/migrations` | Supabase/Postgres schema and performance indexes |
| Deployment | `railway`, `apps/web/vercel.json` | Railway service configs and Vercel web deployment config |

## Trading Flow

1. `services/worker/worker/main.py` loads enabled strategy configs from Supabase, or falls back to `spread_capture`.
2. The worker discovers live tickers per strategy and starts a `TradingRuntime` for each config.
3. Market updates are normalized into `MarketState` and dispatched through `MultiStrategyOrchestrator`.
4. Strategies emit `OrderIntent` or paired spread-capture intents.
5. `RiskEngine` evaluates each intent against configured risk gates.
6. Approved orders are persisted, simulated through `PaperAdapter` or the resting order book, and recorded as fills when matched.
7. Paper account balances, reserved cash, positions, ledger entries, and performance views update from persisted state.

## Strategies

Implemented strategy modules include:

- `spread_capture`: buys YES/NO pairs only when locked settlement value remains positive after fees.
- `mean_reversion`: enters against overextended short-term price momentum and exits on reversion, stop, or max hold time.
- `event_drift`: trades directional event drift signals with position lifecycle handling.
- `calibration_mispricing_macro` and `calibration_mispricing_weather`: compare model probability estimates to market prices.

Strategy behavior is configured through `strategy_configs.params_json`; risk parameters can be overridden per config.

## Dashboard

The Next.js web console includes pages for:

- `/markets`: latest market catalog and quote state
- `/strategies`: enabled strategy configs and controls
- `/models`: model/artifact visibility
- `/orders`: order decisions, status, and metadata
- `/fills`: simulated/live fill history
- `/performance`: paper PnL, fees, fill rate, buying power, drawdown, and strategy curves

## Local Development

Install dependencies:

```bash
pnpm install
pip install -r requirements.txt
pip install -e packages/core -e services/worker -e services/research -e services/models -e apps/api
```

Run services individually:

```bash
make web      # Next.js dashboard
make api      # FastAPI control plane
make worker   # live ingestion and paper-trading worker
make research # research CLI
```

Useful validation commands:

```bash
pytest
pnpm --filter @kalshi/web exec tsc --noEmit
pnpm build:web
```

## Environment

Common variables:

- `SUPABASE_URL`
- `SUPABASE_SECRET_KEY` or `SUPABASE_SERVICE_KEY`
- `DATABASE_URL`
- `KALSHI_BASE_URL`
- `KALSHI_API_KEY_ID`
- `KALSHI_PRIVATE_KEY` or `KALSHI_PRIVATE_KEY_PATH`
- `UPSTASH_REDIS_URL` or `REDIS_URL`
- `JWT_SECRET`
- `OPERATOR_PASSWORD`
- `CORS_ALLOWED_ORIGINS`
- `FRED_API_KEY`

Keep live trading disabled until paper performance, risk limits, and kill-switch behavior are proven.

## Database

Schema changes live in `infra/migrations`. Apply new migrations before deploying code that depends on them. Recent performance indexes support dashboard queries over high-volume order, fill, signal, position, account, and snapshot tables.

## Deployment

Deployment is split across Vercel and Railway:

- Vercel serves `apps/web`.
- Railway runs the FastAPI control plane.
- Railway runs the always-on worker.
- Railway cron jobs run macro/weather ingestion, outcomes, and training.

See `docs/deployment.md` for service config files, schedules, required variables, and CI/CD notes.

## Safety Notes

The current execution path is designed around paper trading. Before enabling live trading, verify authentication, order sizing, liquidity assumptions, kill switches, account reconciliation, alerting, and failure handling end to end.
