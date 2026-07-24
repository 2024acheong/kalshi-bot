BEGIN;

CREATE TABLE IF NOT EXISTS weather_market_outcomes (
    id BIGSERIAL PRIMARY KEY,
    ticker TEXT NOT NULL UNIQUE,
    series TEXT NOT NULL,
    kind TEXT NOT NULL,
    city_code TEXT NOT NULL,
    target_date DATE NOT NULL,
    strike_type TEXT NOT NULL,
    threshold_f NUMERIC(8,2) NOT NULL,
    lower_f NUMERIC(8,2),
    upper_f NUMERIC(8,2),
    actual_value_f NUMERIC(8,2) NOT NULL,
    yes_resolved BOOLEAN NOT NULL,
    resolved_at TIMESTAMPTZ,
    source TEXT NOT NULL DEFAULT 'kalshi_market_catalog_actual_temperature_outcomes',
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now())
);

CREATE INDEX IF NOT EXISTS idx_weather_market_outcomes_city_target
    ON weather_market_outcomes (city_code, kind, target_date DESC);

CREATE INDEX IF NOT EXISTS idx_weather_market_outcomes_strike_type
    ON weather_market_outcomes (strike_type);

COMMIT;
