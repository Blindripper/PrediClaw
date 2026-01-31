from __future__ import annotations

from datetime import UTC, datetime
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


class EventType(str, Enum):
    market_created = "market_created"
    price_changed = "price_changed"
    discussion_posted = "discussion_posted"
    market_closed = "market_closed"
    market_resolved = "market_resolved"
    bot_status_changed = "bot_status_changed"


class BotStatus(str, Enum):
    inactive = "inactive"
    active = "active"
    paused = "paused"


class Bot(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    name: str
    owner_id: str
    wallet_balance_bdc: float = 0.0
    reputation_score: float = 0.0
    api_key: str
    status: BotStatus = BotStatus.inactive


class BotCreateRequest(BaseModel):
    name: str
    owner_id: str


class BotDepositRequest(BaseModel):
    amount_bdc: float = Field(gt=0)
    reason: str = "deposit"


class BotPolicy(BaseModel):
    status: BotStatus = BotStatus.inactive
    max_requests_per_minute: int = Field(default=60, ge=1)
    max_active_markets: int = Field(default=5, ge=0)
    max_trade_bdc: float = Field(default=500.0, ge=0)
    notes: Optional[str] = None


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
    evidence: Optional[List["EvidenceItem"]] = None
    votes: Optional[List[ResolutionVote]] = None


class ResolutionVote(BaseModel):
    resolver_bot_id: UUID
    outcome_id: str
    evidence: Optional[List["EvidenceItem"]] = None


class EvidenceItem(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    source: str
    description: str
    url: Optional[str] = None
    timestamp: datetime = Field(default_factory=lambda: datetime.now(tz=UTC))


class Resolution(BaseModel):
    market_id: UUID
    resolved_outcome_id: str
    resolver_bot_ids: List[UUID]
    evidence: List[EvidenceItem] = Field(default_factory=list)
    timestamp: datetime


class LedgerEntry(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    bot_id: UUID
    market_id: Optional[UUID] = None
    delta_bdc: float
    reason: str
    timestamp: datetime


class TreasuryConfig(BaseModel):
    send_unpaid_to_treasury: bool = True
    liquidity_bot_allocation_pct: float = Field(default=0.0, ge=0.0, le=1.0)
    liquidity_bot_weights: Dict[UUID, float] = Field(default_factory=dict)


class TreasuryLedgerEntry(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    market_id: Optional[UUID] = None
    delta_bdc: float
    reason: str
    timestamp: datetime


class TreasuryState(BaseModel):
    balance_bdc: float
    config: TreasuryConfig


class Event(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    event_type: EventType
    market_id: Optional[UUID] = None
    bot_id: Optional[UUID] = None
    payload: Dict[str, object] = Field(default_factory=dict)
    timestamp: datetime


class BotConfig(BaseModel):
    webhook_url: Optional[str] = None
    event_subscriptions: List[EventType] = Field(default_factory=list)
    alert_balance_threshold_bdc: float = Field(default=10.0, ge=0)


class WebhookRegistrationRequest(BaseModel):
    url: str
    event_types: List[EventType] = Field(default_factory=list)


class WebhookRegistration(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    bot_id: UUID
    url: str
    event_types: List[EventType] = Field(default_factory=list)
    created_at: datetime


class OutboxEntry(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    webhook_id: UUID
    event_id: UUID
    event_type: EventType
    target_url: str
    status: str
    attempts: int = 0
    created_at: datetime
