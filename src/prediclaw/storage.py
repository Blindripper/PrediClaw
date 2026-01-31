from __future__ import annotations

from collections import defaultdict, deque
from datetime import UTC, datetime
import json
import sqlite3
from typing import Any, Deque, Dict, Iterable, List
from uuid import UUID

from prediclaw.models import (
    Bot,
    BotConfig,
    BotPolicy,
    DiscussionPost,
    Event,
    EventType,
    LedgerEntry,
    Market,
    MarketStatus,
    OutboxEntry,
    Resolution,
    ResolutionVote,
    TreasuryState,
    TreasuryConfig,
    TreasuryLedgerEntry,
    Trade,
    WebhookRegistration,
)


class InMemoryStore:
    def __init__(self) -> None:
        self.bots: Dict[UUID, Bot] = {}
        self.bot_policies: Dict[UUID, BotPolicy] = {}
        self.bot_configs: Dict[UUID, BotConfig] = {}
        self.markets: Dict[UUID, Market] = {}
        self.trades: Dict[UUID, List[Trade]] = defaultdict(list)
        self.discussions: Dict[UUID, List[DiscussionPost]] = defaultdict(list)
        self.resolutions: Dict[UUID, Resolution] = {}
        self.resolution_votes: Dict[UUID, List[ResolutionVote]] = defaultdict(list)
        self.ledger: Dict[UUID, List[LedgerEntry]] = defaultdict(list)
        self.treasury_ledger: List[TreasuryLedgerEntry] = []
        self.bot_request_log: Dict[UUID, Deque[datetime]] = defaultdict(deque)
        self.webhooks: Dict[UUID, List[WebhookRegistration]] = defaultdict(list)
        self.events: List[Event] = []
        self.outbox: List[OutboxEntry] = []
        self.treasury_balance_bdc: float = 0.0
        self.treasury_config = TreasuryConfig()

    def now(self) -> datetime:
        return datetime.now(tz=UTC)

    def add_bot(self, bot: Bot) -> Bot:
        self.bots[bot.id] = bot
        self.bot_policies[bot.id] = BotPolicy(status=bot.status)
        self.bot_configs[bot.id] = BotConfig()
        return bot

    def save_bot(self, bot: Bot) -> None:
        self.bots[bot.id] = bot

    def save_bot_policy(self, bot_id: UUID, policy: BotPolicy) -> None:
        self.bot_policies[bot_id] = policy

    def save_bot_config(self, bot_id: UUID, config: BotConfig) -> None:
        self.bot_configs[bot_id] = config

    def add_market(self, market: Market) -> Market:
        if not market.outcome_pools:
            market.outcome_pools = {outcome: 0.0 for outcome in market.outcomes}
        self.markets[market.id] = market
        return market

    def save_market(self, market: Market) -> None:
        self.markets[market.id] = market

    def add_trade(self, trade: Trade) -> Trade:
        self.trades[trade.market_id].append(trade)
        return trade

    def add_discussion(self, post: DiscussionPost) -> DiscussionPost:
        self.discussions[post.market_id].append(post)
        return post

    def add_resolution(self, resolution: Resolution) -> Resolution:
        self.resolutions[resolution.market_id] = resolution
        return resolution

    def add_resolution_votes(
        self, market_id: UUID, votes: List[ResolutionVote]
    ) -> List[ResolutionVote]:
        self.resolution_votes[market_id] = votes
        return votes

    def add_ledger_entry(self, entry: LedgerEntry) -> LedgerEntry:
        self.ledger[entry.bot_id].append(entry)
        return entry

    def add_treasury_entry(
        self, entry: TreasuryLedgerEntry
    ) -> TreasuryLedgerEntry:
        self.treasury_ledger.append(entry)
        return entry

    def add_webhook(self, webhook: WebhookRegistration) -> WebhookRegistration:
        self.webhooks[webhook.bot_id].append(webhook)
        return webhook

    def add_event(self, event: Event) -> Event:
        self.events.append(event)
        for registrations in self.webhooks.values():
            for webhook in registrations:
                if webhook.event_types and event.event_type not in webhook.event_types:
                    continue
                self.outbox.append(
                    OutboxEntry(
                        webhook_id=webhook.id,
                        event_id=event.id,
                        event_type=event.event_type,
                        target_url=webhook.url,
                        status="pending",
                        created_at=self.now(),
                    )
                )
        return event

    def save_outbox_entry(self, entry: OutboxEntry) -> None:
        self.outbox.append(entry)

    def close_expired_markets(self) -> None:
        now = self.now()
        for market in self.markets.values():
            if market.status == MarketStatus.open and now >= market.closes_at:
                market.status = MarketStatus.closed
                self.save_market(market)
                self.add_event(
                    Event(
                        event_type=EventType.market_closed,
                        market_id=market.id,
                        bot_id=market.creator_bot_id,
                        payload={"status": market.status},
                        timestamp=now,
                    )
                )

    def prune_bot_requests(self, bot_id: UUID, window_seconds: int) -> Deque[datetime]:
        now = self.now()
        cutoff = now.timestamp() - window_seconds
        entries = self.bot_request_log[bot_id]
        while entries and entries[0].timestamp() < cutoff:
            entries.popleft()
        return entries

    def save_treasury_state(self) -> None:
        return None


