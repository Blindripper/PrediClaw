from __future__ import annotations

import asyncio
import contextlib
import os
import secrets
from typing import List, Optional
from uuid import UUID

from fastapi import FastAPI, Header, HTTPException
from pydantic import BaseModel

from prediclaw.models import (
    Bot,
    BotCreateRequest,
    BotDepositRequest,
    DiscussionPost,
    DiscussionPostCreateRequest,
    Event,
    EventType,
    LedgerEntry,
    Market,
    MarketCreateRequest,
    MarketStatus,
    OutboxEntry,
    Resolution,
    ResolutionRequest,
    ResolutionVote,
    ResolverPolicy,
    TreasuryConfig,
    TreasuryLedgerEntry,
    TreasuryState,
    Trade,
    TradeCreateRequest,
    WebhookRegistration,
    WebhookRegistrationRequest,
)
from prediclaw.storage import InMemoryStore


class TradeResponse(BaseModel):
    trade: Trade
    updated_market: Market


class ResolveResponse(BaseModel):
    resolution: Resolution
    payouts: List[LedgerEntry]
    market: Market


store = InMemoryStore()
app = FastAPI(title="PrediClaw API", version="0.1.0")
MAX_BOT_REQUESTS_PER_MINUTE = 60
RATE_LIMIT_WINDOW_SECONDS = 60
MIN_BOT_BALANCE_BDC = 10.0
MIN_BOT_REPUTATION_SCORE = 1.0
MARKET_LIFECYCLE_POLL_SECONDS = int(
    os.getenv("PREDICLAW_LIFECYCLE_POLL_SECONDS", "30")
)
AUTO_RESOLVE_ENABLED = os.getenv("PREDICLAW_AUTO_RESOLVE", "false").lower() in {
    "1",
    "true",
    "yes",
}


def settle_market_resolution(
    *,
    market: Market,
    resolved_outcome_id: str,
    resolver_bot_ids: List[UUID],
    actor_bot_id: UUID,
    evidence: Optional[str] = None,
    votes: Optional[List[ResolutionVote]] = None,
) -> ResolveResponse:
    market.status = MarketStatus.resolved
    market.resolved_at = store.now()
    resolution = Resolution(
        market_id=market.id,
        resolved_outcome_id=resolved_outcome_id,
        resolver_bot_ids=resolver_bot_ids,
        evidence=evidence,
        timestamp=market.resolved_at,
    )
    store.add_resolution(resolution)
    store.add_event(
        Event(
            event_type=EventType.market_resolved,
            market_id=market.id,
            bot_id=actor_bot_id,
            payload={
                "resolved_outcome_id": resolution.resolved_outcome_id,
                "resolver_bot_ids": [
                    str(resolver_id) for resolver_id in resolution.resolver_bot_ids
                ],
            },
            timestamp=resolution.timestamp,
        )
    )
    if votes:
        store.add_resolution_votes(market.id, votes)

    total_pool = sum(market.outcome_pools.values())
    winning_pool = market.outcome_pools.get(resolved_outcome_id, 0.0)
    payouts: List[LedgerEntry] = []
    if winning_pool > 0:
        for trade in store.trades.get(market.id, []):
            if trade.outcome_id != resolved_outcome_id:
                continue
            share = trade.amount_bdc / winning_pool
            payout_amount = share * total_pool
            bot = get_bot_or_404(trade.bot_id)
            bot.wallet_balance_bdc += payout_amount
            entry = LedgerEntry(
                bot_id=bot.id,
                market_id=market.id,
                delta_bdc=payout_amount,
                reason="payout",
                timestamp=store.now(),
            )
            store.add_ledger_entry(entry)
            payouts.append(entry)
    total_payout_amount = sum(entry.delta_bdc for entry in payouts)
    remainder = total_pool - total_payout_amount
    if remainder > 0:
        config = store.treasury_config
        liquidity_distribution = 0.0
        if (
            config.liquidity_bot_allocation_pct > 0
            and config.liquidity_bot_weights
        ):
            weight_sum = sum(config.liquidity_bot_weights.values())
            if weight_sum > 0:
                liquidity_distribution = (
                    remainder * config.liquidity_bot_allocation_pct
                )
                for bot_id, weight in config.liquidity_bot_weights.items():
                    if weight <= 0:
                        continue
                    amount = liquidity_distribution * (weight / weight_sum)
                    if amount <= 0:
                        continue
                    bot = get_bot_or_404(bot_id)
                    bot.wallet_balance_bdc += amount
                    store.add_ledger_entry(
                        LedgerEntry(
                            bot_id=bot.id,
                            market_id=market.id,
                            delta_bdc=amount,
                            reason="liquidity_distribution",
                            timestamp=store.now(),
                        )
                    )
        treasury_amount = remainder - liquidity_distribution
        if config.send_unpaid_to_treasury and treasury_amount > 0:
            store.treasury_balance_bdc += treasury_amount
            store.add_treasury_entry(
                TreasuryLedgerEntry(
                    market_id=market.id,
                    delta_bdc=treasury_amount,
                    reason="resolution_remainder",
                    timestamp=store.now(),
                )
            )
    return ResolveResponse(resolution=resolution, payouts=payouts, market=market)


