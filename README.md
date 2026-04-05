# Kalshi Trading System

Monorepo scaffold for a Kalshi trading and research platform.

## Services

- `apps/web`: Next.js ops console
- `apps/api`: FastAPI control plane
- `services/worker`: live ingestion and execution runtime
- `services/research`: replay, backtests, experiments
- `packages/core`: shared Python domain logic
- `packages/contracts`: shared TypeScript API contracts

## First milestone

Build one end-to-end slice:

1. Fetch one Kalshi market.
2. Normalize it into a shared schema.
3. Store it in Postgres.
4. Expose it through the API.
5. Render it in the web app.

## Team split

- Partner A: `apps/web`, `apps/api`, `packages/contracts`
- Partner B: `services/worker`, `services/research`, `packages/core`
- Shared: schema decisions, infra, migrations, docs
