BEGIN;

CREATE EXTENSION IF NOT EXISTS pgcrypto;

CREATE TABLE IF NOT EXISTS users (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    email TEXT NOT NULL UNIQUE,
    role TEXT NOT NULL DEFAULT 'operator',
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now()),
    CHECK (role IN ('operator', 'admin'))
);

CREATE TABLE IF NOT EXISTS strategy_configs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    version INTEGER NOT NULL,
    params_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    status TEXT NOT NULL DEFAULT 'disabled',
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now()),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now()),
    UNIQUE (name, version),
    CHECK (status IN ('enabled', 'disabled', 'paused'))
);

CREATE TABLE IF NOT EXISTS strategy_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    config_id UUID NOT NULL REFERENCES strategy_configs(id) ON DELETE RESTRICT,
    mode TEXT NOT NULL,
    started_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now()),
    ended_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now()),
    CHECK (mode IN ('replay', 'paper', 'live'))
);

CREATE TABLE IF NOT EXISTS market_catalog (
    ticker TEXT PRIMARY KEY,
    title TEXT NOT NULL,
    category TEXT,
    close_time TIMESTAMPTZ,
    status TEXT NOT NULL,
    synced_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now()),
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now()),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now())
);

ALTER TABLE market_catalog
    ADD COLUMN IF NOT EXISTS created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now()),
    ADD COLUMN IF NOT EXISTS updated_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now());

CREATE TABLE IF NOT EXISTS market_snapshots (
    id BIGSERIAL PRIMARY KEY,
    ticker TEXT NOT NULL REFERENCES market_catalog(ticker) ON DELETE CASCADE,
    timestamp TIMESTAMPTZ NOT NULL,
    yes_bid NUMERIC(10, 4),
    yes_ask NUMERIC(10, 4),
    yes_bid_size INTEGER,
    yes_ask_size INTEGER,
    last_price NUMERIC(10, 4),
    volume_24h INTEGER,
    open_interest INTEGER,
    source TEXT NOT NULL,
    raw_sequence BIGINT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now())
);

ALTER TABLE market_snapshots
    ADD COLUMN IF NOT EXISTS raw_sequence BIGINT;

CREATE TABLE IF NOT EXISTS feature_snapshots (
    id BIGSERIAL PRIMARY KEY,
    ticker TEXT NOT NULL REFERENCES market_catalog(ticker) ON DELETE CASCADE,
    timestamp TIMESTAMPTZ NOT NULL,
    run_id UUID REFERENCES strategy_runs(id) ON DELETE SET NULL,
    features_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now())
);

CREATE TABLE IF NOT EXISTS model_registry (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    name TEXT NOT NULL,
    version TEXT NOT NULL,
    artifact_path TEXT NOT NULL,
    trained_at TIMESTAMPTZ NOT NULL,
    train_metrics_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now()),
    UNIQUE (name, version)
);

CREATE TABLE IF NOT EXISTS signals (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id UUID NOT NULL REFERENCES strategy_runs(id) ON DELETE CASCADE,
    ticker TEXT NOT NULL REFERENCES market_catalog(ticker) ON DELETE CASCADE,
    timestamp TIMESTAMPTZ NOT NULL,
    model_id UUID REFERENCES model_registry(id) ON DELETE SET NULL,
    prob_estimate NUMERIC(10, 6),
    edge NUMERIC(10, 6),
    signal_payload JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now())
);

CREATE TABLE IF NOT EXISTS orders (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id UUID NOT NULL REFERENCES strategy_runs(id) ON DELETE CASCADE,
    signal_id UUID REFERENCES signals(id) ON DELETE SET NULL,
    ticker TEXT NOT NULL REFERENCES market_catalog(ticker) ON DELETE CASCADE,
    intent TEXT NOT NULL,
    side TEXT,
    price NUMERIC(10, 4),
    qty INTEGER,
    risk_decision TEXT NOT NULL DEFAULT 'block',
    status TEXT NOT NULL DEFAULT 'proposed',
    submitted_at TIMESTAMPTZ,
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now()),
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    CHECK (risk_decision IN ('allow', 'block', 'reduce_only')),
    CHECK (status IN ('proposed', 'approved', 'rejected', 'submitted', 'filled', 'partially_filled', 'cancelled'))
);

CREATE TABLE IF NOT EXISTS fills (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id UUID NOT NULL REFERENCES orders(id) ON DELETE CASCADE,
    fill_price NUMERIC(10, 4) NOT NULL,
    fill_qty INTEGER NOT NULL,
    fee NUMERIC(10, 4) NOT NULL DEFAULT 0,
    fill_latency_ms INTEGER,
    fill_type TEXT NOT NULL,
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now()),
    CHECK (fill_type IN ('paper', 'live'))
);

