BEGIN;

CREATE TABLE IF NOT EXISTS macro_indicator_series (
    id BIGSERIAL PRIMARY KEY,
    series_id TEXT NOT NULL,
    observation_date DATE NOT NULL,
    value NUMERIC(14,4),
    fetched_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now()),
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now()),
    UNIQUE (series_id, observation_date)
);

CREATE INDEX IF NOT EXISTS idx_macro_series_id_date
    ON macro_indicator_series (series_id, observation_date DESC);

COMMIT;
