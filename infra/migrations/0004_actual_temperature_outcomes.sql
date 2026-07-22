BEGIN;

CREATE TABLE IF NOT EXISTS actual_temperature_outcomes (
    id BIGSERIAL PRIMARY KEY,
    city_code TEXT NOT NULL,
    station_id TEXT NOT NULL,
    outcome_date DATE NOT NULL,
    high_temp_f NUMERIC(5,2),
    low_temp_f NUMERIC(5,2),
    source TEXT NOT NULL DEFAULT 'nws_cli',
    source_product_id TEXT,
    raw_text TEXT,
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now()),
    UNIQUE (city_code, outcome_date)
);

CREATE INDEX IF NOT EXISTS idx_actual_temperature_outcomes_city_date
    ON actual_temperature_outcomes (city_code, outcome_date);

COMMIT;