def select_auto_resolve_outcome(market: Market) -> str:
    if not market.outcome_pools:
        market.outcome_pools = {outcome: 0.0 for outcome in market.outcomes}
    outcome_id, _ = max(
        market.outcome_pools.items(), key=lambda item: (item[1], item[0])
    )
    return outcome_id


def auto_resolve_markets() -> None:
    for market in store.markets.values():
        if market.status != MarketStatus.closed:
            continue
        if market.resolver_policy != ResolverPolicy.single:
            continue
        if market.id in store.resolutions:
            continue
        outcome_id = select_auto_resolve_outcome(market)
        settle_market_resolution(
            market=market,
            resolved_outcome_id=outcome_id,
            resolver_bot_ids=[market.creator_bot_id],
            actor_bot_id=market.creator_bot_id,
            evidence="auto_resolve",
        )


async def market_lifecycle_job() -> None:
    while True:
        store.close_expired_markets()
        if AUTO_RESOLVE_ENABLED:
            auto_resolve_markets()
        await asyncio.sleep(MARKET_LIFECYCLE_POLL_SECONDS)


@app.on_event("startup")
async def start_market_lifecycle_job() -> None:
    app.state.market_lifecycle_task = asyncio.create_task(market_lifecycle_job())


@app.on_event("shutdown")
async def stop_market_lifecycle_job() -> None:
    task = app.state.market_lifecycle_task
    task.cancel()
    with contextlib.suppress(asyncio.CancelledError):
        await task


def get_bot_or_404(bot_id: UUID) -> Bot:
    bot = store.bots.get(bot_id)
    if not bot:
        raise HTTPException(status_code=404, detail="Bot not found.")
    return bot


def get_market_or_404(market_id: UUID) -> Market:
    market = store.markets.get(market_id)
    if not market:
        raise HTTPException(status_code=404, detail="Market not found.")
    return market


def enforce_rate_limit(bot_id: UUID) -> None:
    entries = store.prune_bot_requests(bot_id, RATE_LIMIT_WINDOW_SECONDS)
    if len(entries) >= MAX_BOT_REQUESTS_PER_MINUTE:
        raise HTTPException(status_code=429, detail="Rate limit exceeded.")
    entries.append(store.now())


def validate_treasury_config(config: TreasuryConfig) -> None:
    if config.liquidity_bot_allocation_pct > 0 and not config.liquidity_bot_weights:
        raise HTTPException(
            status_code=400,
            detail="Liquidity bot weights are required when allocation is enabled.",
        )
    for bot_id, weight in config.liquidity_bot_weights.items():
        if weight <= 0:
            raise HTTPException(
                status_code=400,
                detail="Liquidity bot weights must be positive.",
            )
        get_bot_or_404(bot_id)


def authenticate_bot(
    *,
    action_bot_id: UUID,
    request_bot_id: UUID,
    api_key: str,
    require_min_stake: bool = False,
) -> Bot:
    if action_bot_id != request_bot_id:
        raise HTTPException(status_code=403, detail="Bot ID mismatch.")
    bot = get_bot_or_404(action_bot_id)
    if bot.api_key != api_key:
        raise HTTPException(status_code=401, detail="Invalid API key.")
    enforce_rate_limit(bot.id)
    if require_min_stake and (
        bot.wallet_balance_bdc < MIN_BOT_BALANCE_BDC
        and bot.reputation_score < MIN_BOT_REPUTATION_SCORE
    ):
        raise HTTPException(
            status_code=403,
            detail="Insufficient balance or reputation for this action.",
        )
    return bot


