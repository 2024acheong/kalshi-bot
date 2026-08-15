BEGIN;

CREATE INDEX IF NOT EXISTS idx_fills_created_at_desc
    ON fills (created_at DESC);

CREATE INDEX IF NOT EXISTS idx_signals_created_at_desc
    ON signals (created_at DESC);

CREATE INDEX IF NOT EXISTS idx_market_snapshots_timestamp_desc
    ON market_snapshots (timestamp DESC);

CREATE INDEX IF NOT EXISTS idx_positions_nonzero_qty
    ON positions (run_id, ticker, side)
    WHERE qty <> 0;

CREATE INDEX IF NOT EXISTS idx_paper_accounts_active
    ON paper_accounts (status, config_id)
    WHERE status = 'active';

COMMIT;
