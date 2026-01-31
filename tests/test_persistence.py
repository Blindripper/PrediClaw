from __future__ import annotations

from datetime import datetime, timedelta, timezone
from pathlib import Path
from uuid import uuid4

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
    Resolution,
    ResolutionVote,
    ResolverPolicy,
    TreasuryConfig,
    TreasuryLedgerEntry,
    Trade,
    WebhookRegistration,
)
from prediclaw.storage import PersistentStore


def test_persistent_store_reloads_state(tmp_path: Path) -> None:
    db_path = tmp_path / "prediclaw.db"
    store = PersistentStore(str(db_path))
    now = datetime.now(timezone.utc)

    bot = Bot(
        id=uuid4(),
        name="alpha",
        owner_id="owner",
        api_key="key",
        wallet_balance_bdc=42.0,
    )
    store.add_bot(bot)
    store.save_bot_policy(
        bot.id, BotPolicy(status=bot.status, max_requests_per_minute=120)
    )
    store.save_bot_config(
        bot.id, BotConfig(webhook_url="https://example.com/hook")
    )

    market = Market(
        id=uuid4(),
        creator_bot_id=bot.id,
        title="BTC > 80k?",
        description="Test market",
        category="crypto",
        status=MarketStatus.open,
        outcomes=["YES", "NO"],
        created_at=now,
        closes_at=now + timedelta(hours=1),
        resolver_policy=ResolverPolicy.single,
        outcome_pools={"YES": 10.0, "NO": 5.0},
    )
    store.add_market(market)

    trade = Trade(
        id=uuid4(),
        market_id=market.id,
        bot_id=bot.id,
        outcome_id="YES",
        amount_bdc=10.0,
        price=0.66,
        timestamp=now,
    )
    store.add_trade(trade)

    post = DiscussionPost(
        id=uuid4(),
        market_id=market.id,
        bot_id=bot.id,
        outcome_id="YES",
        body="Reasoning",
        confidence=0.8,
        timestamp=now,
    )
    store.add_discussion(post)

    resolution = Resolution(
        market_id=market.id,
        resolved_outcome_id="YES",
        resolver_bot_ids=[bot.id],
        evidence="oracle",
        timestamp=now + timedelta(hours=2),
    )
    store.add_resolution(resolution)
    store.add_resolution_votes(
        market.id,
        [
            ResolutionVote(
                resolver_bot_id=bot.id, outcome_id="YES", evidence="oracle"
            )
        ],
    )

    ledger_entry = LedgerEntry(
        id=uuid4(),
        bot_id=bot.id,
        market_id=market.id,
        delta_bdc=-10.0,
        reason="trade",
        timestamp=now,
    )
    store.add_ledger_entry(ledger_entry)

    webhook = WebhookRegistration(
        id=uuid4(),
        bot_id=bot.id,
        url="https://example.com/hook",
        event_types=[EventType.market_created],
        created_at=now,
    )
    store.add_webhook(webhook)

    event = Event(
        id=uuid4(),
        event_type=EventType.market_created,
        market_id=market.id,
        bot_id=bot.id,
        payload={"market_id": str(market.id)},
        timestamp=now,
    )
    store.add_event(event)

    treasury_entry = TreasuryLedgerEntry(
        id=uuid4(),
        market_id=market.id,
        delta_bdc=5.0,
        reason="resolution_remainder",
        timestamp=now,
    )
    store.add_treasury_entry(treasury_entry)
    store.treasury_balance_bdc = 5.0
    store.treasury_config = TreasuryConfig(send_unpaid_to_treasury=True)
    store.save_treasury_state()

    reloaded = PersistentStore(str(db_path))

    assert reloaded.bots[bot.id].name == "alpha"
    assert reloaded.bot_policies[bot.id].max_requests_per_minute == 120
    assert reloaded.bot_configs[bot.id].webhook_url == "https://example.com/hook"
    assert reloaded.markets[market.id].title == market.title
    assert reloaded.trades[market.id][0].id == trade.id
    assert reloaded.discussions[market.id][0].id == post.id
    assert reloaded.resolutions[market.id].resolved_outcome_id == "YES"
    assert reloaded.resolution_votes[market.id][0].resolver_bot_id == bot.id
    assert reloaded.ledger[bot.id][0].id == ledger_entry.id
    assert reloaded.webhooks[bot.id][0].id == webhook.id
    assert reloaded.events[0].id == event.id
    assert any(entry.webhook_id == webhook.id for entry in reloaded.outbox)
    assert reloaded.treasury_ledger[0].id == treasury_entry.id
    assert reloaded.treasury_balance_bdc == 5.0
