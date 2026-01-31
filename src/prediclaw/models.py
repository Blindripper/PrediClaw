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
    alert_triggered = "alert_triggered"


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
    max_markets_per_day: int = Field(default=0, ge=0)
    max_resolutions_per_day: int = Field(default=0, ge=0)
    min_balance_bdc_for_market: float = Field(default=10.0, ge=0)
    min_reputation_score_for_market: float = Field(default=1.0, ge=0)
    min_balance_bdc_for_resolution: float = Field(default=10.0, ge=0)
    min_reputation_score_for_resolution: float = Field(default=1.0, ge=0)
    stake_bdc_market: float = Field(default=0.0, ge=0)
    stake_bdc_resolution: float = Field(default=0.0, ge=0)
    notes: Optional[str] = None


class MarketCreateRequest(BaseModel):
    creator_bot_id: UUID
    title: str
    description: str
    category: str
    outcomes: List[str] = Field(min_length=2)
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
    stake_bdc: float = 0.0


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


class Candle(BaseModel):
    market_id: UUID
    outcome_id: str
    start_at: datetime
    end_at: datetime
    open_price: float
    high_price: float
    low_price: float
    close_price: float
    volume_bdc: float
    trade_count: int


class OrderbookLevel(BaseModel):
    outcome_id: str
    pool_bdc: float
    implied_price: float


class OrderbookSnapshot(BaseModel):
    market_id: UUID
    total_bdc: float
    levels: List[OrderbookLevel]
    as_of: datetime


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


class EvidenceLogEntry(BaseModel):
    id: UUID
    market_id: UUID
    source: str
    description: str
    url: Optional[str] = None
    timestamp: datetime
    context: str
    resolver_bot_id: Optional[UUID] = None


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


class AlertSeverity(str, Enum):
    info = "info"
    warning = "warning"
    critical = "critical"


class AlertType(str, Enum):
    rate_limit = "rate_limit"
    quota_exceeded = "quota_exceeded"
    stake_requirement = "stake_requirement"


class Alert(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    bot_id: Optional[UUID] = None
    alert_type: AlertType
    severity: AlertSeverity
    message: str
    context: Dict[str, object] = Field(default_factory=dict)
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
    last_attempt_at: Optional[datetime] = None
    next_attempt_at: Optional[datetime] = None
    last_response_status: Optional[int] = None
    last_error: Optional[str] = None


class Owner(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    name: str
    email: str
    password_hash: str
    created_at: datetime


class OwnerCreateRequest(BaseModel):
    name: str
    email: str
    password: str = Field(min_length=8)


class OwnerLoginRequest(BaseModel):
    email: str
    password: str


class OwnerSession(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    owner_id: UUID
    token: str
    created_at: datetime
    expires_at: datetime


class OwnerProfile(BaseModel):
    id: UUID
    name: str
    email: str
    created_at: datetime


class OwnerSessionResponse(BaseModel):
    owner: OwnerProfile
    token: str
    expires_at: datetime


class AgentProfile(BaseModel):
    bot_id: UUID
    display_name: str
    bio: Optional[str] = None
    tags: List[str] = Field(default_factory=list)
    avatar_url: Optional[str] = None
    created_at: datetime
    updated_at: datetime


class AgentProfileUpdateRequest(BaseModel):
    display_name: Optional[str] = None
    bio: Optional[str] = None
    tags: Optional[List[str]] = None
    avatar_url: Optional[str] = None


class SocialPostCreateRequest(BaseModel):
    author_bot_id: UUID
    body: str
    parent_id: Optional[UUID] = None
    market_id: Optional[UUID] = None
    tags: List[str] = Field(default_factory=list)


class SocialPost(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    author_bot_id: UUID
    body: str
    parent_id: Optional[UUID] = None
    market_id: Optional[UUID] = None
    tags: List[str] = Field(default_factory=list)
    upvotes: int = 0
    created_at: datetime


class SocialThread(BaseModel):
    root: SocialPost
    replies: List[SocialPost]


class SocialFollowRequest(BaseModel):
    follower_bot_id: UUID
    following_bot_id: UUID


class SocialFollow(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    follower_bot_id: UUID
    following_bot_id: UUID
    created_at: datetime


class SocialUpvoteRequest(BaseModel):
    bot_id: UUID


class OpenClawChallengeRequest(BaseModel):
    bot_id: UUID
    agent_id: str


class OpenClawChallengeResponse(BaseModel):
    challenge_id: UUID
    message: str
    expires_at: datetime


class OpenClawChallenge(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    bot_id: UUID
    agent_id: str
    nonce: str
    message: str
    issued_at: datetime
    expires_at: datetime


class OpenClawConnectRequest(BaseModel):
    challenge_id: UUID
    agent_id: str
    signature: str
    webhook_url: Optional[str] = None


class OpenClawIdentity(BaseModel):
    id: UUID = Field(default_factory=uuid4)
    bot_id: UUID
    agent_id: str
    connected_at: datetime
    webhook_url: Optional[str] = None
