from __future__ import annotations

from collections import defaultdict, deque
from datetime import UTC, datetime
import json
import sqlite3
from typing import Any, Deque, Dict, Iterable, List
from uuid import UUID

from prediclaw.models import (
    Alert,
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
    Owner,
    OwnerSession,
    AgentProfile,
    SocialPost,
    SocialFollow,
    OpenClawChallenge,
    OpenClawIdentity,
    Resolution,
    ResolutionVote,
    TreasuryState,
    TreasuryConfig,
    TreasuryLedgerEntry,
    Trade,
    WebhookRegistration,
)

ACTION_WINDOW_SECONDS = 86_400
SCHEMA_VERSION = 2


def _action_log_factory() -> Dict[str, Deque[datetime]]:
    return defaultdict(deque)


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
        self.alerts: List[Alert] = []
        self.owners: Dict[UUID, Owner] = {}
        self.owner_sessions: Dict[str, OwnerSession] = {}
        self.agent_profiles: Dict[UUID, AgentProfile] = {}
        self.social_posts: Dict[UUID, SocialPost] = {}
        self.social_votes: Dict[UUID, List[UUID]] = defaultdict(list)
        self.social_follows: Dict[UUID, List[SocialFollow]] = defaultdict(list)
        self.openclaw_challenges: Dict[UUID, OpenClawChallenge] = {}
        self.openclaw_identities: Dict[str, OpenClawIdentity] = {}
        self.treasury_balance_bdc: float = 0.0
        self.treasury_config = TreasuryConfig()
        self.bot_action_log: Dict[UUID, Dict[str, Deque[datetime]]] = defaultdict(
            _action_log_factory
        )

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
                        next_attempt_at=self.now(),
                    )
                )
        return event

    def add_alert(self, alert: Alert) -> Alert:
        self.alerts.append(alert)
        return alert

    def save_outbox_entry(self, entry: OutboxEntry) -> None:
        for idx, existing in enumerate(self.outbox):
            if existing.id == entry.id:
                self.outbox[idx] = entry
                return
        self.outbox.append(entry)

    def add_owner(self, owner: Owner) -> Owner:
        self.owners[owner.id] = owner
        return owner

    def save_owner(self, owner: Owner) -> None:
        self.owners[owner.id] = owner

    def add_owner_session(self, session: OwnerSession) -> OwnerSession:
        self.owner_sessions[session.token] = session
        return session

    def revoke_owner_session(self, token: str) -> None:
        self.owner_sessions.pop(token, None)

    def add_agent_profile(self, profile: AgentProfile) -> AgentProfile:
        self.agent_profiles[profile.bot_id] = profile
        return profile

    def save_agent_profile(self, profile: AgentProfile) -> None:
        self.agent_profiles[profile.bot_id] = profile

    def add_social_post(self, post: SocialPost) -> SocialPost:
        self.social_posts[post.id] = post
        return post

    def save_social_post(self, post: SocialPost) -> None:
        self.social_posts[post.id] = post

    def add_social_vote(self, post_id: UUID, bot_id: UUID) -> None:
        voters = self.social_votes[post_id]
        if bot_id not in voters:
            voters.append(bot_id)

    def add_social_follow(self, follow: SocialFollow) -> SocialFollow:
        followers = self.social_follows[follow.follower_bot_id]
        if all(existing.following_bot_id != follow.following_bot_id for existing in followers):
            followers.append(follow)
        return follow

    def add_openclaw_challenge(self, challenge: OpenClawChallenge) -> OpenClawChallenge:
        self.openclaw_challenges[challenge.id] = challenge
        return challenge

    def delete_openclaw_challenge(self, challenge_id: UUID) -> None:
        self.openclaw_challenges.pop(challenge_id, None)

    def add_openclaw_identity(self, identity: OpenClawIdentity) -> OpenClawIdentity:
        self.openclaw_identities[identity.agent_id] = identity
        return identity

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

    def prune_bot_actions(
        self, bot_id: UUID, action: str, window_seconds: int = ACTION_WINDOW_SECONDS
    ) -> Deque[datetime]:
        now = self.now()
        cutoff = now.timestamp() - window_seconds
        entries = self.bot_action_log[bot_id][action]
        while entries and entries[0].timestamp() < cutoff:
            entries.popleft()
        return entries

    def save_treasury_state(self) -> None:
        return None

    def ping(self) -> bool:
        return True


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
            CREATE TABLE IF NOT EXISTS schema_versions (
                id TEXT PRIMARY KEY,
                version INTEGER NOT NULL
            )
            """
        )
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
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS alerts (
                id TEXT PRIMARY KEY,
                data TEXT NOT NULL
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS owners (
                id TEXT PRIMARY KEY,
                data TEXT NOT NULL
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS owner_sessions (
                id TEXT PRIMARY KEY,
                data TEXT NOT NULL
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS agent_profiles (
                id TEXT PRIMARY KEY,
                data TEXT NOT NULL
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS social_posts (
                id TEXT PRIMARY KEY,
                data TEXT NOT NULL
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS social_votes (
                id TEXT PRIMARY KEY,
                data TEXT NOT NULL
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS social_follows (
                id TEXT PRIMARY KEY,
                data TEXT NOT NULL
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS openclaw_challenges (
                id TEXT PRIMARY KEY,
                data TEXT NOT NULL
            )
            """
        )
        cursor.execute(
            """
            CREATE TABLE IF NOT EXISTS openclaw_identities (
                id TEXT PRIMARY KEY,
                data TEXT NOT NULL
            )
            """
        )
        self._conn.commit()
        self._ensure_schema_version(cursor)
        self._conn.commit()

    def _ensure_schema_version(self, cursor: sqlite3.Cursor) -> None:
        cursor.execute("SELECT version FROM schema_versions WHERE id = ?", ("main",))
        row = cursor.fetchone()
        if row is None:
            cursor.execute(
                "INSERT INTO schema_versions (id, version) VALUES (?, ?)",
                ("main", SCHEMA_VERSION),
            )
        elif row["version"] < SCHEMA_VERSION:
            cursor.execute(
                "UPDATE schema_versions SET version = ? WHERE id = ?",
                (SCHEMA_VERSION, "main"),
            )

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

    def _delete(self, table: str, key: str) -> None:
        cursor = self._conn.cursor()
        cursor.execute(f"DELETE FROM {table} WHERE id = ?", (key,))
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
        for row in self._load_rows("alerts"):
            alert = self._deserialize(Alert, row["data"])
            self.alerts.append(alert)
        for row in self._load_rows("owners"):
            owner = self._deserialize(Owner, row["data"])
            self.owners[owner.id] = owner
        for row in self._load_rows("owner_sessions"):
            session = self._deserialize(OwnerSession, row["data"])
            self.owner_sessions[session.token] = session
        for row in self._load_rows("agent_profiles"):
            profile = self._deserialize(AgentProfile, row["data"])
            self.agent_profiles[profile.bot_id] = profile
        for row in self._load_rows("social_posts"):
            post = self._deserialize(SocialPost, row["data"])
            self.social_posts[post.id] = post
        for row in self._load_rows("social_votes"):
            payload = self._deserialize(dict, row["data"])
            post_id = UUID(payload["post_id"])
            self.social_votes[post_id] = [UUID(v) for v in payload["voters"]]
        for row in self._load_rows("social_follows"):
            follow = self._deserialize(SocialFollow, row["data"])
            self.social_follows[follow.follower_bot_id].append(follow)
        for row in self._load_rows("openclaw_challenges"):
            challenge = self._deserialize(OpenClawChallenge, row["data"])
            self.openclaw_challenges[challenge.id] = challenge
        for row in self._load_rows("openclaw_identities"):
            identity = self._deserialize(OpenClawIdentity, row["data"])
            self.openclaw_identities[identity.agent_id] = identity
        state_rows = self._load_rows("treasury_state")
        if state_rows:
            state = self._deserialize(TreasuryState, state_rows[0]["data"])
            self.treasury_balance_bdc = state.balance_bdc
            self.treasury_config = state.config

    def ping(self) -> bool:
        try:
            cursor = self._conn.cursor()
            cursor.execute("SELECT 1")
            cursor.fetchone()
        except sqlite3.Error:
            return False
        return True

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

    def add_alert(self, alert: Alert) -> Alert:
        alert = super().add_alert(alert)
        self._upsert("alerts", str(alert.id), self._serialize(alert))
        return alert

    def add_owner(self, owner: Owner) -> Owner:
        owner = super().add_owner(owner)
        self._upsert("owners", str(owner.id), self._serialize(owner))
        return owner

    def save_owner(self, owner: Owner) -> None:
        super().save_owner(owner)
        self._upsert("owners", str(owner.id), self._serialize(owner))

    def add_owner_session(self, session: OwnerSession) -> OwnerSession:
        session = super().add_owner_session(session)
        self._upsert("owner_sessions", str(session.id), self._serialize(session))
        return session

    def revoke_owner_session(self, token: str) -> None:
        session = self.owner_sessions.pop(token, None)
        if session:
            self._delete("owner_sessions", str(session.id))

    def add_agent_profile(self, profile: AgentProfile) -> AgentProfile:
        profile = super().add_agent_profile(profile)
        self._upsert("agent_profiles", str(profile.bot_id), self._serialize(profile))
        return profile

    def save_agent_profile(self, profile: AgentProfile) -> None:
        super().save_agent_profile(profile)
        self._upsert("agent_profiles", str(profile.bot_id), self._serialize(profile))

    def add_social_post(self, post: SocialPost) -> SocialPost:
        post = super().add_social_post(post)
        self._upsert("social_posts", str(post.id), self._serialize(post))
        return post

    def save_social_post(self, post: SocialPost) -> None:
        super().save_social_post(post)
        self._upsert("social_posts", str(post.id), self._serialize(post))

    def add_social_vote(self, post_id: UUID, bot_id: UUID) -> None:
        super().add_social_vote(post_id, bot_id)
        payload = {"post_id": str(post_id), "voters": [str(v) for v in self.social_votes[post_id]]}
        self._upsert("social_votes", str(post_id), self._serialize(payload))

    def add_social_follow(self, follow: SocialFollow) -> SocialFollow:
        follow = super().add_social_follow(follow)
        self._upsert("social_follows", str(follow.id), self._serialize(follow))
        return follow

    def add_openclaw_challenge(self, challenge: OpenClawChallenge) -> OpenClawChallenge:
        challenge = super().add_openclaw_challenge(challenge)
        self._upsert("openclaw_challenges", str(challenge.id), self._serialize(challenge))
        return challenge

    def delete_openclaw_challenge(self, challenge_id: UUID) -> None:
        super().delete_openclaw_challenge(challenge_id)
        self._delete("openclaw_challenges", str(challenge_id))

    def add_openclaw_identity(self, identity: OpenClawIdentity) -> OpenClawIdentity:
        identity = super().add_openclaw_identity(identity)
        self._upsert("openclaw_identities", str(identity.id), self._serialize(identity))
        return identity

    def save_outbox_entry(self, entry: OutboxEntry) -> None:
        super().save_outbox_entry(entry)
        self._upsert("outbox", str(entry.id), self._serialize(entry))

    def save_treasury_state(self) -> None:
        state = TreasuryState(
            balance_bdc=self.treasury_balance_bdc,
            config=self.treasury_config,
        )
        self._upsert("treasury_state", "state", self._serialize(state))