@app.post("/bots", response_model=Bot)
def create_bot(payload: BotCreateRequest) -> Bot:
    bot = Bot(
        name=payload.name,
        owner_id=payload.owner_id,
        api_key=secrets.token_urlsafe(32),
    )
    return store.add_bot(bot)


@app.get("/bots", response_model=List[Bot])
def list_bots() -> List[Bot]:
    return list(store.bots.values())


@app.post("/bots/{bot_id}/deposit", response_model=Bot)
def deposit_bdc(
    bot_id: UUID,
    payload: BotDepositRequest,
    api_key: str = Header(..., alias="X-API-Key"),
    request_bot_id: UUID = Header(..., alias="X-Bot-Id"),
) -> Bot:
    bot = authenticate_bot(
        action_bot_id=bot_id,
        request_bot_id=request_bot_id,
        api_key=api_key,
    )
    bot.wallet_balance_bdc += payload.amount_bdc
    store.add_ledger_entry(
        LedgerEntry(
            bot_id=bot.id,
            delta_bdc=payload.amount_bdc,
            reason=payload.reason,
            timestamp=store.now(),
        )
    )
    return bot


@app.post("/markets", response_model=Market)
def create_market(
    payload: MarketCreateRequest,
    api_key: str = Header(..., alias="X-API-Key"),
    request_bot_id: UUID = Header(..., alias="X-Bot-Id"),
) -> Market:
    creator = authenticate_bot(
        action_bot_id=payload.creator_bot_id,
        request_bot_id=request_bot_id,
        api_key=api_key,
        require_min_stake=True,
    )
    market = Market(
        creator_bot_id=creator.id,
        title=payload.title,
        description=payload.description,
        category=payload.category,
        outcomes=payload.outcomes,
        created_at=store.now(),
        closes_at=payload.closes_at,
        resolver_policy=payload.resolver_policy,
    )
    market = store.add_market(market)
    store.add_event(
        Event(
            event_type=EventType.market_created,
            market_id=market.id,
            bot_id=creator.id,
            payload={"title": market.title, "category": market.category},
            timestamp=market.created_at,
        )
    )
    return market


@app.get("/markets", response_model=List[Market])
def list_markets() -> List[Market]:
    store.close_expired_markets()
    return list(store.markets.values())


@app.get("/markets/{market_id}", response_model=Market)
def get_market(market_id: UUID) -> Market:
    store.close_expired_markets()
    return get_market_or_404(market_id)


@app.post("/markets/{market_id}/trades", response_model=TradeResponse)
def create_trade(
    market_id: UUID,
    payload: TradeCreateRequest,
    api_key: str = Header(..., alias="X-API-Key"),
    request_bot_id: UUID = Header(..., alias="X-Bot-Id"),
) -> TradeResponse:
    store.close_expired_markets()
    market = get_market_or_404(market_id)
    if market.status != MarketStatus.open:
        raise HTTPException(status_code=409, detail="Market is not open for trading.")
    bot = authenticate_bot(
        action_bot_id=payload.bot_id,
        request_bot_id=request_bot_id,
        api_key=api_key,
    )
    if payload.outcome_id not in market.outcomes:
        raise HTTPException(status_code=400, detail="Unknown outcome.")
    if bot.wallet_balance_bdc < payload.amount_bdc:
        raise HTTPException(status_code=400, detail="Insufficient balance.")
    bot.wallet_balance_bdc -= payload.amount_bdc
    market.outcome_pools[payload.outcome_id] += payload.amount_bdc
    total_pool = sum(market.outcome_pools.values())
    price = market.outcome_pools[payload.outcome_id] / total_pool if total_pool else 0.0
    trade = Trade(
        market_id=market.id,
        bot_id=bot.id,
        outcome_id=payload.outcome_id,
        amount_bdc=payload.amount_bdc,
        price=price,
        timestamp=store.now(),
    )
    store.add_trade(trade)
    store.add_event(
        Event(
            event_type=EventType.price_changed,
            market_id=market.id,
            bot_id=bot.id,
            payload={
                "outcome_id": trade.outcome_id,
                "price": trade.price,
                "amount_bdc": trade.amount_bdc,
            },
            timestamp=trade.timestamp,
        )
    )
    store.add_ledger_entry(
        LedgerEntry(
            bot_id=bot.id,
            market_id=market.id,
            delta_bdc=-payload.amount_bdc,
            reason="trade",
            timestamp=trade.timestamp,
        )
    )
    return TradeResponse(trade=trade, updated_market=market)


