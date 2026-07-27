BEGIN;

CREATE TABLE IF NOT EXISTS paper_accounts (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    config_id UUID NOT NULL REFERENCES strategy_configs(id) ON DELETE CASCADE,
    name TEXT NOT NULL DEFAULT 'default',
    currency TEXT NOT NULL DEFAULT 'USD',
    starting_cash NUMERIC(14, 4) NOT NULL DEFAULT 10000,
    cash_balance NUMERIC(14, 4) NOT NULL DEFAULT 10000,
    reserved_cash NUMERIC(14, 4) NOT NULL DEFAULT 0,
    status TEXT NOT NULL DEFAULT 'active',
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now()),
    updated_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now()),
    UNIQUE (config_id, name),
    CHECK (status IN ('active', 'disabled')),
    CHECK (cash_balance >= 0),
    CHECK (reserved_cash >= 0)
);

CREATE TABLE IF NOT EXISTS paper_ledger_entries (
    id UUID PRIMARY KEY DEFAULT gen_random_uuid(),
    account_id UUID NOT NULL REFERENCES paper_accounts(id) ON DELETE CASCADE,
    run_id UUID REFERENCES strategy_runs(id) ON DELETE SET NULL,
    order_id UUID REFERENCES orders(id) ON DELETE SET NULL,
    fill_id UUID REFERENCES fills(id) ON DELETE SET NULL,
    ticker TEXT REFERENCES market_catalog(ticker) ON DELETE SET NULL,
    side TEXT,
    entry_type TEXT NOT NULL,
    amount NUMERIC(14, 4) NOT NULL,
    cash_balance_after NUMERIC(14, 4) NOT NULL,
    reserved_cash_after NUMERIC(14, 4) NOT NULL,
    metadata_json JSONB NOT NULL DEFAULT '{}'::jsonb,
    created_at TIMESTAMPTZ NOT NULL DEFAULT timezone('utc', now()),
    CHECK (
        entry_type IN (
            'initial_deposit',
            'reserve',
            'release_reserve',
            'fill_debit',
            'fill_fee',
            'realized_credit',
            'adjustment'
        )
    )
);

CREATE INDEX IF NOT EXISTS idx_paper_accounts_config
    ON paper_accounts(config_id);

CREATE INDEX IF NOT EXISTS idx_paper_ledger_account_created
    ON paper_ledger_entries(account_id, created_at DESC);

CREATE INDEX IF NOT EXISTS idx_paper_ledger_run
    ON paper_ledger_entries(run_id);

CREATE INDEX IF NOT EXISTS idx_paper_ledger_order
    ON paper_ledger_entries(order_id);

COMMIT;
