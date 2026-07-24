BEGIN;

CREATE TABLE IF NOT EXISTS weather_ensemble_snapshots (
    id BIGSERIAL PRIMARY KEY,
    location_lat NUMERIC(6,3) NOT NULL,
    location_lon NUMERIC(6,3) NOT NULL,
    forecast_issued_at TIMESTAMPTZ NOT NULL,
    target_datetime TIMESTAMPTZ NOT NULL,
    ensemble_member INTEGER NOT NULL,
    temperature_c NUMERIC(5,2),
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now())
);

CREATE INDEX IF NOT EXISTS idx_weather_ensemble_location_target
    ON weather_ensemble_snapshots (location_lat, location_lon, target_datetime);

COMMIT;
