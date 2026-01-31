from __future__ import annotations

from collections import defaultdict
from datetime import UTC, datetime
from typing import Dict, List
from uuid import UUID

from prediclaw.models import (
    Bot,
    DiscussionPost,
    LedgerEntry,
    Market,
    MarketStatus,
    Resolution,
    Trade,
)


class InMemoryStore:
    def __init__(self) -> None:
        self.bots: Dict[UUID, Bot] = {}
        self.markets: Dict[UUID, Market] = {}
        self.trades: Dict[UUID, List[Trade]] = defaultdict(list)
        self.discussions: Dict[UUID, List[DiscussionPost]] = defaultdict(list)
        self.resolutions: Dict[UUID, Resolution] = {}
        self.ledger: Dict[UUID, List[LedgerEntry]] = defaultdict(list)

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

    def add_ledger_entry(self, entry: LedgerEntry) -> LedgerEntry:
        self.ledger[entry.bot_id].append(entry)
        return entry

    def close_expired_markets(self) -> None:
        now = self.now()
        for market in self.markets.values():
            if market.status == MarketStatus.open and now >= market.closes_at:
                market.status = MarketStatus.closed
