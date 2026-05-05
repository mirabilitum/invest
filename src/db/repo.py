from datetime import datetime
import sqlite3


class Repo:
    def __init__(self, conn: sqlite3.Connection) -> None:
        self.conn = conn

    def log_data_source(
        self, source: str, status: str, error_message: str | None = None
    ) -> None:
        self.conn.execute(
            "INSERT INTO data_source_log (source, status, error_message, checked_at) "
            "VALUES (?, ?, ?, ?)",
            (source, status, error_message, datetime.now().isoformat()),
        )
        self.conn.commit()

    def log_alert(self, alert_type: str, severity: str, message: str) -> None:
        self.conn.execute(
            "INSERT INTO alert_log (alert_type, severity, message, created_at) "
            "VALUES (?, ?, ?, ?)",
            (alert_type, severity, message, datetime.now().isoformat()),
        )
        self.conn.commit()

    def get_positions(self) -> list[dict]:
        self.conn.row_factory = sqlite3.Row
        rows = self.conn.execute("SELECT * FROM position").fetchall()
        return [dict(r) for r in rows]

    def get_avoid_zone_etfs(self) -> list[dict]:
        self.conn.row_factory = sqlite3.Row
        rows = self.conn.execute(
            "SELECT p.*, c.category_type AS classification_type, c.industry AS classification_industry "
            "FROM position p "
            "JOIN classification c ON p.etf_code = c.etf_code "
            "WHERE c.category_type = 'avoid'"
        ).fetchall()
        return [dict(r) for r in rows]

    def upsert_etf_pool(self, etf: dict) -> None:
        self.conn.execute(
            """
            INSERT OR REPLACE INTO etf_pool
                (code, name, market, category, aum, fee, position_ratio,
                 fx_method, subscription_status, screened_passed, last_verified)
            VALUES
                (:code, :name, :market, :category, :aum, :fee, :position_ratio,
                 :fx_method, :subscription_status, :screened_passed, :last_verified)
            """,
            etf,
        )
        self.conn.commit()

    def insert_classification(self, data: dict) -> None:
        data["rated_at"] = data.get("rated_at", datetime.now().isoformat())
        self.conn.execute(
            """
            INSERT INTO classification
                (etf_code, industry, market, category_type, revenue_vol, gdp_corr,
                 moat_depth, moat_durability, lifecycle, rule_version, rated_at)
            VALUES
                (:etf_code, :industry, :market, :category_type, :revenue_vol, :gdp_corr,
                 :moat_depth, :moat_durability, :lifecycle, :rule_version, :rated_at)
            """,
            data,
        )
        self.conn.commit()

    def insert_scorecard(self, data: dict) -> None:
        data["rated_at"] = data.get("rated_at", datetime.now().isoformat())
        self.conn.execute(
            """
            INSERT INTO scorecard
                (etf_code, industry, category_type, signal_pe, signal_core,
                 signal_sentiment, total_score, action, a_share_gated,
                 human_override, rule_version, rated_at)
            VALUES
                (:etf_code, :industry, :category_type, :signal_pe, :signal_core,
                 :signal_sentiment, :total_score, :action, :a_share_gated,
                 :human_override, :rule_version, :rated_at)
            """,
            data,
        )
        self.conn.commit()

    def insert_trade(self, data: dict) -> None:
        self.conn.execute(
            """
            INSERT INTO trade_log
                (etf_code, action, shares, price, realized_pnl, reason,
                 rule_version, human_override, executed_at)
            VALUES
                (:etf_code, :action, :shares, :price, :realized_pnl, :reason,
                 :rule_version, :human_override, :executed_at)
            """,
            data,
        )
        self.conn.commit()

    def insert_report(self, data: dict) -> None:
        self.conn.execute(
            """
            INSERT INTO report
                (report_type, content, realized_pnl, unrealized_pnl,
                 cumulative_realized, raw_data_snapshot, fact_check_passed, generated_at)
            VALUES
                (:report_type, :content, :realized_pnl, :unrealized_pnl,
                 :cumulative_realized, :raw_data_snapshot, :fact_check_passed, :generated_at)
            """,
            data,
        )
        self.conn.commit()

    def get_build_up_state(self) -> dict | None:
        """Get current build-up state. Returns None if not initialized."""
        self.conn.row_factory = sqlite3.Row
        row = self.conn.execute(
            "SELECT * FROM build_up_state ORDER BY id DESC LIMIT 1"
        ).fetchone()
        return dict(row) if row else None

    def init_build_up(self, total_capital: float = 1_000_000) -> dict:
        """Initialize a new build-up round (reset)."""
        import json
        round_id = 1
        existing = self.conn.execute(
            "SELECT MAX(round_id) as max_round FROM build_up_state"
        ).fetchone()
        if existing and existing[0]:
            round_id = existing[0] + 1
        self.conn.execute(
            """INSERT INTO build_up_state
               (round_id, status, total_capital, current_batch, total_batches,
                batch_sizes, market_weights, market_deployed,
                phase, phase_amount, filled_amount, started_at)
               VALUES (?, 'waiting', ?, 0, 4,
                '[0.30,0.30,0.25,0.15]', '{}', '{}',
                1, 0, 0, ?)""",
            (round_id, total_capital, datetime.now().isoformat()),
        )
        self.conn.commit()
        return self.get_build_up_state()

    def set_build_up_weights(self, weights: dict) -> dict:
        """Update dynamic market allocation weights."""
        import json
        state = self.get_build_up_state()
        if not state:
            return None
        self.conn.execute(
            "UPDATE build_up_state SET market_weights = ? WHERE id = ?",
            (json.dumps(weights), state["id"]),
        )
        self.conn.commit()
        return self.get_build_up_state()

    def deploy_batch(self, market: str, amount: float) -> dict | None:
        """Record a batch deployment for a specific market. Advances batch when all markets done."""
        import json
        state = self.get_build_up_state()
        if not state or state["status"] != "deploying":
            return None

        deployed = json.loads(state.get("market_deployed", "{}"))
        deployed[market] = deployed.get(market, 0) + amount
        total_deployed = sum(deployed.values())

        self.conn.execute(
            """UPDATE build_up_state SET filled_amount = ?, market_deployed = ?,
               last_deploy_date = datetime('now') WHERE id = ?""",
            (total_deployed, json.dumps(deployed), state["id"]),
        )
        self.conn.commit()

        # Check if current batch is complete
        weights = json.loads(state.get("market_weights", "{}"))
        batch_sizes = json.loads(state.get("batch_sizes", "[0.30,0.30,0.25,0.15]"))
        batch = state["current_batch"]
        if batch > 0 and batch <= len(batch_sizes):
            batch_pct = batch_sizes[batch - 1]
            batch_target = state["total_capital"] * batch_pct
            # Batch done when total deployed hits batch target
            if total_deployed >= state["total_capital"] * sum(batch_sizes[:batch]):
                return self._advance_batch(state["id"])
        return self.get_build_up_state()

    def _advance_batch(self, state_id: int) -> dict | None:
        """Move to next batch, or mark done if all batches complete."""
        state = self.conn.execute("SELECT * FROM build_up_state WHERE id = ?", (state_id,)).fetchone()
        if not state:
            return None
        import json
        state = dict(state)
        batch_sizes = json.loads(state.get("batch_sizes", "[0.30,0.30,0.25,0.15]"))
        next_batch = state["current_batch"] + 1

        if next_batch > len(batch_sizes):
            self.conn.execute(
                """UPDATE build_up_state SET status = 'done', completed_at = datetime('now')
                   WHERE id = ?""", (state_id,),
            )
        else:
            self.conn.execute(
                "UPDATE build_up_state SET current_batch = ? WHERE id = ?",
                (next_batch, state_id),
            )
        self.conn.commit()
        return self.get_build_up_state()

    def start_deploying(self) -> dict | None:
        """Set status to deploying (called when PE window opens)."""
        state = self.get_build_up_state()
        if not state:
            return None
        self.conn.execute(
            "UPDATE build_up_state SET status = 'deploying', current_batch = 1 WHERE id = ?",
            (state["id"],),
        )
        self.conn.commit()
        return self.get_build_up_state()

    def pause_build_up(self) -> dict | None:
        """Pause build-up (PE window closed temporarily)."""
        state = self.get_build_up_state()
        if not state or state["status"] not in ("deploying", "paused"):
            return None
        self.conn.execute(
            "UPDATE build_up_state SET status = 'paused', paused_at = datetime('now') WHERE id = ?",
            (state["id"],),
        )
        self.conn.commit()
        return self.get_build_up_state()

    def resume_build_up(self) -> dict | None:
        """Resume from paused state (PE window reopened)."""
        state = self.get_build_up_state()
        if not state or state["status"] != "paused":
            return None
        self.conn.execute(
            "UPDATE build_up_state SET status = 'deploying', paused_at = NULL WHERE id = ?",
            (state["id"],),
        )
        self.conn.commit()
        return self.get_build_up_state()

    def add_to_phase(self, amount: float) -> dict | None:
        """Legacy: record purchase amount."""
        state = self.get_build_up_state()
        if not state:
            return None
        new_filled = state["filled_amount"] + amount
        self.conn.execute(
            "UPDATE build_up_state SET filled_amount = ? WHERE id = ?",
            (new_filled, state["id"]),
        )
        self.conn.commit()
        return self.get_build_up_state()

    def reset_build_up(self) -> dict:
        """Reset to a fresh round."""
        return self.init_build_up()

    def get_latest_classifications(self) -> list[dict]:
        self.conn.row_factory = sqlite3.Row
        rows = self.conn.execute(
            """
            SELECT c.*
            FROM classification c
            INNER JOIN (
                SELECT etf_code, MAX(rated_at) AS max_rated
                FROM classification
                GROUP BY etf_code
            ) latest ON c.etf_code = latest.etf_code
                     AND c.rated_at = latest.max_rated
            """
        ).fetchall()
        return [dict(r) for r in rows]

    # ── PE history ───────────────────────────────────────────────

    def save_pe(self, index_code: str, pe_value: float) -> None:
        self.conn.execute(
            "INSERT INTO pe_history (index_code, pe_value, recorded_at) VALUES (?, ?, ?)",
            (index_code, pe_value, datetime.now().isoformat()),
        )
        self.conn.commit()

    def get_pe_history(self, index_code: str) -> list[float]:
        self.conn.row_factory = sqlite3.Row
        rows = self.conn.execute(
            "SELECT pe_value FROM pe_history WHERE index_code = ? ORDER BY recorded_at ASC",
            (index_code,),
        ).fetchall()
        return [r["pe_value"] for r in rows]

    def compute_pe_percentile(self, index_code: str, current_pe: float) -> float | None:
        """Compute PE percentile from stored history. Returns None if no history."""
        history = self.get_pe_history(index_code)
        if not history:
            return None
        below = sum(1 for v in history if v < current_pe)
        return round(below / len(history), 4)