@app.post("/markets/{market_id}/discussion", response_model=DiscussionPost)
def create_discussion_post(
    market_id: UUID,
    payload: DiscussionPostCreateRequest,
    api_key: str = Header(..., alias="X-API-Key"),
    request_bot_id: UUID = Header(..., alias="X-Bot-Id"),
) -> DiscussionPost:
    market = get_market_or_404(market_id)
    bot = authenticate_bot(
        action_bot_id=payload.bot_id,
        request_bot_id=request_bot_id,
        api_key=api_key,
    )
    if payload.outcome_id not in market.outcomes:
        raise HTTPException(status_code=400, detail="Unknown outcome.")
    post = DiscussionPost(
        market_id=market.id,
        bot_id=bot.id,
        outcome_id=payload.outcome_id,
        body=payload.body,
        confidence=payload.confidence,
        timestamp=store.now(),
    )
    post = store.add_discussion(post)
    store.add_event(
        Event(
            event_type=EventType.discussion_posted,
            market_id=market.id,
            bot_id=bot.id,
            payload={
                "post_id": str(post.id),
                "outcome_id": post.outcome_id,
                "confidence": post.confidence,
            },
            timestamp=post.timestamp,
        )
    )
    return post


@app.get("/markets/{market_id}/discussion", response_model=List[DiscussionPost])
def list_discussion_posts(market_id: UUID) -> List[DiscussionPost]:
    get_market_or_404(market_id)
    return store.discussions.get(market_id, [])


@app.post("/markets/{market_id}/resolve", response_model=ResolveResponse)
def resolve_market(
    market_id: UUID,
    payload: ResolutionRequest,
    api_key: str = Header(..., alias="X-API-Key"),
    request_bot_id: UUID = Header(..., alias="X-Bot-Id"),
) -> ResolveResponse:
    store.close_expired_markets()
    market = get_market_or_404(market_id)
    if market.status == MarketStatus.resolved:
        raise HTTPException(status_code=409, detail="Market already resolved.")
    if len(set(payload.resolver_bot_ids)) != len(payload.resolver_bot_ids):
        raise HTTPException(status_code=400, detail="Duplicate resolver IDs provided.")
    if not payload.resolver_bot_ids:
        raise HTTPException(status_code=400, detail="At least one resolver is required.")
    if request_bot_id not in payload.resolver_bot_ids:
        raise HTTPException(status_code=403, detail="Resolver not authorized.")
    authenticate_bot(
        action_bot_id=request_bot_id,
        request_bot_id=request_bot_id,
        api_key=api_key,
        require_min_stake=True,
    )

    resolver_bots = {
        resolver_id: get_bot_or_404(resolver_id)
        for resolver_id in payload.resolver_bot_ids
    }

    resolved_outcome_id: str
    votes: List[ResolutionVote] = []

    if market.resolver_policy == ResolverPolicy.single:
        if len(payload.resolver_bot_ids) != 1:
            raise HTTPException(
                status_code=400, detail="Single resolver policy requires one resolver."
            )
        if not payload.resolved_outcome_id:
            raise HTTPException(
                status_code=400, detail="Resolved outcome is required for single policy."
            )
        if payload.resolved_outcome_id not in market.outcomes:
            raise HTTPException(status_code=400, detail="Unknown outcome.")
        resolved_outcome_id = payload.resolved_outcome_id
    else:
        if len(payload.resolver_bot_ids) < 2:
            raise HTTPException(
                status_code=400,
                detail="Majority and consensus policies require multiple resolvers.",
            )
        if not payload.votes:
            raise HTTPException(
                status_code=400, detail="Votes are required for this resolver policy."
            )
        if len(set(vote.resolver_bot_id for vote in payload.votes)) != len(
            payload.votes
        ):
            raise HTTPException(
                status_code=400, detail="Duplicate resolver votes provided."
            )
        missing_votes = set(payload.resolver_bot_ids) - {
            vote.resolver_bot_id for vote in payload.votes
        }
        if missing_votes:
            raise HTTPException(
                status_code=400,
                detail="Votes are required from all listed resolvers.",
            )
        for vote in payload.votes:
            if vote.resolver_bot_id not in resolver_bots:
                raise HTTPException(
                    status_code=400, detail="Vote provided by unknown resolver."
                )
            if vote.outcome_id not in market.outcomes:
                raise HTTPException(status_code=400, detail="Unknown outcome.")
        votes = payload.votes
        if market.resolver_policy == ResolverPolicy.majority:
            outcome_counts: dict[str, int] = {}
            for vote in votes:
                outcome_counts[vote.outcome_id] = (
                    outcome_counts.get(vote.outcome_id, 0) + 1
                )
            resolved_outcome_id, max_count = max(
                outcome_counts.items(), key=lambda item: item[1]
            )
            if max_count <= len(votes) / 2:
                raise HTTPException(
                    status_code=409, detail="No majority consensus reached."
                )
        else:
            outcome_weights: dict[str, float] = {}
            total_weight = 0.0
            for vote in votes:
                weight = resolver_bots[vote.resolver_bot_id].reputation_score
                total_weight += weight
                outcome_weights[vote.outcome_id] = (
                    outcome_weights.get(vote.outcome_id, 0.0) + weight
                )
            if total_weight <= 0:
                raise HTTPException(
                    status_code=400,
                    detail="Consensus policy requires positive resolver reputation.",
                )
            resolved_outcome_id, max_weight = max(
                outcome_weights.items(), key=lambda item: item[1]
            )
            if max_weight <= total_weight / 2:
                raise HTTPException(
                    status_code=409, detail="No consensus reached."
                )
        if (
            payload.resolved_outcome_id
            and payload.resolved_outcome_id != resolved_outcome_id
        ):
            raise HTTPException(
                status_code=400,
                detail="Resolved outcome does not match resolver votes.",
            )

    return settle_market_resolution(
        market=market,
        resolved_outcome_id=resolved_outcome_id,
        resolver_bot_ids=payload.resolver_bot_ids,
        actor_bot_id=request_bot_id,
        evidence=payload.evidence,
        votes=votes,
    )


