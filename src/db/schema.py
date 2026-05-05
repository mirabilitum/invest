import sqlite3


def create_tables(conn: sqlite3.Connection) -> None:
    conn.executescript("""
        CREATE TABLE IF NOT EXISTS data_source_log (
            id          INTEGER PRIMARY KEY AUTOINCREMENT,
            source      TEXT    NOT NULL,
            status      TEXT    NOT NULL,
            error_message TEXT,
            checked_at  TEXT    NOT NULL
        );

        CREATE TABLE IF NOT EXISTS etf_pool (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            code                TEXT    UNIQUE NOT NULL,
            name                TEXT,
            market              TEXT,
            category            TEXT,
            aum                 REAL,
            fee                 REAL,
            position_ratio      REAL,
            fx_method           TEXT,
            subscription_status TEXT,
            screened_passed     INTEGER,
            last_verified       TEXT
        );

        CREATE TABLE IF NOT EXISTS classification (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            etf_code            TEXT,
            industry            TEXT,
            market              TEXT,
            category_type       TEXT,
            revenue_vol         REAL,
            gdp_corr            REAL,
            moat_depth          REAL,
            moat_durability     REAL,
            lifecycle           TEXT,
            rule_version        TEXT DEFAULT 'v1.4',
            rated_at            TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS scorecard (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            etf_code            TEXT,
            industry            TEXT,
            category_type       TEXT,
            signal_pe           REAL,
            signal_core         REAL,
            signal_sentiment    REAL,
            total_score         REAL,
            action              TEXT,
            a_share_gated       INTEGER,
            human_override      INTEGER DEFAULT 0,
            rule_version        TEXT DEFAULT 'v1.4',
            rated_at            TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS position (
            id                      INTEGER PRIMARY KEY AUTOINCREMENT,
            etf_code                TEXT,
            market                  TEXT,
            industry                TEXT,
            shares                  REAL,
            cost_basis              REAL,
            current_value           REAL,
            unrealized_pnl_pct      REAL,
            category_type           TEXT,
            partial_close_status    TEXT DEFAULT NULL,
            holding_months          INTEGER DEFAULT 0,
            updated_at              TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS trade_log (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            etf_code        TEXT,
            action          TEXT,
            shares          REAL,
            price           REAL,
            realized_pnl    REAL,
            reason          TEXT,
            rule_version    TEXT,
            human_override  INTEGER DEFAULT 0,
            executed_at     TEXT NOT NULL
        );

        CREATE TABLE IF NOT EXISTS alert_log (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            alert_type      TEXT    NOT NULL,
            severity        TEXT    NOT NULL,
            message         TEXT,
            acknowledged    INTEGER DEFAULT 0,
            created_at      TEXT    NOT NULL
        );

        CREATE TABLE IF NOT EXISTS report (
            id                  INTEGER PRIMARY KEY AUTOINCREMENT,
            report_type         TEXT    NOT NULL,
            content             TEXT,
            realized_pnl        REAL    DEFAULT 0,
            unrealized_pnl      REAL    DEFAULT 0,
            cumulative_realized REAL    DEFAULT 0,
            raw_data_snapshot   TEXT,
            fact_check_passed   INTEGER DEFAULT 0,
            generated_at        TEXT    NOT NULL
        );

        CREATE TABLE IF NOT EXISTS build_up_state (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            round_id        INTEGER NOT NULL DEFAULT 1,
            status          TEXT    NOT NULL DEFAULT 'waiting',
            total_capital   REAL    NOT NULL DEFAULT 1000000,
            current_batch   INTEGER NOT NULL DEFAULT 0,
            total_batches   INTEGER NOT NULL DEFAULT 4,
            batch_sizes     TEXT    NOT NULL DEFAULT '[0.30,0.30,0.25,0.15]',
            market_weights  TEXT,
            market_deployed TEXT,
            last_deploy_date TEXT,
            phase           INTEGER NOT NULL DEFAULT 1,
            phase_amount    REAL    NOT NULL,
            filled_amount   REAL    NOT NULL DEFAULT 0,
            started_at      TEXT    NOT NULL,
            paused_at       TEXT,
            completed_at    TEXT
        );

        CREATE TABLE IF NOT EXISTS pe_history (
            id              INTEGER PRIMARY KEY AUTOINCREMENT,
            index_code      TEXT    NOT NULL,
            pe_value        REAL    NOT NULL,
            recorded_at     TEXT    NOT NULL
        );
    """)