CREATE TABLE IF NOT EXISTS positions (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    run_id UUID NOT NULL REFERENCES strategy_runs(id) ON DELETE CASCADE,
    ticker TEXT NOT NULL REFERENCES market_catalog(ticker) ON DELETE CASCADE,
    side TEXT NOT NULL,
    qty INTEGER NOT NULL DEFAULT 0,
    avg_entry NUMERIC(10, 4),
    unrealized_pnl NUMERIC(12, 4) NOT NULL DEFAULT 0,
    opened_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now()),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now()),
    UNIQUE (run_id, ticker, side)
);

CREATE TABLE IF NOT EXISTS risk_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    order_id UUID REFERENCES orders(id) ON DELETE CASCADE,
    gate TEXT NOT NULL,
    decision TEXT NOT NULL,
    reason TEXT,
    payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    checked_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now()),
    CHECK (decision IN ('allow', 'block', 'reduce_only'))
);

CREATE TABLE IF NOT EXISTS backtest_runs (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    config_id UUID NOT NULL REFERENCES strategy_configs(id) ON DELETE RESTRICT,
    date_from DATE NOT NULL,
    date_to DATE NOT NULL,
    market_universe JSONB NOT NULL DEFAULT '[]'::jsonb,
    run_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now()),
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb
);

CREATE TABLE IF NOT EXISTS backtest_metrics (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    backtest_id UUID NOT NULL REFERENCES backtest_runs(id) ON DELETE CASCADE,
    total_pnl NUMERIC(12, 4),
    sharpe NUMERIC(10, 4),
    brier_score NUMERIC(10, 6),
    hit_rate NUMERIC(10, 6),
    max_drawdown NUMERIC(10, 6),
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now()),
    UNIQUE (backtest_id)
);

CREATE TABLE IF NOT EXISTS system_events (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    event_type TEXT NOT NULL,
    payload_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now())
);

ALTER TABLE system_events
    ADD COLUMN IF NOT EXISTS payload_json JSONB;

UPDATE system_events
SET payload_json = COALESCE(payload_json, payload, '{}'::jsonb)
WHERE payload_json IS NULL;

ALTER TABLE system_events
    ALTER COLUMN payload_json SET DEFAULT '{}'::jsonb;

ALTER TABLE system_events
    ALTER COLUMN payload_json SET NOT NULL;

CREATE INDEX IF NOT EXISTS idx_strategy_configs_status
    ON strategy_configs (status);

CREATE INDEX IF NOT EXISTS idx_strategy_runs_config_id_started_at
    ON strategy_runs (config_id, started_at DESC);

CREATE INDEX IF NOT EXISTS idx_market_catalog_status
    ON market_catalog (status);

CREATE INDEX IF NOT EXISTS idx_market_catalog_synced_at_desc
    ON market_catalog (synced_at DESC);

CREATE INDEX IF NOT EXISTS idx_market_snapshots_ticker_timestamp_desc
    ON market_snapshots (ticker, timestamp DESC);

CREATE UNIQUE INDEX IF NOT EXISTS idx_market_snapshots_dedup
    ON market_snapshots (ticker, timestamp, source, COALESCE(raw_sequence, -1));

CREATE INDEX IF NOT EXISTS idx_feature_snapshots_ticker_timestamp_desc
    ON feature_snapshots (ticker, timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_feature_snapshots_run_id
    ON feature_snapshots (run_id);

CREATE INDEX IF NOT EXISTS idx_signals_run_id_timestamp_desc
    ON signals (run_id, timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_signals_ticker_timestamp_desc
    ON signals (ticker, timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_orders_run_id_created_at_desc
    ON orders (run_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_orders_signal_id
    ON orders (signal_id);

CREATE INDEX IF NOT EXISTS idx_orders_ticker_status
    ON orders (ticker, status);

CREATE INDEX IF NOT EXISTS idx_fills_order_id
    ON fills (order_id);

CREATE INDEX IF NOT EXISTS idx_positions_run_id_ticker
    ON positions (run_id, ticker);

CREATE INDEX IF NOT EXISTS idx_risk_events_order_id_checked_at_desc
    ON risk_events (order_id, checked_at DESC);

CREATE INDEX IF NOT EXISTS idx_backtest_runs_config_id_run_at_desc
    ON backtest_runs (config_id, run_at DESC);

CREATE INDEX IF NOT EXISTS idx_system_events_event_type_created_at_desc
    ON system_events (event_type, created_at DESC);

COMMIT;
