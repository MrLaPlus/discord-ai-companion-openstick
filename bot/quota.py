from __future__ import annotations

import time

from .config import Config
from .storage import Store


class QuotaExceeded(Exception):
    def __init__(self, retry_after: float, daily: bool = False):
        self.retry_after = max(1, retry_after)
        self.daily = daily
        super().__init__("Daily quota reached" if daily else "Please wait before another AI request")


class Quota:
    def __init__(self, store: Store, config: Config):
        self.store, self.config = store, config

    def reserve(self, tokens: int, now: float | None = None, *, category="chat") -> int:
        now = time.time() if now is None else now
        pause = self.store.meta("provider_pause_until", 0)
        if now < pause:
            raise QuotaExceeded(pause - now)
        # Reserve pending work; release overestimates only after actual provider usage arrives.
        from .control import budget_settings

        budget = budget_settings(self.store)
        cost = tokens * budget["usd_per_million"] / 1_000_000
        with self.store.db:
            self.store.db.execute("BEGIN IMMEDIATE")
            used = self.store.db.execute(
                "SELECT coalesce(sum(max(reserved_cost,coalesce(actual_cost,0))),0) FROM attempts WHERE created>?",
                (now - 86400,),
            ).fetchone()[0]
            if budget["daily_usd"] and used + cost > budget["daily_usd"]:
                raise QuotaExceeded(86400, daily=True)
            for seconds, requests, cap in [
                (86400, self.config.rpd, self.config.tpd),
                (60, self.config.rpm, self.config.tpm),
            ]:
                row = self.store.db.execute(
                    "SELECT count(*),coalesce(sum(coalesce(actual,reserved)),0),min(created) "
                    "FROM attempts WHERE created>?",
                    (now - seconds,),
                ).fetchone()
                if row[0] >= requests or row[1] + tokens > cap:
                    retry = (row[2] + seconds - now) if row[2] is not None else seconds
                    raise QuotaExceeded(retry, daily=seconds == 86400)
            cursor = self.store.db.execute(
                "INSERT INTO attempts(created,reserved,category,reserved_cost) VALUES (?,?,?,?)",
                (now, tokens, category, cost),
            )
        return cursor.lastrowid

    def finish(self, attempt: int, actual: int | None, status: str):
        with self.store.db:
            self.store.db.execute(
                "UPDATE attempts SET actual=?,status=?,actual_cost=CASE WHEN ? IS NULL THEN NULL ELSE reserved_cost*?/max(reserved,1) END WHERE id=?",
                (actual, status, actual, actual or 0, attempt),
            )

    def defer(self, seconds: float):
        self.store.set_meta("provider_pause_until", time.time() + min(max(seconds, 1), 86400))

    def summary(self) -> dict:
        row = self.store.db.execute(
            "SELECT count(*),coalesce(sum(reserved),0),coalesce(sum(actual),0) FROM attempts WHERE created>?",
            (time.time() - 86400,),
        ).fetchone()
        return {"requests_24h": row[0], "reserved_tokens_24h": row[1], "reported_tokens_24h": row[2]}

    def remaining_tokens(self) -> int:
        now = time.time()
        result = []
        for window, limit in [(60, self.config.tpm), (86400, self.config.tpd)]:
            used = self.store.db.execute(
                "SELECT coalesce(sum(coalesce(actual,reserved)),0) FROM attempts WHERE created>?",
                (now - window,),
            ).fetchone()[0]
            result.append(max(0, limit - used))
        return min(result)

    def utilization(self, now: float | None = None) -> dict[str, float]:
        now = time.time() if now is None else now
        usage = {}
        for seconds, requests, tokens in [
            (60, self.config.rpm, self.config.tpm),
            (86400, self.config.rpd, self.config.tpd),
        ]:
            row = self.store.db.execute(
                "SELECT count(*),coalesce(sum(coalesce(actual,reserved)),0) FROM attempts WHERE created>?",
                (now - seconds,),
            ).fetchone()
            usage[f"requests_{seconds}"] = row[0] * 100 / requests
            usage[f"tokens_{seconds}"] = row[1] * 100 / tokens
        return usage
