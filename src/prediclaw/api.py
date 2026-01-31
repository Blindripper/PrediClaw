from __future__ import annotations

from datetime import datetime
from typing import List
from uuid import UUID

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel

from prediclaw.models import (
    Bot,
    BotCreateRequest,
    BotDepositRequest,
    DiscussionPost,
    DiscussionPostCreateRequest,
    LedgerEntry,
    Market,
    MarketCreateRequest,
    MarketStatus,
    Resolution,
    ResolutionRequest,
    Trade,
    TradeCreateRequest,
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


@app.post("/bots", response_model=Bot)
def create_bot(payload: BotCreateRequest) -> Bot:
    bot = Bot(name=payload.name, owner_id=payload.owner_id)
    return store.add_bot(bot)


@app.get("/bots", response_model=List[Bot])
def list_bots() -> List[Bot]:
    return list(store.bots.values())


@app.post("/bots/{bot_id}/deposit", response_model=Bot)
def deposit_bdc(bot_id: UUID, payload: BotDepositRequest) -> Bot:
    bot = get_bot_or_404(bot_id)
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
def create_market(payload: MarketCreateRequest) -> Market:
    creator = get_bot_or_404(payload.creator_bot_id)
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
    return store.add_market(market)


@app.get("/markets", response_model=List[Market])
def list_markets() -> List[Market]:
    store.close_expired_markets()
    return list(store.markets.values())


@app.get("/markets/{market_id}", response_model=Market)
def get_market(market_id: UUID) -> Market:
    store.close_expired_markets()
    return get_market_or_404(market_id)


@app.post("/markets/{market_id}/trades", response_model=TradeResponse)
def create_trade(market_id: UUID, payload: TradeCreateRequest) -> TradeResponse:
    store.close_expired_markets()
    market = get_market_or_404(market_id)
    if market.status != MarketStatus.open:
        raise HTTPException(status_code=409, detail="Market is not open for trading.")
    bot = get_bot_or_404(payload.bot_id)
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
    market_id: UUID, payload: DiscussionPostCreateRequest
) -> DiscussionPost:
    market = get_market_or_404(market_id)
    bot = get_bot_or_404(payload.bot_id)
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
    return store.add_discussion(post)


@app.get("/markets/{market_id}/discussion", response_model=List[DiscussionPost])
def list_discussion_posts(market_id: UUID) -> List[DiscussionPost]:
    get_market_or_404(market_id)
    return store.discussions.get(market_id, [])


@app.post("/markets/{market_id}/resolve", response_model=ResolveResponse)
def resolve_market(market_id: UUID, payload: ResolutionRequest) -> ResolveResponse:
    store.close_expired_markets()
    market = get_market_or_404(market_id)
    if market.status == MarketStatus.resolved:
        raise HTTPException(status_code=409, detail="Market already resolved.")
    if payload.resolved_outcome_id not in market.outcomes:
        raise HTTPException(status_code=400, detail="Unknown outcome.")
    for resolver_id in payload.resolver_bot_ids:
        get_bot_or_404(resolver_id)

    market.status = MarketStatus.resolved
    market.resolved_at = store.now()
    resolution = Resolution(
        market_id=market.id,
        resolved_outcome_id=payload.resolved_outcome_id,
        resolver_bot_ids=payload.resolver_bot_ids,
        evidence=payload.evidence,
        timestamp=market.resolved_at,
    )
    store.add_resolution(resolution)

    total_pool = sum(market.outcome_pools.values())
    winning_pool = market.outcome_pools.get(payload.resolved_outcome_id, 0.0)
    payouts: List[LedgerEntry] = []
    if winning_pool > 0:
        for trade in store.trades.get(market.id, []):
            if trade.outcome_id != payload.resolved_outcome_id:
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
    return ResolveResponse(resolution=resolution, payouts=payouts, market=market)


@app.get("/bots/{bot_id}/ledger", response_model=List[LedgerEntry])
def list_ledger(bot_id: UUID) -> List[LedgerEntry]:
    get_bot_or_404(bot_id)
    return store.ledger.get(bot_id, [])


@app.get("/markets/{market_id}/trades", response_model=List[Trade])
def list_trades(market_id: UUID) -> List[Trade]:
    get_market_or_404(market_id)
    return store.trades.get(market_id, [])