class PersistentStore(InMemoryStore):
    def __init__(self, db_path: str) -> None:
        super().__init__()
        self._db_path = db_path
        self._conn = sqlite3.connect(self._db_path, check_same_thread=False)
        self._conn.row_factory = sqlite3.Row
        self._initialize_schema()
        self._load_state()

    def _initialize_schema(self) -> None:
        cursor = self._conn.cursor()
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS bots (
                id TEXT PRIMARY KEY,
                data TEXT NOT NULL
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS bot_policies (
                id TEXT PRIMARY KEY,
                data TEXT NOT NULL
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS bot_configs (
                id TEXT PRIMARY KEY,
                data TEXT NOT NULL
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS markets (
                id TEXT PRIMARY KEY,
                data TEXT NOT NULL
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS trades (
                id TEXT PRIMARY KEY,
                market_id TEXT NOT NULL,
                data TEXT NOT NULL
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS discussions (
                id TEXT PRIMARY KEY,
                market_id TEXT NOT NULL,
                data TEXT NOT NULL
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS resolutions (
                id TEXT PRIMARY KEY,
                data TEXT NOT NULL
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS resolution_votes (
                id TEXT PRIMARY KEY,
                market_id TEXT NOT NULL,
                data TEXT NOT NULL
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS ledger (
                id TEXT PRIMARY KEY,
                bot_id TEXT NOT NULL,
                data TEXT NOT NULL
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS treasury_ledger (
                id TEXT PRIMARY KEY,
                data TEXT NOT NULL
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS webhooks (
                id TEXT PRIMARY KEY,
                bot_id TEXT NOT NULL,
                data TEXT NOT NULL
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS events (
                id TEXT PRIMARY KEY,
                data TEXT NOT NULL
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS outbox (
                id TEXT PRIMARY KEY,
                data TEXT NOT NULL
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS treasury_state (
                id TEXT PRIMARY KEY,
                data TEXT NOT NULL
            )
            """
        )
        self._conn.commit()

    def _serialize(self, model: Any) -> str:
        if hasattr(model, "model_dump"):
            payload = model.model_dump(mode="json")
        else:
            payload = model
        return json.dumps(payload)

    def _deserialize(self, model_type: Any, payload: str) -> Any:
        data = json.loads(payload)
        if hasattr(model_type, "model_validate"):
            return model_type.model_validate(data)
        return data

    def _upsert(self, table: str, key: str, payload: str) -> None:
        cursor = self._conn.cursor()
        cursor.execute(
            f"INSERT OR REPLACE INTO {table} (id, data) VALUES (?, ?)",
            (key, payload),
        )
        self._conn.commit()

    def _upsert_with_field(
        self, table: str, key: str, field_name: str, field_value: str, payload: str
    ) -> None:
        cursor = self._conn.cursor()
        cursor.execute(
            f"INSERT OR REPLACE INTO {table} (id, {field_name}, data) VALUES (?, ?, ?)",
            (key, field_value, payload),
        )
        self._conn.commit()

    def _load_rows(self, table: str) -> Iterable[sqlite3.Row]:
        cursor = self._conn.cursor()
        cursor.execute(f"SELECT * FROM {table}")
        return cursor.fetchall()

    def _load_state(self) -> None:
        for row in self._load_rows("bots"):
            bot = self._deserialize(Bot, row["data"])
            self.bots[bot.id] = bot
        for row in self._load_rows("bot_policies"):
            policy = self._deserialize(BotPolicy, row["data"])
            self.bot_policies[UUID(row["id"])] = policy
        for row in self._load_rows("bot_configs"):
            config = self._deserialize(BotConfig, row["data"])
            self.bot_configs[UUID(row["id"])] = config
        for row in self._load_rows("markets"):
            market = self._deserialize(Market, row["data"])
            self.markets[market.id] = market
        for row in self._load_rows("trades"):
            trade = self._deserialize(Trade, row["data"])
            self.trades[trade.market_id].append(trade)
        for row in self._load_rows("discussions"):
            post = self._deserialize(DiscussionPost, row["data"])
            self.discussions[post.market_id].append(post)
        for row in self._load_rows("resolutions"):
            resolution = self._deserialize(Resolution, row["data"])
            self.resolutions[resolution.market_id] = resolution
        for row in self._load_rows("resolution_votes"):
            vote = self._deserialize(ResolutionVote, row["data"])
            market_id = UUID(row["market_id"])
            self.resolution_votes[market_id].append(vote)
        for row in self._load_rows("ledger"):
            entry = self._deserialize(LedgerEntry, row["data"])
            self.ledger[entry.bot_id].append(entry)
        for row in self._load_rows("treasury_ledger"):
            entry = self._deserialize(TreasuryLedgerEntry, row["data"])
            self.treasury_ledger.append(entry)
        for row in self._load_rows("webhooks"):
            webhook = self._deserialize(WebhookRegistration, row["data"])
            self.webhooks[webhook.bot_id].append(webhook)
        for row in self._load_rows("events"):
            event = self._deserialize(Event, row["data"])
            self.events.append(event)
        for row in self._load_rows("outbox"):
            entry = self._deserialize(OutboxEntry, row["data"])
            self.outbox.append(entry)
        state_rows = self._load_rows("treasury_state")
        if state_rows:
            state = self._deserialize(TreasuryState, state_rows[0]["data"])
            self.treasury_balance_bdc = state.balance_bdc
            self.treasury_config = state.config

    def add_bot(self, bot: Bot) -> Bot:
        bot = super().add_bot(bot)
        self._upsert("bots", str(bot.id), self._serialize(bot))
        policy = self.bot_policies[bot.id]
        self._upsert("bot_policies", str(bot.id), self._serialize(policy))
        config = self.bot_configs[bot.id]
        self._upsert("bot_configs", str(bot.id), self._serialize(config))
        return bot

    def save_bot(self, bot: Bot) -> None:
        super().save_bot(bot)
        self._upsert("bots", str(bot.id), self._serialize(bot))

    def save_bot_policy(self, bot_id: UUID, policy: BotPolicy) -> None:
        super().save_bot_policy(bot_id, policy)
        self._upsert("bot_policies", str(bot_id), self._serialize(policy))

    def save_bot_config(self, bot_id: UUID, config: BotConfig) -> None:
        super().save_bot_config(bot_id, config)
        self._upsert("bot_configs", str(bot_id), self._serialize(config))

    def add_market(self, market: Market) -> Market:
        market = super().add_market(market)
        self._upsert("markets", str(market.id), self._serialize(market))
        return market

    def save_market(self, market: Market) -> None:
        super().save_market(market)
        self._upsert("markets", str(market.id), self._serialize(market))

    def add_trade(self, trade: Trade) -> Trade:
        trade = super().add_trade(trade)
        self._upsert_with_field(
            "trades",
            str(trade.id),
            "market_id",
            str(trade.market_id),
            self._serialize(trade),
        )
        return trade

    def add_discussion(self, post: DiscussionPost) -> DiscussionPost:
        post = super().add_discussion(post)
        self._upsert_with_field(
            "discussions",
            str(post.id),
            "market_id",
            str(post.market_id),
            self._serialize(post),
        )
        return post

    def add_resolution(self, resolution: Resolution) -> Resolution:
        resolution = super().add_resolution(resolution)
        self._upsert(
            "resolutions", str(resolution.market_id), self._serialize(resolution)
        )
        return resolution

    def add_resolution_votes(
        self, market_id: UUID, votes: List[ResolutionVote]
    ) -> List[ResolutionVote]:
        stored_votes = super().add_resolution_votes(market_id, votes)
        for vote in stored_votes:
            self._upsert_with_field(
                "resolution_votes",
                f"{market_id}:{vote.resolver_bot_id}",
                "market_id",
                str(market_id),
                self._serialize(vote),
            )
        return stored_votes

    def add_ledger_entry(self, entry: LedgerEntry) -> LedgerEntry:
        entry = super().add_ledger_entry(entry)
        self._upsert_with_field(
            "ledger",
            str(entry.id),
            "bot_id",
            str(entry.bot_id),
            self._serialize(entry),
        )
        return entry

    def add_treasury_entry(
        self, entry: TreasuryLedgerEntry
    ) -> TreasuryLedgerEntry:
        entry = super().add_treasury_entry(entry)
        self._upsert("treasury_ledger", str(entry.id), self._serialize(entry))
        return entry

    def add_webhook(self, webhook: WebhookRegistration) -> WebhookRegistration:
        webhook = super().add_webhook(webhook)
        self._upsert_with_field(
            "webhooks",
            str(webhook.id),
            "bot_id",
            str(webhook.bot_id),
            self._serialize(webhook),
        )
        return webhook

    def add_event(self, event: Event) -> Event:
        event = super().add_event(event)
        self._upsert("events", str(event.id), self._serialize(event))
        for entry in self.outbox:
            self._upsert("outbox", str(entry.id), self._serialize(entry))
        return event

    def save_outbox_entry(self, entry: OutboxEntry) -> None:
        super().save_outbox_entry(entry)
        self._upsert("outbox", str(entry.id), self._serialize(entry))

    def save_treasury_state(self) -> None:
        state = TreasuryState(
            balance_bdc=self.treasury_balance_bdc,
            config=self.treasury_config,
        )
        self._upsert("treasury_state", "state", self._serialize(state))