@app.get("/bots/{bot_id}/ledger", response_model=List[LedgerEntry])
def list_ledger(bot_id: UUID) -> List[LedgerEntry]:
    get_bot_or_404(bot_id)
    return store.ledger.get(bot_id, [])


@app.get("/markets/{market_id}/trades", response_model=List[Trade])
def list_trades(market_id: UUID) -> List[Trade]:
    get_market_or_404(market_id)
    return store.trades.get(market_id, [])


@app.post("/bots/{bot_id}/webhooks", response_model=WebhookRegistration)
def register_webhook(
    bot_id: UUID,
    payload: WebhookRegistrationRequest,
    api_key: str = Header(..., alias="X-API-Key"),
    request_bot_id: UUID = Header(..., alias="X-Bot-Id"),
) -> WebhookRegistration:
    bot = authenticate_bot(
        action_bot_id=bot_id,
        request_bot_id=request_bot_id,
        api_key=api_key,
    )
    webhook = WebhookRegistration(
        bot_id=bot.id,
        url=payload.url,
        event_types=payload.event_types,
        created_at=store.now(),
    )
    return store.add_webhook(webhook)


@app.get("/events/outbox", response_model=List[OutboxEntry])
def list_outbox() -> List[OutboxEntry]:
    return store.outbox


@app.get("/treasury", response_model=TreasuryState)
def get_treasury_state() -> TreasuryState:
    return TreasuryState(
        balance_bdc=store.treasury_balance_bdc,
        config=store.treasury_config,
    )


@app.get("/treasury/ledger", response_model=List[TreasuryLedgerEntry])
def list_treasury_ledger() -> List[TreasuryLedgerEntry]:
    return store.treasury_ledger


@app.put("/treasury/config", response_model=TreasuryConfig)
def update_treasury_config(
    payload: TreasuryConfig,
    api_key: str = Header(..., alias="X-API-Key"),
    request_bot_id: UUID = Header(..., alias="X-Bot-Id"),
) -> TreasuryConfig:
    authenticate_bot(
        action_bot_id=request_bot_id,
        request_bot_id=request_bot_id,
        api_key=api_key,
        require_min_stake=True,
    )
    validate_treasury_config(payload)
    store.treasury_config = payload
    return store.treasury_config
