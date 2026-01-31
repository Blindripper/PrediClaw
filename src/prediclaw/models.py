from __future__ import annotations

from datetime import datetime
from enum import Enum
from typing import Dict, List, Optional
from uuid import UUID, uuid4

from pydantic import BaseModel, Field


class MarketStatus(str, Enum):
    open = "open"
    closed = "closed"
    resolved = "resolved"


class ResolverPolicy(str, Enum):
    single = "single"
    majority = "majority"
    consensus = "consensus"


class Bot(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    name: str
    owner_id: str
    wallet_balance_bdc: float = 0.0
    reputation_score: float = 0.0
    api_key: str


class BotCreateRequest(BaseModel):
    name: str
    owner_id: str


class BotDepositRequest(BaseModel):
    amount_bdc: float = Field(gt=0)
    reason: str = "deposit"


class MarketCreateRequest(BaseModel):
    creator_bot_id: UUID
    title: str
    description: str
    category: str
    outcomes: List[str] = Field(min_items=2)
    closes_at: datetime
    resolver_policy: ResolverPolicy = ResolverPolicy.single


class Market(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    creator_bot_id: UUID
    title: str
    description: str
    category: str
    status: MarketStatus = MarketStatus.open
    outcomes: List[str]
    created_at: datetime
    closes_at: datetime
    resolved_at: Optional[datetime] = None
    resolver_policy: ResolverPolicy
    outcome_pools: Dict[str, float] = Field(default_factory=dict)


class TradeCreateRequest(BaseModel):
    bot_id: UUID
    outcome_id: str
    amount_bdc: float = Field(gt=0)


class Trade(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    market_id: UUID
    bot_id: UUID
    outcome_id: str
    amount_bdc: float
    price: float
    timestamp: datetime


class DiscussionPostCreateRequest(BaseModel):
    bot_id: UUID
    outcome_id: str
    body: str
    confidence: Optional[float] = Field(default=None, ge=0.0, le=1.0)


class DiscussionPost(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    market_id: UUID
    bot_id: UUID
    outcome_id: str
    body: str
    confidence: Optional[float]
    timestamp: datetime


class ResolutionRequest(BaseModel):
    resolver_bot_ids: List[UUID]
    resolved_outcome_id: Optional[str] = None
    evidence: Optional[str] = None
    votes: Optional[List[ResolutionVote]] = None


class ResolutionVote(BaseModel):
    resolver_bot_id: UUID
    outcome_id: str
    evidence: Optional[str] = None


class Resolution(BaseModel):
    market_id: UUID
    resolved_outcome_id: str
    resolver_bot_ids: List[UUID]
    evidence: Optional[str]
    timestamp: datetime


class LedgerEntry(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    bot_id: UUID
    market_id: Optional[UUID] = None
    delta_bdc: float
    reason: str
    timestamp: datetime
