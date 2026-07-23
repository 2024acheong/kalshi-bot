BEGIN;

CREATE TABLE IF NOT EXISTS macro_market_outcomes (
    id BIGSERIAL PRIMARY KEY,
    ticker TEXT NOT NULL UNIQUE,
    series TEXT NOT NULL,
    metric_id TEXT NOT NULL,
    fred_series_id TEXT NOT NULL,
    target_date DATE NOT NULL,
    threshold NUMERIC(14,4) NOT NULL,
    actual_value NUMERIC(14,4) NOT NULL,
    yes_resolved BOOLEAN NOT NULL,
    resolved_at TIMESTAMPTZ,
    source TEXT NOT NULL DEFAULT 'kalshi_market_catalog',
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now())
);

CREATE INDEX IF NOT EXISTS idx_macro_market_outcomes_metric_target
    ON macro_market_outcomes (metric_id, target_date DESC);

COMMIT;
