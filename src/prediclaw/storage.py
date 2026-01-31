from __future__ import annotations

from collections import defaultdict, deque
from datetime import UTC, datetime
from typing import Deque, Dict, List
from uuid import UUID

from prediclaw.models import (
    Bot,
    DiscussionPost,
    Event,
    EventType,
    LedgerEntry,
    Market,
    MarketStatus,
    OutboxEntry,
    Resolution,
    ResolutionVote,
    Trade,
    WebhookRegistration,
)


class InMemoryStore:
    def __init__(self) -> None:
        self.bots: Dict[UUID, Bot] = {}
        self.markets: Dict[UUID, Market] = {}
        self.trades: Dict[UUID, List[Trade]] = defaultdict(list)
        self.discussions: Dict[UUID, List[DiscussionPost]] = defaultdict(list)
        self.resolutions: Dict[UUID, Resolution] = {}
        self.resolution_votes: Dict[UUID, List[ResolutionVote]] = defaultdict(list)
        self.ledger: Dict[UUID, List[LedgerEntry]] = defaultdict(list)
        self.bot_request_log: Dict[UUID, Deque[datetime]] = defaultdict(deque)
        self.webhooks: Dict[UUID, List[WebhookRegistration]] = defaultdict(list)
        self.events: List[Event] = []
        self.outbox: List[OutboxEntry] = []

    def now(self) -> datetime:
        return datetime.now(tz=UTC)

    def add_bot(self, bot: Bot) -> Bot:
        self.bots[bot.id] = bot
        return bot

    def add_market(self, market: Market) -> Market:
        if not market.outcome_pools:
            market.outcome_pools = {outcome: 0.0 for outcome in market.outcomes}
        self.markets[market.id] = market
        return market

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

    def close_expired_markets(self) -> None:
        now = self.now()
        for market in self.markets.values():
            if market.status == MarketStatus.open and now >= market.closes_at:
                market.status = MarketStatus.closed
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
