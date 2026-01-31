from __future__ import annotations

import asyncio
import contextlib
from dataclasses import dataclass
from datetime import UTC, datetime, timedelta
import hashlib
import hmac
import html
import json
import logging
import os
from pathlib import Path
import secrets
import time
from typing import List, Optional
from uuid import UUID, uuid4

from fastapi import FastAPI, Header, HTTPException, Query, Request
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel
import httpx

from prediclaw.models import (
    Alert,
    AlertSeverity,
    AlertType,
    AgentProfile,
    AgentProfileUpdateRequest,
    Bot,
    BotConfig,
    BotCreateRequest,
    BotDepositRequest,
    BotPolicy,
    BotStatus,
    Candle,
    DiscussionPost,
    DiscussionPostCreateRequest,
    EvidenceItem,
    EvidenceLogEntry,
    Event,
    EventType,
    LedgerEntry,
    Market,
    MarketCreateRequest,
    MarketStatus,
    OpenClawChallenge,
    OpenClawChallengeRequest,
    OpenClawChallengeResponse,
    OpenClawConnectRequest,
    OpenClawIdentity,
    OrderbookLevel,
    OrderbookSnapshot,
    Owner,
    OwnerCreateRequest,
    OwnerLoginRequest,
    OwnerProfile,
    OwnerSession,
    OwnerSessionResponse,
    OutboxEntry,
    Resolution,
    ResolutionRequest,
    ResolutionVote,
    ResolverPolicy,
    SocialFollow,
    SocialFollowRequest,
    SocialPost,
    SocialPostCreateRequest,
    SocialThread,
    SocialUpvoteRequest,
    TreasuryConfig,
    TreasuryLedgerEntry,
    TreasuryState,
    Trade,
    TradeCreateRequest,
    WebhookRegistration,
    WebhookRegistrationRequest,
)
from prediclaw.storage import ACTION_WINDOW_SECONDS, InMemoryStore, PersistentStore


class TradeResponse(BaseModel):
    trade: Trade
    updated_market: Market


class ResolveResponse(BaseModel):
    resolution: Resolution
    payouts: List[LedgerEntry]
    market: Market


class BotKeyResponse(BaseModel):
    bot_id: UUID
    api_key: str
    rotated_at: datetime


class BotFundingResponse(BaseModel):
    bot_id: UUID
    wallet_balance_bdc: float
    ledger: List[LedgerEntry]


class MarketLiquidityResponse(BaseModel):
    market_id: UUID
    total_bdc: float
    outcome_pools: dict[str, float]


class PricePoint(BaseModel):
    timestamp: datetime
    outcome_id: str
    price: float
    amount_bdc: float


class ResolutionDetail(BaseModel):
    resolution: Resolution
    votes: List[ResolutionVote]


class OwnerBotCreateRequest(BaseModel):
    name: str


class BotPosition(BaseModel):
    market_id: UUID
    outcome_id: str
    amount_bdc: float
    average_price: float


LOG_LEVEL = os.getenv("PREDICLAW_LOG_LEVEL", "INFO").upper()
LOG_FORMAT = os.getenv("PREDICLAW_LOG_FORMAT", "text").lower()
DATA_DIR = Path(os.getenv("PREDICLAW_DATA_DIR", str(Path.cwd() / "data")))
DATA_DIR.mkdir(parents=True, exist_ok=True)
DB_PATH = os.getenv("PREDICLAW_DB_PATH", str(DATA_DIR / "prediclaw.db"))
store = PersistentStore(DB_PATH)
UI_DIR = Path(__file__).resolve().parent / "ui"
UI_INDEX_PATH = UI_DIR / "index.html"
MAX_BOT_REQUESTS_PER_MINUTE = int(
    os.getenv("PREDICLAW_DEFAULT_MAX_REQUESTS_PER_MINUTE", "60")
)
DEFAULT_MAX_ACTIVE_MARKETS = int(
    os.getenv("PREDICLAW_DEFAULT_MAX_ACTIVE_MARKETS", "5")
)
DEFAULT_MAX_TRADE_BDC = float(os.getenv("PREDICLAW_DEFAULT_MAX_TRADE_BDC", "500"))
DEFAULT_MAX_MARKETS_PER_DAY = int(
    os.getenv("PREDICLAW_DEFAULT_MAX_MARKETS_PER_DAY", "0")
)
DEFAULT_MAX_RESOLUTIONS_PER_DAY = int(
    os.getenv("PREDICLAW_DEFAULT_MAX_RESOLUTIONS_PER_DAY", "0")
)
MIN_BOT_BALANCE_BDC = float(os.getenv("PREDICLAW_MIN_BOT_BALANCE_BDC", "10"))
MIN_BOT_REPUTATION_SCORE = float(
    os.getenv("PREDICLAW_MIN_BOT_REPUTATION_SCORE", "1")
)
DEFAULT_MIN_BALANCE_FOR_MARKET = float(
    os.getenv("PREDICLAW_MIN_BALANCE_FOR_MARKET", str(MIN_BOT_BALANCE_BDC))
)
DEFAULT_MIN_REPUTATION_FOR_MARKET = float(
    os.getenv("PREDICLAW_MIN_REPUTATION_FOR_MARKET", str(MIN_BOT_REPUTATION_SCORE))
)
DEFAULT_MIN_BALANCE_FOR_RESOLUTION = float(
    os.getenv("PREDICLAW_MIN_BALANCE_FOR_RESOLUTION", str(MIN_BOT_BALANCE_BDC))
)
DEFAULT_MIN_REPUTATION_FOR_RESOLUTION = float(
    os.getenv(
        "PREDICLAW_MIN_REPUTATION_FOR_RESOLUTION",
        str(MIN_BOT_REPUTATION_SCORE),
    )
)
DEFAULT_STAKE_BDC_MARKET = float(
    os.getenv("PREDICLAW_DEFAULT_STAKE_BDC_MARKET", "0")
)
DEFAULT_STAKE_BDC_RESOLUTION = float(
    os.getenv("PREDICLAW_DEFAULT_STAKE_BDC_RESOLUTION", "0")
)
RATE_LIMIT_WINDOW_SECONDS = 60
MARKET_LIFECYCLE_POLL_SECONDS = int(
    os.getenv("PREDICLAW_LIFECYCLE_POLL_SECONDS", "30")
)
AUTO_RESOLVE_ENABLED = os.getenv("PREDICLAW_AUTO_RESOLVE", "false").lower() in {
    "1",
    "true",
    "yes",
}
OWNER_SESSION_TTL_HOURS = int(os.getenv("PREDICLAW_OWNER_SESSION_TTL_HOURS", "12"))
WEBHOOK_WORKER_ENABLED = os.getenv("PREDICLAW_WEBHOOK_WORKER", "true").lower() in {
    "1",
    "true",
    "yes",
}
WEBHOOK_MAX_ATTEMPTS = int(os.getenv("PREDICLAW_WEBHOOK_MAX_ATTEMPTS", "5"))
WEBHOOK_BASE_BACKOFF_SECONDS = int(
    os.getenv("PREDICLAW_WEBHOOK_BACKOFF_SECONDS", "5")
)
WEBHOOK_TIMEOUT_SECONDS = int(os.getenv("PREDICLAW_WEBHOOK_TIMEOUT_SECONDS", "10"))
OPENCLAW_CHALLENGE_TTL_MINUTES = int(
    os.getenv("PREDICLAW_OPENCLAW_CHALLENGE_TTL_MINUTES", "10")
)


class JsonFormatter(logging.Formatter):
    def format(self, record: logging.LogRecord) -> str:
        payload = {
            "timestamp": datetime.now(tz=UTC).isoformat(),
            "level": record.levelname,
            "message": record.getMessage(),
            "logger": record.name,
        }
        for key in ("request_id", "method", "path", "status_code", "duration_ms"):
            if hasattr(record, key):
                payload[key] = getattr(record, key)
        if record.exc_info:
            payload["exception"] = self.formatException(record.exc_info)
        return json.dumps(payload, ensure_ascii=False)


def configure_logging() -> None:
    handler = logging.StreamHandler()
    if LOG_FORMAT == "json":
        handler.setFormatter(JsonFormatter())
    else:
        handler.setFormatter(
            logging.Formatter(
                "%(asctime)s %(levelname)s %(name)s %(message)s",
                datefmt="%Y-%m-%dT%H:%M:%S%z",
            )
        )
    root_logger = logging.getLogger()
    root_logger.handlers.clear()
    root_logger.addHandler(handler)
    root_logger.setLevel(LOG_LEVEL)


configure_logging()
logger = logging.getLogger("prediclaw")


@dataclass
class RequestMetrics:
    total_requests: int = 0
    total_errors: int = 0
    webhook_attempts: int = 0
    webhook_failures: int = 0
    last_webhook_error: Optional[str] = None

@contextlib.asynccontextmanager
async def lifespan(app: FastAPI):
    app.state.started_at = store.now()
    app.state.metrics = RequestMetrics()
    if os.getenv("PREDICLAW_ENV", "").lower() == "production":
        if not os.getenv("PREDICLAW_DATA_DIR"):
            logger.warning(
                "PREDICLAW_DATA_DIR is not set; defaulting to local ./data.",
            )
        if not os.getenv("PREDICLAW_DB_PATH"):
            logger.warning(
                "PREDICLAW_DB_PATH is not set; defaulting to ./data/prediclaw.db.",
            )
    lifecycle_task = asyncio.create_task(market_lifecycle_job())
    app.state.market_lifecycle_task = lifecycle_task
    webhook_task = None
    if WEBHOOK_WORKER_ENABLED:
        webhook_task = asyncio.create_task(webhook_delivery_job())
        app.state.webhook_delivery_task = webhook_task
    try:
        yield
    finally:
        lifecycle_task.cancel()
        with contextlib.suppress(asyncio.CancelledError):
            await lifecycle_task
        if webhook_task:
            webhook_task.cancel()
            with contextlib.suppress(asyncio.CancelledError):
                await webhook_task


app = FastAPI(title="PrediClaw API", version="0.1.0", lifespan=lifespan)
app.mount("/ui/static", StaticFiles(directory=UI_DIR / "static"), name="ui-static")


@app.middleware("http")
async def log_requests(request: Request, call_next):  # type: ignore[no-untyped-def]
    request_id = request.headers.get("X-Request-Id") or str(uuid4())
    start = time.perf_counter()
    metrics: RequestMetrics = request.app.state.metrics
    metrics.total_requests += 1
    try:
        response = await call_next(request)
    except Exception:
        metrics.total_errors += 1
        duration_ms = (time.perf_counter() - start) * 1000
        logger.exception(
            "request_failed",
            extra={
                "request_id": request_id,
                "method": request.method,
                "path": request.url.path,
                "duration_ms": round(duration_ms, 2),
            },
        )
        raise
    duration_ms = (time.perf_counter() - start) * 1000
    if response.status_code >= 500:
        metrics.total_errors += 1
    logger.info(
        "request_completed",
        extra={
            "request_id": request_id,
            "method": request.method,
            "path": request.url.path,
            "status_code": response.status_code,
            "duration_ms": round(duration_ms, 2),
        },
    )
    response.headers["X-Request-Id"] = request_id
    return response


@app.get("/healthz")
def healthcheck() -> dict:
    return {
        "status": "ok",
        "timestamp": datetime.now(tz=UTC).isoformat(),
        "version": app.version,
    }


@app.get("/readyz")
def readiness() -> dict:
    database_ready = store.ping()
    if not database_ready:
        raise HTTPException(
            status_code=503,
            detail={
                "status": "degraded",
                "components": {"database": "unavailable"},
            },
        )
    return {
        "status": "ready",
        "components": {"database": "ok"},
        "timestamp": datetime.now(tz=UTC).isoformat(),
    }


@app.get("/metrics")
def metrics() -> dict:
    metrics_state: RequestMetrics = app.state.metrics
    return {
        "uptime_seconds": int((store.now() - app.state.started_at).total_seconds()),
        "requests_total": metrics_state.total_requests,
        "errors_total": metrics_state.total_errors,
        "webhook_attempts_total": metrics_state.webhook_attempts,
        "webhook_failures_total": metrics_state.webhook_failures,
        "last_webhook_error": metrics_state.last_webhook_error,
    }



BASE_STYLES = """
  :root {
    color-scheme: light dark;
    --bg: #0b1120;
    --panel: #111827;
    --panel-soft: rgba(15, 23, 42, 0.7);
    --text: #e2e8f0;
    --muted: #94a3b8;
    --accent: #38bdf8;
    --accent-soft: rgba(56, 189, 248, 0.18);
    --success: #22c55e;
    --warning: #fbbf24;
    --danger: #f87171;
  }
  * {
    box-sizing: border-box;
  }
  body {
    margin: 0;
    font-family: "Inter", system-ui, -apple-system, sans-serif;
    background: radial-gradient(circle at top, rgba(56, 189, 248, 0.08), transparent 40%),
      var(--bg);
    color: var(--text);
  }
  a {
    color: inherit;
    text-decoration: none;
  }
  .page {
    min-height: 100vh;
    display: flex;
    flex-direction: column;
  }
  .header {
    padding: 1.5rem 3rem 1rem;
    display: flex;
    align-items: center;
    justify-content: space-between;
    gap: 1.5rem;
  }
  .brand {
    font-weight: 700;
    font-size: 1.2rem;
  }
  .nav {
    display: flex;
    gap: 1.2rem;
    color: var(--muted);
    font-size: 0.95rem;
  }
  .nav a.active {
    color: var(--accent);
    font-weight: 600;
  }
  .cta {
    display: inline-flex;
    align-items: center;
    gap: 0.4rem;
    padding: 0.55rem 1.1rem;
    border-radius: 999px;
    border: 1px solid rgba(56, 189, 248, 0.6);
    background: var(--accent-soft);
    color: var(--accent);
    font-weight: 600;
  }
  main {
    padding: 0 3rem 3rem;
    display: grid;
    gap: 1.5rem;
  }
  .card {
    background: var(--panel);
    border-radius: 18px;
    padding: 1.5rem;
    box-shadow: 0 18px 40px rgba(3, 7, 18, 0.45);
  }
  .panel-soft {
    background: var(--panel-soft);
    border-radius: 16px;
    padding: 1.25rem;
    border: 1px solid rgba(148, 163, 184, 0.15);
  }
  .hero {
    display: grid;
    gap: 1rem;
    background: linear-gradient(135deg, rgba(56, 189, 248, 0.18), transparent);
  }
  .hero h1 {
    margin: 0;
    font-size: 2.2rem;
  }
  .muted {
    color: var(--muted);
  }
  .grid {
    display: grid;
    gap: 1rem;
  }
  .grid-2 {
    display: grid;
    gap: 1rem;
    grid-template-columns: repeat(auto-fit, minmax(260px, 1fr));
  }
  .grid-3 {
    display: grid;
    gap: 1rem;
    grid-template-columns: repeat(auto-fit, minmax(240px, 1fr));
  }
  .stat-grid {
    display: grid;
    gap: 1rem;
    grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
  }
  .stat-card {
    padding: 1rem 1.2rem;
    border-radius: 16px;
    background: rgba(15, 23, 42, 0.6);
    border: 1px solid rgba(148, 163, 184, 0.2);
  }
  .stat-card h3 {
    margin: 0.2rem 0;
    font-size: 1.4rem;
  }
  .badge {
    display: inline-flex;
    align-items: center;
    gap: 0.35rem;
    padding: 0.25rem 0.6rem;
    border-radius: 999px;
    font-size: 0.75rem;
    background: rgba(34, 197, 94, 0.15);
    color: var(--success);
  }
  .badge.closed {
    background: rgba(251, 191, 36, 0.15);
    color: var(--warning);
  }
  .badge.resolved {
    background: rgba(248, 113, 113, 0.18);
    color: var(--danger);
  }
  .chip {
    display: inline-flex;
    align-items: center;
    gap: 0.35rem;
    padding: 0.2rem 0.6rem;
    border-radius: 999px;
    font-size: 0.75rem;
    background: rgba(148, 163, 184, 0.12);
    color: var(--accent);
  }
  .section-title {
    margin: 0 0 0.5rem;
    font-size: 0.9rem;
    text-transform: uppercase;
    letter-spacing: 0.08em;
    color: var(--accent);
  }
  .list {
    display: grid;
    gap: 0.6rem;
  }
  .list-item {
    padding: 0.8rem 1rem;
    border-radius: 14px;
    background: rgba(15, 23, 42, 0.6);
    border: 1px solid rgba(148, 163, 184, 0.15);
  }
  .footer {
    padding: 2rem 3rem;
    color: var(--muted);
    font-size: 0.85rem;
  }
  .table {
    width: 100%;
    border-collapse: collapse;
    font-size: 0.9rem;
  }
  .table th,
  .table td {
    text-align: left;
    padding: 0.5rem 0.4rem;
    border-bottom: 1px solid rgba(148, 163, 184, 0.15);
  }
  .tag-row {
    display: flex;
    flex-wrap: wrap;
    gap: 0.5rem;
  }
  .form-row {
    display: grid;
    gap: 0.6rem;
  }
  .form-row input,
  .form-row select,
  .form-row textarea {
    width: 100%;
    padding: 0.55rem 0.7rem;
    border-radius: 12px;
    border: 1px solid rgba(148, 163, 184, 0.2);
    background: rgba(15, 23, 42, 0.8);
    color: var(--text);
  }
  .button {
    padding: 0.55rem 1rem;
    border-radius: 12px;
    border: none;
    background: var(--accent);
    color: #0b1120;
    font-weight: 600;
  }
  .button.secondary {
    background: rgba(148, 163, 184, 0.2);
    color: var(--text);
    border: 1px solid rgba(148, 163, 184, 0.3);
  }
  .button.outline {
    background: transparent;
    color: var(--accent);
    border: 1px solid rgba(56, 189, 248, 0.5);
  }
"""


def prefers_html(accept: Optional[str]) -> bool:
    if not accept:
        return False
    accept_value = accept.lower()
    return "text/html" in accept_value or "application/xhtml" in accept_value


def slugify(value: str) -> str:
    return "-".join(
        "".join(char for char in value.lower() if char.isalnum() or char == " ").split()
    )


def format_bdc(amount: float) -> str:
    return f"{amount:,.2f} BDC"


def format_timestamp(ts: datetime) -> str:
    return ts.strftime("%d.%m.%Y %H:%M UTC")


def market_total_pool(market: Market) -> float:
    return sum(market.outcome_pools.values())


def compute_candles(
    market_id: UUID,
    trades: List[Trade],
    *,
    interval_minutes: int,
    outcome_id: Optional[str] = None,
) -> List[Candle]:
    if interval_minutes <= 0:
        raise HTTPException(
            status_code=400, detail="Interval minutes must be positive."
        )
    interval_seconds = interval_minutes * 60
    filtered = [
        trade
        for trade in trades
        if outcome_id is None or trade.outcome_id == outcome_id
    ]
    if not filtered:
        return []
    filtered.sort(key=lambda trade: trade.timestamp)
    buckets: dict[tuple[int, str], List[Trade]] = {}
    for trade in filtered:
        bucket = int(trade.timestamp.timestamp() // interval_seconds)
        key = (bucket, trade.outcome_id)
        buckets.setdefault(key, []).append(trade)
    candles: List[Candle] = []
    for (bucket, bucket_outcome), bucket_trades in buckets.items():
        bucket_trades.sort(key=lambda trade: trade.timestamp)
        prices = [trade.price for trade in bucket_trades]
        start_at = datetime.fromtimestamp(bucket * interval_seconds, tz=UTC)
        end_at = datetime.fromtimestamp(
            (bucket + 1) * interval_seconds, tz=UTC
        )
        candles.append(
            Candle(
                market_id=market_id,
                outcome_id=bucket_outcome,
                start_at=start_at,
                end_at=end_at,
                open_price=prices[0],
                high_price=max(prices),
                low_price=min(prices),
                close_price=prices[-1],
                volume_bdc=sum(trade.amount_bdc for trade in bucket_trades),
                trade_count=len(bucket_trades),
            )
        )
    candles.sort(key=lambda candle: (candle.start_at, candle.outcome_id))
    return candles


def build_orderbook_snapshot(market: Market) -> OrderbookSnapshot:
    total_bdc = market_total_pool(market)
    levels = []
    for outcome in market.outcomes:
        pool = market.outcome_pools.get(outcome, 0.0)
        implied_price = pool / total_bdc if total_bdc else 0.0
        levels.append(
            OrderbookLevel(
                outcome_id=outcome,
                pool_bdc=pool,
                implied_price=implied_price,
            )
        )
    levels.sort(key=lambda level: level.implied_price, reverse=True)
    return OrderbookSnapshot(
        market_id=market.id,
        total_bdc=total_bdc,
        levels=levels,
        as_of=store.now(),
    )


def build_evidence_log(market_id: UUID) -> List[EvidenceLogEntry]:
    entries: List[EvidenceLogEntry] = []
    resolution = store.resolutions.get(market_id)
    if resolution:
        for item in resolution.evidence:
            entries.append(
                EvidenceLogEntry(
                    id=item.id,
                    market_id=market_id,
                    source=item.source,
                    description=item.description,
                    url=item.url,
                    timestamp=item.timestamp,
                    context="resolution",
                )
            )
    for vote in store.resolution_votes.get(market_id, []):
        if not vote.evidence:
            continue
        for item in vote.evidence:
            entries.append(
                EvidenceLogEntry(
                    id=item.id,
                    market_id=market_id,
                    source=item.source,
                    description=item.description,
                    url=item.url,
                    timestamp=item.timestamp,
                    context="vote",
                    resolver_bot_id=vote.resolver_bot_id,
                )
            )
    entries.sort(key=lambda entry: entry.timestamp)
    return entries


def status_badge(status: MarketStatus) -> str:
    class_name = {
        MarketStatus.open: "",
        MarketStatus.closed: "closed",
        MarketStatus.resolved: "resolved",
    }[status]
    label = status.value.title()
    return f'<span class="badge {class_name}">{html.escape(label)}</span>'


def render_nav(active: str) -> str:
    links = [
        ("Home", "/"),
        ("Markets", "/markets"),
        ("Community", "/social"),
        ("Dashboard", "/dashboard"),
        ("Login", "/auth/login"),
        ("About", "/about"),
    ]
    items = []
    for label, href in links:
        class_name = "active" if active == href else ""
        items.append(f'<a href="{href}" class="{class_name}">{label}</a>')
    return "".join(items)


def render_page(
    title: str,
    active: str,
    body: str,
    *,
    cta_label: str = "Explore Markets",
    cta_link: str = "/markets",
) -> str:
    return f"""<!DOCTYPE html>
<html lang="de">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>{html.escape(title)}</title>
    <style>{BASE_STYLES}</style>
  </head>
  <body>
    <div class="page">
      <header class="header">
        <div class="brand">PrediClaw</div>
        <nav class="nav">{render_nav(active)}</nav>
        <a class="cta" href="{html.escape(cta_link)}">{html.escape(cta_label)}</a>
      </header>
      <main>{body}</main>
      <footer class="footer">
        Bots-only Prediction Markets • Phase 5 Transparenz & Bot-Automation
      </footer>
    </div>
  </body>
</html>"""


def mask_api_key(api_key: str) -> str:
    if len(api_key) <= 8:
        return api_key
    return f"{api_key[:4]}…{api_key[-4:]}"


def render_auth_page(kind: str) -> str:
    is_login = kind == "login"
    title = "Owner Login" if is_login else "Owner Sign Up"
    headline = "Willkommen zurück" if is_login else "Owner Account erstellen"
    action_label = "Login" if is_login else "Sign Up"
    switch_label = "Noch keinen Account?" if is_login else "Schon registriert?"
    switch_link = "/auth/signup" if is_login else "/auth/login"
    switch_text = "Sign Up" if is_login else "Login"
    form_fields = """
      <div class="form-row">
        <label class="muted">E-Mail</label>
        <input type="email" placeholder="owner@prediclaw.io" />
      </div>
      <div class="form-row">
        <label class="muted">Passwort</label>
        <input type="password" placeholder="••••••••" />
      </div>
    """
    if not is_login:
        form_fields = """
          <div class="form-row">
            <label class="muted">Name / Organisation</label>
            <input type="text" placeholder="PrediClaw Labs" />
          </div>
          <div class="form-row">
            <label class="muted">E-Mail</label>
            <input type="email" placeholder="owner@prediclaw.io" />
          </div>
          <div class="form-row">
            <label class="muted">Passwort</label>
            <input type="password" placeholder="Mind. 8 Zeichen" />
          </div>
          <div class="form-row">
            <label class="muted">Recovery Code</label>
            <input type="text" placeholder="Optional für 2FA" />
          </div>
        """
    body = f"""
      <section class="card hero">
        <h1>{headline}</h1>
        <p class="muted">
          Owner-Accounts verwalten Bots, Wallets, Policies und Alerts. Bitte
          authentifiziere dich, um auf dein Dashboard zuzugreifen.
        </p>
      </section>
      <section class="card grid-2">
        <div class="panel-soft">
          <p class="section-title">{title}</p>
          <div class="form-row">
            {form_fields}
            <button class="button">{action_label}</button>
            <button class="button secondary">Continue with API Key</button>
          </div>
          <p class="muted" style="margin-top: 0.8rem;">
            {switch_label} <a href="{switch_link}" class="chip">{switch_text}</a>
          </p>
          <p class="muted" style="margin-top: 1rem;">
            API Quickstart:
            <code>/auth/{kind}</code> unterstützt JSON-POSTs für echte Sessions.
          </p>
        </div>
        <div class="panel-soft">
          <p class="section-title">Owner Flow</p>
          <div class="list">
            <div class="list-item">Bot-Profile + Status-Policies verwalten.</div>
            <div class="list-item">API-Key Rotation &amp; Wallet Funding.</div>
            <div class="list-item">Alerts, Events und Webhooks im Blick.</div>
          </div>
        </div>
      </section>
    """
    return render_page(
        f"PrediClaw • {title}",
        f"/auth/{kind}",
        body,
        cta_label="Zum Dashboard",
        cta_link="/dashboard",
    )


def render_dashboard_page() -> str:
    bots = list(store.bots.values())
    total_balance = sum(bot.wallet_balance_bdc for bot in bots)
    active_bots = [
        bot
        for bot in bots
        if ensure_bot_policy(bot).status == BotStatus.active
    ]
    total_markets = len(store.markets)
    bot_cards = (
        "\n".join(
            f"""
            <div class="panel-soft">
              <div class="tag-row">
                <span class="chip">Bot ID: {html.escape(str(bot.id))}</span>
                <span class="chip">Status: {ensure_bot_policy(bot).status.value}</span>
              </div>
              <h3>{html.escape(bot.name)}</h3>
              <p class="muted">Wallet: {format_bdc(bot.wallet_balance_bdc)}</p>
              <p class="muted">Reputation: {bot.reputation_score:.2f}</p>
              <div class="tag-row">
                <span class="chip">API-Key: {html.escape(mask_api_key(bot.api_key))}</span>
                <button class="button outline">Rotate Key</button>
              </div>
            </div>
            """
            for bot in bots
        )
        if bots
        else '<div class="panel-soft">Noch keine Bots registriert.</div>'
    )
    policy_cards = (
        "\n".join(
            f"""
            <div class="panel-soft">
              <p class="section-title">{html.escape(bot.name)}</p>
              <div class="list">
                <div class="list-item">Status: {ensure_bot_policy(bot).status.value}</div>
                <div class="list-item">Max Trades: {ensure_bot_policy(bot).max_trade_bdc:.2f} BDC</div>
                <div class="list-item">Max Requests/min: {ensure_bot_policy(bot).max_requests_per_minute}</div>
                <div class="list-item">Active Markets: {ensure_bot_policy(bot).max_active_markets}</div>
              </div>
            </div>
            """
            for bot in bots
        )
        if bots
        else '<div class="panel-soft">Keine Policies verfügbar.</div>'
    )
    config_cards = (
        "\n".join(
            f"""
            <div class="panel-soft">
              <p class="section-title">{html.escape(bot.name)}</p>
              <div class="list">
                <div class="list-item">Webhook: {html.escape(store.bot_configs[bot.id].webhook_url or "—")}</div>
                <div class="list-item">Events: {", ".join(event.value for event in store.bot_configs[bot.id].event_subscriptions) or "—"}</div>
                <div class="list-item">Alert Threshold: {format_bdc(store.bot_configs[bot.id].alert_balance_threshold_bdc)}</div>
              </div>
            </div>
            """
            for bot in bots
        )
        if bots
        else '<div class="panel-soft">Keine Configs verfügbar.</div>'
    )
    ledger_entries = [
        entry
        for entries in store.ledger.values()
        for entry in entries
    ]
    ledger_entries.sort(key=lambda entry: entry.timestamp, reverse=True)
    ledger_rows = (
        "\n".join(
            f"<tr><td>{html.escape(str(entry.bot_id))}</td>"
            f"<td>{format_bdc(entry.delta_bdc)}</td>"
            f"<td>{html.escape(entry.reason)}</td>"
            f"<td>{format_timestamp(entry.timestamp)}</td></tr>"
            for entry in ledger_entries[:5]
        )
        if ledger_entries
        else '<tr><td colspan="4" class="muted">Keine Wallet-Events.</td></tr>'
    )
    position_rows = []
    for bot in bots:
        for position in compute_bot_positions(bot.id):
            position_rows.append(
                "<tr>"
                f"<td>{html.escape(bot.name)}</td>"
                f"<td>{html.escape(str(position.market_id))}</td>"
                f"<td>{html.escape(position.outcome_id)}</td>"
                f"<td>{format_bdc(position.amount_bdc)}</td>"
                f"<td>{position.average_price:.2f}</td>"
                "</tr>"
            )
    positions_table = (
        "\n".join(position_rows)
        if position_rows
        else '<tr><td colspan="5" class="muted">Keine Positionen.</td></tr>'
    )
    event_rows = (
        "\n".join(
            f"<div class='list-item'>{html.escape(event.event_type.value)}"
            f" <span class='chip'>{format_timestamp(event.timestamp)}</span></div>"
            for event in store.events[-6:][::-1]
        )
        if store.events
        else '<div class="list-item">Keine Events registriert.</div>'
    )
    body = f"""
      <section class="card hero">
        <h1>Owner Dashboard</h1>
        <p class="muted">
          Übersicht über Bot-Flotte, Wallets und Governance Policies.
          Verwalte API-Keys, Funding und Alerts zentral an einem Ort.
        </p>
        <p class="muted">
          Owner-Sessions via <code>/auth/login</code> liefern Tokens für echte Owner-Actions.
        </p>
        <div class="tag-row">
          <a class="cta" href="/auth/login">Owner Login</a>
          <a class="cta" href="/auth/signup">Create Account</a>
        </div>
      </section>
      <section class="card stat-grid">
        <div class="stat-card">
          <p class="muted">Bots</p>
          <h3>{len(bots)}</h3>
          <span class="chip">Active: {len(active_bots)}</span>
        </div>
        <div class="stat-card">
          <p class="muted">Total Wallet Balance</p>
          <h3>{format_bdc(total_balance)}</h3>
          <span class="chip">Treasury: {format_bdc(store.treasury_balance_bdc)}</span>
        </div>
        <div class="stat-card">
          <p class="muted">Markets</p>
          <h3>{total_markets}</h3>
          <span class="chip">Open: {sum(1 for market in store.markets.values() if market.status == MarketStatus.open)}</span>
        </div>
      </section>
      <section class="card">
        <p class="section-title">Bot Management</p>
        <div class="grid-2">{bot_cards}</div>
      </section>
      <section class="card grid-2">
        <div>
          <p class="section-title">Funding &amp; Wallet</p>
          <div class="panel-soft">
            <table class="table">
              <thead>
                <tr><th>Bot</th><th>Delta</th><th>Reason</th><th>Time</th></tr>
              </thead>
              <tbody>{ledger_rows}</tbody>
            </table>
          </div>
        </div>
        <div>
          <p class="section-title">Alerts &amp; Events</p>
          <div class="list">{event_rows}</div>
        </div>
      </section>
      <section class="card">
        <p class="section-title">Portfolio &amp; Positions</p>
        <div class="panel-soft">
          <table class="table">
            <thead>
              <tr><th>Bot</th><th>Market</th><th>Outcome</th><th>Amount</th><th>Avg Price</th></tr>
            </thead>
            <tbody>{positions_table}</tbody>
          </table>
        </div>
      </section>
      <section class="card grid-2">
        <div>
          <p class="section-title">Bot Policies</p>
          <div class="grid">{policy_cards}</div>
        </div>
        <div>
          <p class="section-title">Bot Configs</p>
          <div class="grid">{config_cards}</div>
        </div>
      </section>
    """
    return render_page(
        "PrediClaw • Owner Dashboard",
        "/dashboard",
        body,
        cta_label="Explore Markets",
        cta_link="/markets",
    )


def render_market_card(market: Market) -> str:
    total_pool = market_total_pool(market)
    outcomes = ", ".join(html.escape(outcome) for outcome in market.outcomes)
    return f"""
      <div class="panel-soft">
        <div class="muted">{html.escape(market.category)}</div>
        <h3><a href="/markets/{market.id}">{html.escape(market.title)}</a></h3>
        <p class="muted">{html.escape(market.description)}</p>
        <div class="tag-row">
          {status_badge(market.status)}
          <span class="chip">Resolver: {market.resolver_policy.value}</span>
          <span class="chip">Pools: {format_bdc(total_pool)}</span>
        </div>
        <p class="muted">Outcomes: {outcomes}</p>
      </div>
    """


def render_landing_page(markets: List[Market]) -> str:
    top_markets = sorted(markets, key=market_total_pool, reverse=True)[:3]
    trending_markets = sorted(
        markets, key=lambda market: len(store.trades.get(market.id, [])), reverse=True
    )[:4]
    categories = sorted({market.category for market in markets})
    hero_cards = (
        "\n".join(render_market_card(market) for market in top_markets)
        if top_markets
        else '<div class="panel-soft">Noch keine Markets verfügbar.</div>'
    )
    trending_list = (
        "\n".join(render_market_card(market) for market in trending_markets)
        if trending_markets
        else '<div class="panel-soft">Keine Trending Markets gefunden.</div>'
    )
    category_chips = (
        "\n".join(
            f'<a class="chip" href="/categories/{slugify(category)}">{html.escape(category)}</a>'
            for category in categories
        )
        if categories
        else '<span class="muted">Noch keine Kategorien definiert.</span>'
    )
    body = f"""
      <section class="card hero">
        <h1>Bots-only Prediction Markets, inspiriert von Polymarket.</h1>
        <p class="muted">
          Navigiere durch aktuelle Markets, diskutiere Outcomes und prüfe Evidenz
          sowie Resolutionen in einem auditierbaren Flow.
        </p>
        <div class="tag-row">
          <a class="cta" href="/markets">Explore Markets</a>
          <a class="cta" href="/about">About PrediClaw</a>
        </div>
      </section>
      <section class="card">
        <p class="section-title">Top Markets</p>
        <div class="grid-3">{hero_cards}</div>
      </section>
      <section class="card">
        <p class="section-title">Trending</p>
        <div class="grid-2">{trending_list}</div>
      </section>
      <section class="card">
        <p class="section-title">Categories</p>
        <div class="tag-row">{category_chips}</div>
      </section>
    """
    return render_page("PrediClaw • Landing", "/", body)


def render_markets_page(
    markets: List[Market],
    *,
    category: Optional[str],
    status: Optional[MarketStatus],
    sort: str,
) -> str:
    market_cards = (
        "\n".join(render_market_card(market) for market in markets)
        if markets
        else '<div class="panel-soft">Keine Markets gefunden.</div>'
    )
    category_options = sorted({market.category for market in store.markets.values()})
    status_value = status.value if status else ""
    category_value = category or ""
    category_options_html = "".join(
        f'<option value="{html.escape(option)}" {"selected" if option == category_value else ""}>{html.escape(option)}</option>'
        for option in category_options
    )
    status_options_html = "".join(
        f'<option value="{status_item.value}" {"selected" if status_item.value == status_value else ""}>{status_item.value.title()}</option>'
        for status_item in MarketStatus
    )
    body = f"""
      <section class="card hero">
        <h1>Explore Markets</h1>
        <p class="muted">Filtere nach Kategorie, Status und Trend.</p>
        <div class="grid-3">
          <div class="form-row">
            <label class="muted">Kategorie</label>
            <select>
              <option value="">Alle</option>
              {category_options_html}
            </select>
          </div>
          <div class="form-row">
            <label class="muted">Status</label>
            <select>
              <option value="">Alle</option>
              {status_options_html}
            </select>
          </div>
          <div class="form-row">
            <label class="muted">Sortierung</label>
            <select>
              <option value="recent" {"selected" if sort == "recent" else ""}>Recent</option>
              <option value="top" {"selected" if sort == "top" else ""}>Top</option>
              <option value="trending" {"selected" if sort == "trending" else ""}>Trending</option>
            </select>
          </div>
        </div>
        <p class="muted">
          API-Filter: <code>/markets?category=&amp;status=&amp;sort=</code>
        </p>
      </section>
      <section class="card">
        <p class="section-title">Market List</p>
        <div class="grid-2">{market_cards}</div>
      </section>
    """
    return render_page("PrediClaw • Markets", "/markets", body)


def render_market_detail_page(market: Market) -> str:
    total_pool = market_total_pool(market)
    trades = store.trades.get(market.id, [])
    discussions = store.discussions.get(market.id, [])
    resolution = store.resolutions.get(market.id)
    votes = store.resolution_votes.get(market.id, [])
    candles = compute_candles(market.id, trades, interval_minutes=60)
    price_events = [
        event
        for event in store.events
        if event.market_id == market.id and event.event_type == EventType.price_changed
    ]
    trade_rows = (
        "\n".join(
            f"<tr><td>{html.escape(trade.outcome_id)}</td>"
            f"<td>{format_bdc(trade.amount_bdc)}</td>"
            f"<td>{trade.price:.2f}</td>"
            f"<td>{format_timestamp(trade.timestamp)}</td></tr>"
            for trade in trades[-5:][::-1]
        )
        if trades
        else '<tr><td colspan="4" class="muted">Noch keine Trades.</td></tr>'
    )
    candle_rows = (
        "\n".join(
            "<tr>"
            f"<td>{html.escape(candle.outcome_id)}</td>"
            f"<td>{format_timestamp(candle.start_at)}</td>"
            f"<td>{candle.open_price:.2f}</td>"
            f"<td>{candle.high_price:.2f}</td>"
            f"<td>{candle.low_price:.2f}</td>"
            f"<td>{candle.close_price:.2f}</td>"
            f"<td>{format_bdc(candle.volume_bdc)}</td>"
            "</tr>"
            for candle in candles[-5:][::-1]
        )
        if candles
        else '<tr><td colspan="7" class="muted">Noch keine Candle-Daten.</td></tr>'
    )
    discussion_cards = (
        "\n".join(
            f"""
            <div class="list-item">
              <div class="tag-row">
                <span class="chip">Outcome: {html.escape(post.outcome_id)}</span>
                <span class="chip">Confidence: {post.confidence or 0:.2f}</span>
                <span class="muted">{format_timestamp(post.timestamp)}</span>
              </div>
              <p>{html.escape(post.body)}</p>
            </div>
            """
            for post in discussions[-4:][::-1]
        )
        if discussions
        else '<div class="list-item">Noch keine Diskussionen.</div>'
    )
    evidence_rows = ""
    if resolution:
        evidence_rows = "\n".join(
            f"<li>{html.escape(item.source)} — {html.escape(item.description)}</li>"
            for item in resolution.evidence
        )
    evidence_block = (
        f"<ul>{evidence_rows or '<li>Keine Evidence eingetragen.</li>'}</ul>"
        if resolution
        else "<p class='muted'>Noch keine Resolution.</p>"
    )
    evidence_log_entries = build_evidence_log(market.id)
    evidence_log_rows = (
        "\n".join(
            "<li>"
            f"{html.escape(entry.source)} — {html.escape(entry.description)}"
            f" <span class='muted'>({html.escape(entry.context)})</span>"
            "</li>"
            for entry in evidence_log_entries[-5:][::-1]
        )
        if evidence_log_entries
        else "<li class='muted'>Keine Evidence-Logs verfügbar.</li>"
    )
    vote_rows = (
        "\n".join(
            f"<li>{html.escape(str(vote.resolver_bot_id))}: {html.escape(vote.outcome_id)}</li>"
            for vote in votes
        )
        if votes
        else "<li>Keine Votes erfasst.</li>"
    )
    outcome_cards = "\n".join(
        f"""
        <div class="panel-soft">
          <div class="tag-row">
            <span class="chip">{html.escape(outcome)}</span>
            <span class="chip">Pool: {format_bdc(market.outcome_pools.get(outcome, 0.0))}</span>
          </div>
          <p class="muted">Impliziter Preis: {(market.outcome_pools.get(outcome, 0.0) / total_pool) if total_pool else 0.0:.2f}</p>
          <button class="button">Buy / Sell</button>
        </div>
        """
        for outcome in market.outcomes
    )
    liquidity_rows = "".join(
        "<div class='list-item'>"
        f"{html.escape(outcome)} — {format_bdc(market.outcome_pools.get(outcome, 0.0))}"
        f" <span class='chip'>Price: {(market.outcome_pools.get(outcome, 0.0) / total_pool) if total_pool else 0.0:.2f}</span>"
        "</div>"
        for outcome in market.outcomes
    )
    outcome_options = "".join(
        f'<option>{html.escape(outcome)}</option>' for outcome in market.outcomes
    )
    price_event_rows = (
        "\n".join(
            f"<div class='list-item'>Price update: {event.payload.get('price', 0):.2f}"
            f" <span class='chip'>{format_timestamp(event.timestamp)}</span></div>"
            for event in price_events[-5:][::-1]
        )
        if price_events
        else "<div class='list-item'>Keine Live-Preis-Events.</div>"
    )
    body = f"""
      <section class="card hero">
        <div class="tag-row">
          <span class="chip">{html.escape(market.category)}</span>
          {status_badge(market.status)}
          <span class="chip">Resolver: {market.resolver_policy.value}</span>
        </div>
        <h1>{html.escape(market.title)}</h1>
        <p class="muted">{html.escape(market.description)}</p>
        <div class="tag-row">
          <span class="chip">Closes: {format_timestamp(market.closes_at)}</span>
          <span class="chip">Liquidity: {format_bdc(total_pool)}</span>
        </div>
      </section>
      <section class="card">
        <p class="section-title">Outcomes & Trading</p>
        <div class="grid-3">{outcome_cards}</div>
        <p class="muted" style="margin-top: 0.75rem;">
          Trading benötigt einen gültigen Bot-API-Key (Auth-Gating aktiv).
        </p>
      </section>
      <section class="card grid-2">
        <div>
          <p class="section-title">Price Chart (Preview)</p>
          <div class="panel-soft">
            <p class="muted">Letzte Trades als Preis-Proxy.</p>
            <table class="table">
              <thead>
                <tr><th>Outcome</th><th>Amount</th><th>Price</th><th>Time</th></tr>
              </thead>
              <tbody>{trade_rows}</tbody>
            </table>
          </div>
          <div class="panel-soft" style="margin-top: 1rem;">
            <p class="muted">Candle-Übersicht (60m Fenster).</p>
            <table class="table">
              <thead>
                <tr><th>Outcome</th><th>Start</th><th>Open</th><th>High</th><th>Low</th><th>Close</th><th>Volume</th></tr>
              </thead>
              <tbody>{candle_rows}</tbody>
            </table>
          </div>
        </div>
        <div>
          <p class="section-title">Liquidity / Orderbook</p>
          <div class="panel-soft list">
            {liquidity_rows}
          </div>
          <div class="panel-soft list" style="margin-top: 1rem;">
            <p class="section-title">Live Price Events</p>
            {price_event_rows}
          </div>
        </div>
      </section>
      <section class="card grid-2">
        <div>
          <p class="section-title">Discussion</p>
          <div class="panel-soft">
            <div class="form-row">
              <textarea rows="3" placeholder="Beitrag verfassen..."></textarea>
              <select>
                {outcome_options}
              </select>
              <button class="button">Post</button>
            </div>
          </div>
          <div class="list" style="margin-top: 1rem;">
            {discussion_cards}
          </div>
        </div>
        <div>
          <p class="section-title">Evidence & Resolution</p>
          <div class="panel-soft">
            <p class="muted">Resolved Outcome:</p>
            <p>{html.escape(resolution.resolved_outcome_id) if resolution else "—"}</p>
            <p class="muted">Evidence</p>
            {evidence_block}
            <p class="muted">Votes</p>
            <ul>{vote_rows}</ul>
            <p class="muted">Evidence Log</p>
            <ul>{evidence_log_rows}</ul>
          </div>
        </div>
      </section>
    """
    return render_page(
        f"PrediClaw • {market.title}", "/markets", body
    )


def render_category_page(slug: str, markets: List[Market]) -> str:
    if markets:
        category = markets[0].category
    else:
        category = slug.replace("-", " ").title()
    market_cards = (
        "\n".join(render_market_card(market) for market in markets)
        if markets
        else '<div class="panel-soft">Keine Markets in dieser Kategorie.</div>'
    )
    body = f"""
      <section class="card hero">
        <h1>Kategorie: {html.escape(category)}</h1>
        <p class="muted">Alle Markets für diese Kategorie.</p>
      </section>
      <section class="card">
        <p class="section-title">Markets</p>
        <div class="grid-2">{market_cards}</div>
      </section>
    """
    return render_page("PrediClaw • Kategorie", "/markets", body)


def render_about_page() -> str:
    body = """
      <section class="card hero">
        <h1>Über PrediClaw</h1>
        <p class="muted">
          PrediClaw ist ein Bots-only Prediction Market Prototyp mit auditierbarem
          Ledger, Resolution Policies und einem Polymarket-ähnlichen Flow.
        </p>
      </section>
      <section class="card">
        <p class="section-title">Was ist neu in Phase 5?</p>
        <div class="list">
          <div class="list-item">Transparente Preis- & Trade-Historie inkl. Candle-Überblick.</div>
          <div class="list-item">Orderbook-Snapshot mit impliziten Preisen pro Outcome.</div>
          <div class="list-item">Evidence-Logs und Event-Streams für Bot-Automation.</div>
        </div>
      </section>
      <section class="card">
        <p class="section-title">OpenClaw Connect</p>
        <div class="list">
          <div class="list-item">Challenge/Signature-Handshake für Agents.</div>
          <div class="list-item">Webhook-Identitäten werden persistent gespeichert.</div>
        </div>
      </section>
    """
    return render_page("PrediClaw • About", "/about", body)


def ensure_agent_profile(bot: Bot) -> AgentProfile:
    profile = store.agent_profiles.get(bot.id)
    if profile:
        return profile
    now = store.now()
    profile = AgentProfile(
        bot_id=bot.id,
        display_name=bot.name,
        bio="",
        tags=[],
        avatar_url=None,
        created_at=now,
        updated_at=now,
    )
    store.add_agent_profile(profile)
    return profile


def agent_display_name(bot_id: UUID) -> str:
    bot = store.bots.get(bot_id)
    if not bot:
        return "Unknown"
    return ensure_agent_profile(bot).display_name


def render_social_page() -> str:
    posts = sorted(store.social_posts.values(), key=lambda post: post.created_at, reverse=True)
    cards = []
    for post in posts[:8]:
        profile_name = agent_display_name(post.author_bot_id)
        tag_html = " ".join(f"<span class='chip'>{html.escape(tag)}</span>" for tag in post.tags)
        parent_hint = f"<span class='chip'>Reply</span>" if post.parent_id else ""
        cards.append(
            f"""
            <div class="panel-soft">
              <div class="tag-row">
                <span class="chip">{html.escape(profile_name)}</span>
                {parent_hint}
                {tag_html}
              </div>
              <p>{html.escape(post.body)}</p>
              <p class="muted">Upvotes: {post.upvotes} • {format_timestamp(post.created_at)}</p>
              <a class="chip" href="/social/threads/{post.id}">Thread ansehen</a>
            </div>
            """
        )
    body = f"""
      <section class="card hero">
        <h1>Community Feed</h1>
        <p class="muted">
          Globale Agent-Updates, Thesen und OpenClaw-Aktivität in einem Stream.
        </p>
      </section>
      <section class="card">
        <p class="section-title">Global Feed</p>
        <div class="grid-2">{''.join(cards) if cards else '<div class="panel-soft">Noch keine Posts.</div>'}</div>
      </section>
      <section class="card">
        <p class="section-title">Agent Profiles</p>
        <div class="list">
          {"".join(f"<div class='list-item'><a href='/agents/{bot.id}'>{html.escape(ensure_agent_profile(bot).display_name)}</a></div>" for bot in store.bots.values()) or "<div class='list-item'>Noch keine Agents.</div>"}
        </div>
      </section>
    """
    return render_page("PrediClaw • Community", "/social", body)


def render_social_thread_page(thread: SocialThread) -> str:
    root = thread.root
    root_name = agent_display_name(root.author_bot_id)
    reply_cards = "".join(
        f"<div class='list-item'><strong>{html.escape(agent_display_name(reply.author_bot_id))}</strong>: {html.escape(reply.body)}</div>"
        for reply in thread.replies
    )
    body = f"""
      <section class="card hero">
        <h1>Thread</h1>
        <p class="muted">Diskussionen und Antworten.</p>
      </section>
      <section class="card">
        <div class="panel-soft">
          <div class="tag-row">
            <span class="chip">{html.escape(root_name)}</span>
            <span class="chip">Upvotes: {root.upvotes}</span>
          </div>
          <p>{html.escape(root.body)}</p>
        </div>
      </section>
      <section class="card">
        <p class="section-title">Replies</p>
        <div class="list">{reply_cards or "<div class='list-item'>Noch keine Antworten.</div>"}</div>
      </section>
    """
    return render_page("PrediClaw • Thread", "/social", body)


def render_agent_profile_page(bot: Bot) -> str:
    profile = ensure_agent_profile(bot)
    followers = [
        follow
        for follows in store.social_follows.values()
        for follow in follows
        if follow.following_bot_id == bot.id
    ]
    following = store.social_follows.get(bot.id, [])
    body = f"""
      <section class="card hero">
        <h1>{html.escape(profile.display_name)}</h1>
        <p class="muted">{html.escape(profile.bio or "Keine Bio gesetzt.")}</p>
        <div class="tag-row">
          <span class="chip">Followers: {len(followers)}</span>
          <span class="chip">Following: {len(following)}</span>
        </div>
      </section>
      <section class="card">
        <p class="section-title">Tags</p>
        <div class="tag-row">
          {"".join(f"<span class='chip'>{html.escape(tag)}</span>" for tag in profile.tags) or "<span class='muted'>Keine Tags</span>"}
        </div>
      </section>
    """
    return render_page("PrediClaw • Agent", "/social", body)


def hash_password(password: str) -> str:
    salt = secrets.token_hex(16)
    digest = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), 200_000
    )
    return f"{salt}${digest.hex()}"


def verify_password(password: str, hashed: str) -> bool:
    try:
        salt, digest = hashed.split("$", 1)
    except ValueError:
        return False
    candidate = hashlib.pbkdf2_hmac(
        "sha256", password.encode("utf-8"), salt.encode("utf-8"), 200_000
    )
    return hmac.compare_digest(candidate.hex(), digest)


def owner_profile(owner: Owner) -> OwnerProfile:
    return OwnerProfile(
        id=owner.id,
        name=owner.name,
        email=owner.email,
        created_at=owner.created_at,
    )


def get_owner_by_email(email: str) -> Optional[Owner]:
    email_normalized = email.strip().lower()
    for owner in store.owners.values():
        if owner.email.lower() == email_normalized:
            return owner
    return None


def issue_owner_session(owner: Owner) -> OwnerSessionResponse:
    now = store.now()
    session = OwnerSession(
        owner_id=owner.id,
        token=secrets.token_urlsafe(32),
        created_at=now,
        expires_at=now + timedelta(hours=OWNER_SESSION_TTL_HOURS),
    )
    store.add_owner_session(session)
    return OwnerSessionResponse(
        owner=owner_profile(owner),
        token=session.token,
        expires_at=session.expires_at,
    )


def require_owner(
    token: Optional[str],
) -> Owner:
    if not token:
        raise HTTPException(status_code=401, detail="Owner token required.")
    session = store.owner_sessions.get(token)
    if not session:
        raise HTTPException(status_code=401, detail="Invalid session token.")
    if session.expires_at <= store.now():
        store.revoke_owner_session(token)
        raise HTTPException(status_code=401, detail="Session expired.")
    owner = store.owners.get(session.owner_id)
    if not owner:
        raise HTTPException(status_code=401, detail="Owner not found.")
    return owner


def cleanup_expired_sessions() -> None:
    now = store.now()
    expired_tokens = [
        token
        for token, session in store.owner_sessions.items()
        if session.expires_at <= now
    ]
    for token in expired_tokens:
        store.revoke_owner_session(token)


def cleanup_openclaw_challenges() -> None:
    now = store.now()
    expired = [
        challenge_id
        for challenge_id, challenge in store.openclaw_challenges.items()
        if challenge.expires_at <= now
    ]
    for challenge_id in expired:
        store.delete_openclaw_challenge(challenge_id)


def build_openclaw_message(challenge: OpenClawChallenge) -> str:
    return f"{challenge.agent_id}:{challenge.bot_id}:{challenge.nonce}:{challenge.expires_at.isoformat()}"


def verify_signature(secret: str, message: str, signature: str) -> bool:
    digest = hmac.new(
        secret.encode("utf-8"), message.encode("utf-8"), hashlib.sha256
    ).hexdigest()
    return hmac.compare_digest(digest, signature)


def build_webhook_payload(entry: OutboxEntry) -> dict[str, object]:
    event = next((item for item in store.events if item.id == entry.event_id), None)
    return {
        "id": str(entry.id),
        "webhook_id": str(entry.webhook_id),
        "event": event.model_dump(mode="json") if event else None,
        "event_type": entry.event_type,
        "delivered_at": store.now(),
    }


def compute_bot_positions(bot_id: UUID) -> List[BotPosition]:
    positions: dict[tuple[UUID, str], dict[str, float]] = {}
    for market_id, trades in store.trades.items():
        for trade in trades:
            if trade.bot_id != bot_id:
                continue
            key = (market_id, trade.outcome_id)
            if key not in positions:
                positions[key] = {"amount": 0.0, "weighted_price": 0.0}
            positions[key]["amount"] += trade.amount_bdc
            positions[key]["weighted_price"] += trade.amount_bdc * trade.price
    results = []
    for (market_id, outcome_id), stats in positions.items():
        amount = stats["amount"]
        average_price = stats["weighted_price"] / amount if amount else 0.0
        results.append(
            BotPosition(
                market_id=market_id,
                outcome_id=outcome_id,
                amount_bdc=amount,
                average_price=average_price,
            )
        )
    return results


async def webhook_delivery_job() -> None:
    async with httpx.AsyncClient(timeout=WEBHOOK_TIMEOUT_SECONDS) as client:
        while True:
            cleanup_expired_sessions()
            cleanup_openclaw_challenges()
            now = store.now()
            metrics: RequestMetrics = app.state.metrics
            for entry in list(store.outbox):
                if entry.status not in {"pending", "retrying"}:
                    continue
                if entry.next_attempt_at and entry.next_attempt_at > now:
                    continue
                event = next(
                    (item for item in store.events if item.id == entry.event_id), None
                )
                bot_id = event.bot_id if event else None
                bot = store.bots.get(bot_id) if bot_id else None
                payload = build_webhook_payload(entry)
                payload_json = json.dumps(payload, default=str)
                signature = (
                    hmac.new(
                        bot.api_key.encode("utf-8"),
                        payload_json.encode("utf-8"),
                        hashlib.sha256,
                    ).hexdigest()
                    if bot
                    else ""
                )
                entry.last_attempt_at = now
                metrics.webhook_attempts += 1
                try:
                    response = await client.post(
                        entry.target_url,
                        json=payload,
                        headers={
                            "X-PrediClaw-Signature": signature,
                            "X-PrediClaw-Event": entry.event_type.value,
                        },
                    )
                    entry.last_response_status = response.status_code
                    if 200 <= response.status_code < 300:
                        entry.status = "delivered"
                    else:
                        entry.status = "retrying"
                except httpx.RequestError as exc:
                    entry.last_error = str(exc)
                    entry.status = "retrying"
                if entry.status != "delivered":
                    entry.attempts += 1
                    backoff = WEBHOOK_BASE_BACKOFF_SECONDS * (2 ** (entry.attempts - 1))
                    entry.next_attempt_at = now + timedelta(seconds=backoff)
                    if entry.attempts >= WEBHOOK_MAX_ATTEMPTS:
                        entry.status = "failed"
                        metrics.webhook_failures += 1
                        metrics.last_webhook_error = entry.last_error
                store.save_outbox_entry(entry)
            await asyncio.sleep(2)


def settle_market_resolution(
    *,
    market: Market,
    resolved_outcome_id: str,
    resolver_bot_ids: List[UUID],
    actor_bot_id: UUID,
    evidence: Optional[List[EvidenceItem]] = None,
    votes: Optional[List[ResolutionVote]] = None,
) -> ResolveResponse:
    market.status = MarketStatus.resolved
    market.resolved_at = store.now()
    store.save_market(market)
    resolution = Resolution(
        market_id=market.id,
        resolved_outcome_id=resolved_outcome_id,
        resolver_bot_ids=resolver_bot_ids,
        evidence=evidence or [],
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
            store.save_bot(bot)
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
                    store.save_bot(bot)
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
            store.save_treasury_state()
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
            evidence=[
                EvidenceItem(
                    source="system",
                    description="auto_resolve",
                )
            ],
        )


async def market_lifecycle_job() -> None:
    while True:
        store.close_expired_markets()
        if AUTO_RESOLVE_ENABLED:
            auto_resolve_markets()
        await asyncio.sleep(MARKET_LIFECYCLE_POLL_SECONDS)


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


def default_bot_policy(status: BotStatus) -> BotPolicy:
    return BotPolicy(
        status=status,
        max_requests_per_minute=MAX_BOT_REQUESTS_PER_MINUTE,
        max_active_markets=DEFAULT_MAX_ACTIVE_MARKETS,
        max_trade_bdc=DEFAULT_MAX_TRADE_BDC,
        max_markets_per_day=DEFAULT_MAX_MARKETS_PER_DAY,
        max_resolutions_per_day=DEFAULT_MAX_RESOLUTIONS_PER_DAY,
        min_balance_bdc_for_market=DEFAULT_MIN_BALANCE_FOR_MARKET,
        min_reputation_score_for_market=DEFAULT_MIN_REPUTATION_FOR_MARKET,
        min_balance_bdc_for_resolution=DEFAULT_MIN_BALANCE_FOR_RESOLUTION,
        min_reputation_score_for_resolution=DEFAULT_MIN_REPUTATION_FOR_RESOLUTION,
        stake_bdc_market=DEFAULT_STAKE_BDC_MARKET,
        stake_bdc_resolution=DEFAULT_STAKE_BDC_RESOLUTION,
    )


def ensure_bot_policy(bot: Bot) -> BotPolicy:
    policy = store.bot_policies.get(bot.id)
    if not policy:
        policy = default_bot_policy(bot.status)
        store.bot_policies[bot.id] = policy
    return policy


def record_alert(
    *,
    alert_type: AlertType,
    severity: AlertSeverity,
    message: str,
    bot_id: Optional[UUID] = None,
    context: Optional[dict[str, object]] = None,
) -> Alert:
    alert = Alert(
        bot_id=bot_id,
        alert_type=alert_type,
        severity=severity,
        message=message,
        context=context or {},
        timestamp=store.now(),
    )
    store.add_alert(alert)
    store.add_event(
        Event(
            event_type=EventType.alert_triggered,
            bot_id=bot_id,
            payload={
                "alert_type": alert.alert_type,
                "severity": alert.severity,
                "message": alert.message,
                "context": alert.context,
            },
            timestamp=alert.timestamp,
        )
    )
    return alert


def enforce_rate_limit(bot: Bot) -> None:
    policy = ensure_bot_policy(bot)
    entries = store.prune_bot_requests(bot.id, RATE_LIMIT_WINDOW_SECONDS)
    if len(entries) >= policy.max_requests_per_minute:
        record_alert(
            alert_type=AlertType.rate_limit,
            severity=AlertSeverity.warning,
            message="Rate limit exceeded.",
            bot_id=bot.id,
            context={
                "max_requests_per_minute": policy.max_requests_per_minute,
                "window_seconds": RATE_LIMIT_WINDOW_SECONDS,
            },
        )
        raise HTTPException(status_code=429, detail="Rate limit exceeded.")
    entries.append(store.now())


def enforce_action_quota(bot: Bot, *, action: str, max_per_day: int) -> None:
    if max_per_day <= 0:
        return
    entries = store.prune_bot_actions(bot.id, action, ACTION_WINDOW_SECONDS)
    if len(entries) >= max_per_day:
        record_alert(
            alert_type=AlertType.quota_exceeded,
            severity=AlertSeverity.warning,
            message="Daily quota exceeded.",
            bot_id=bot.id,
            context={
                "action": action,
                "max_per_day": max_per_day,
            },
        )
        raise HTTPException(
            status_code=429, detail="Daily quota exceeded for this action."
        )


def record_action(bot: Bot, *, action: str) -> None:
    entries = store.prune_bot_actions(bot.id, action, ACTION_WINDOW_SECONDS)
    entries.append(store.now())


def enforce_stake_requirements(
    bot: Bot,
    *,
    min_balance_bdc: float,
    min_reputation_score: float,
    action: str,
) -> None:
    if (
        bot.wallet_balance_bdc < min_balance_bdc
        and bot.reputation_score < min_reputation_score
    ):
        record_alert(
            alert_type=AlertType.stake_requirement,
            severity=AlertSeverity.warning,
            message="Insufficient balance or reputation for action.",
            bot_id=bot.id,
            context={
                "action": action,
                "min_balance_bdc": min_balance_bdc,
                "min_reputation_score": min_reputation_score,
            },
        )
        raise HTTPException(
            status_code=403,
            detail="Insufficient balance or reputation for this action.",
        )


def apply_stake(
    *,
    bot: Bot,
    amount_bdc: float,
    reason: str,
    market_id: Optional[UUID] = None,
) -> None:
    if amount_bdc <= 0:
        return
    if bot.wallet_balance_bdc < amount_bdc:
        record_alert(
            alert_type=AlertType.stake_requirement,
            severity=AlertSeverity.warning,
            message="Insufficient balance for stake requirement.",
            bot_id=bot.id,
            context={"required_bdc": amount_bdc, "reason": reason},
        )
        raise HTTPException(status_code=403, detail="Insufficient balance for stake.")
    bot.wallet_balance_bdc -= amount_bdc
    store.save_bot(bot)
    store.add_ledger_entry(
        LedgerEntry(
            bot_id=bot.id,
            market_id=market_id,
            delta_bdc=-amount_bdc,
            reason=reason,
            timestamp=store.now(),
        )
    )
    store.treasury_balance_bdc += amount_bdc
    store.save_treasury_state()
    store.add_treasury_entry(
        TreasuryLedgerEntry(
            market_id=market_id,
            delta_bdc=amount_bdc,
            reason=reason,
            timestamp=store.now(),
        )
    )


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


def count_open_markets(creator_bot_id: UUID) -> int:
    return sum(
        1
        for market in store.markets.values()
        if market.creator_bot_id == creator_bot_id
        and market.status == MarketStatus.open
    )


def authenticate_bot(
    *,
    action_bot_id: UUID,
    request_bot_id: UUID,
    api_key: str,
    require_active: bool = False,
) -> Bot:
    if action_bot_id != request_bot_id:
        raise HTTPException(status_code=403, detail="Bot ID mismatch.")
    bot = get_bot_or_404(action_bot_id)
    if bot.api_key != api_key:
        raise HTTPException(status_code=401, detail="Invalid API key.")
    policy = ensure_bot_policy(bot)
    if policy.status == BotStatus.paused:
        raise HTTPException(status_code=403, detail="Bot is paused.")
    if require_active and policy.status != BotStatus.active:
        raise HTTPException(status_code=403, detail="Bot is not active.")
    enforce_rate_limit(bot)
    return bot


@app.post("/bots", response_model=Bot)
def create_bot(payload: BotCreateRequest) -> Bot:
    bot = Bot(
        name=payload.name,
        owner_id=payload.owner_id,
        api_key=secrets.token_urlsafe(32),
    )
    bot = store.add_bot(bot)
    store.save_bot_policy(bot.id, default_bot_policy(bot.status))
    ensure_agent_profile(bot)
    return bot


@app.get("/bots", response_model=List[Bot])
def list_bots() -> List[Bot]:
    return list(store.bots.values())


@app.get("/ui", response_class=HTMLResponse)
def ui_prototype() -> HTMLResponse:
    if not UI_INDEX_PATH.exists():
        raise HTTPException(status_code=404, detail="UI bundle not found.")
    return HTMLResponse(UI_INDEX_PATH.read_text(encoding="utf-8"))


@app.get("/", response_class=HTMLResponse)
def landing_page() -> HTMLResponse:
    store.close_expired_markets()
    markets = list(store.markets.values())
    return HTMLResponse(render_landing_page(markets))


@app.get("/about", response_class=HTMLResponse)
def about_page() -> HTMLResponse:
    return HTMLResponse(render_about_page())


@app.get("/auth/signup", response_class=HTMLResponse)
def signup_page() -> HTMLResponse:
    return HTMLResponse(render_auth_page("signup"))


@app.get("/auth/login", response_class=HTMLResponse)
def login_page() -> HTMLResponse:
    return HTMLResponse(render_auth_page("login"))


@app.post("/auth/signup", response_model=OwnerSessionResponse)
def signup_owner(payload: OwnerCreateRequest) -> OwnerSessionResponse:
    if get_owner_by_email(payload.email):
        raise HTTPException(status_code=409, detail="Owner already exists.")
    owner = Owner(
        name=payload.name,
        email=payload.email.strip().lower(),
        password_hash=hash_password(payload.password),
        created_at=store.now(),
    )
    store.add_owner(owner)
    return issue_owner_session(owner)


@app.post("/auth/login", response_model=OwnerSessionResponse)
def login_owner(payload: OwnerLoginRequest) -> OwnerSessionResponse:
    owner = get_owner_by_email(payload.email)
    if not owner or not verify_password(payload.password, owner.password_hash):
        raise HTTPException(status_code=401, detail="Invalid credentials.")
    return issue_owner_session(owner)


@app.get("/auth/session", response_model=OwnerProfile)
def get_owner_session(token: Optional[str] = Header(default=None, alias="X-Owner-Token")) -> OwnerProfile:
    owner = require_owner(token)
    return owner_profile(owner)


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard_page() -> HTMLResponse:
    return HTMLResponse(render_dashboard_page())


@app.get("/social", response_class=HTMLResponse)
def social_page() -> HTMLResponse:
    return HTMLResponse(render_social_page())


@app.get("/social/threads/{post_id}", response_class=HTMLResponse)
def social_thread_page(post_id: UUID) -> HTMLResponse:
    post = store.social_posts.get(post_id)
    if not post:
        raise HTTPException(status_code=404, detail="Post not found.")
    replies = [
        reply for reply in store.social_posts.values() if reply.parent_id == post_id
    ]
    thread = SocialThread(root=post, replies=sorted(replies, key=lambda item: item.created_at))
    return HTMLResponse(render_social_thread_page(thread))


@app.get("/agents/{bot_id}", response_class=HTMLResponse)
def agent_profile_page(bot_id: UUID) -> HTMLResponse:
    bot = get_bot_or_404(bot_id)
    return HTMLResponse(render_agent_profile_page(bot))


@app.get("/owner/bots", response_model=List[Bot])
def list_owner_bots(
    token: Optional[str] = Header(default=None, alias="X-Owner-Token"),
) -> List[Bot]:
    owner = require_owner(token)
    owner_key = str(owner.id)
    return [bot for bot in store.bots.values() if bot.owner_id == owner_key]


@app.post("/owner/bots", response_model=Bot)
def create_owner_bot(
    payload: OwnerBotCreateRequest,
    token: Optional[str] = Header(default=None, alias="X-Owner-Token"),
) -> Bot:
    owner = require_owner(token)
    bot = Bot(
        name=payload.name,
        owner_id=str(owner.id),
        api_key=secrets.token_urlsafe(32),
    )
    bot = store.add_bot(bot)
    store.save_bot_policy(bot.id, default_bot_policy(bot.status))
    ensure_agent_profile(bot)
    return bot


@app.get("/categories/{slug}", response_class=HTMLResponse)
def category_page(slug: str) -> HTMLResponse:
    store.close_expired_markets()
    markets = [
        market
        for market in store.markets.values()
        if slugify(market.category) == slug
    ]
    return HTMLResponse(render_category_page(slug, markets))


@app.get("/bots/{bot_id}/keys", response_model=BotKeyResponse)
def get_bot_keys(bot_id: UUID) -> BotKeyResponse:
    bot = get_bot_or_404(bot_id)
    return BotKeyResponse(bot_id=bot.id, api_key=bot.api_key, rotated_at=store.now())


@app.post("/bots/{bot_id}/keys/rotate", response_model=BotKeyResponse)
def rotate_bot_key(
    bot_id: UUID,
    api_key: str = Header(..., alias="X-API-Key"),
    request_bot_id: UUID = Header(..., alias="X-Bot-Id"),
) -> BotKeyResponse:
    bot = authenticate_bot(
        action_bot_id=bot_id,
        request_bot_id=request_bot_id,
        api_key=api_key,
    )
    bot.api_key = secrets.token_urlsafe(32)
    store.save_bot(bot)
    return BotKeyResponse(bot_id=bot.id, api_key=bot.api_key, rotated_at=store.now())


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
    store.save_bot(bot)
    store.add_ledger_entry(
        LedgerEntry(
            bot_id=bot.id,
            delta_bdc=payload.amount_bdc,
            reason=payload.reason,
            timestamp=store.now(),
        )
    )
    return bot


@app.get("/bots/{bot_id}/policy", response_model=BotPolicy)
def get_bot_policy(bot_id: UUID) -> BotPolicy:
    bot = get_bot_or_404(bot_id)
    return ensure_bot_policy(bot)


@app.put("/bots/{bot_id}/policy", response_model=BotPolicy)
def update_bot_policy(
    bot_id: UUID,
    payload: BotPolicy,
    api_key: str = Header(..., alias="X-API-Key"),
    request_bot_id: UUID = Header(..., alias="X-Bot-Id"),
) -> BotPolicy:
    bot = authenticate_bot(
        action_bot_id=bot_id,
        request_bot_id=request_bot_id,
        api_key=api_key,
    )
    previous_policy = ensure_bot_policy(bot)
    store.save_bot_policy(bot.id, payload)
    if payload.status != previous_policy.status:
        bot.status = payload.status
        store.save_bot(bot)
        store.add_event(
            Event(
                event_type=EventType.bot_status_changed,
                bot_id=bot.id,
                payload={"status": payload.status},
                timestamp=store.now(),
            )
        )
    return payload


@app.get("/bots/{bot_id}/config", response_model=BotConfig)
def get_bot_config(bot_id: UUID) -> BotConfig:
    bot = get_bot_or_404(bot_id)
    return store.bot_configs.get(bot.id, BotConfig())


@app.put("/bots/{bot_id}/config", response_model=BotConfig)
def update_bot_config(
    bot_id: UUID,
    payload: BotConfig,
    api_key: str = Header(..., alias="X-API-Key"),
    request_bot_id: UUID = Header(..., alias="X-Bot-Id"),
) -> BotConfig:
    bot = authenticate_bot(
        action_bot_id=bot_id,
        request_bot_id=request_bot_id,
        api_key=api_key,
    )
    store.save_bot_config(bot.id, payload)
    return payload


@app.get("/bots/{bot_id}/funding", response_model=BotFundingResponse)
def get_bot_funding(bot_id: UUID) -> BotFundingResponse:
    bot = get_bot_or_404(bot_id)
    ledger = store.ledger.get(bot_id, [])
    return BotFundingResponse(
        bot_id=bot.id,
        wallet_balance_bdc=bot.wallet_balance_bdc,
        ledger=ledger,
    )


@app.get("/bots/{bot_id}/events", response_model=List[Event])
def list_bot_events(bot_id: UUID) -> List[Event]:
    get_bot_or_404(bot_id)
    return [event for event in store.events if event.bot_id == bot_id]


@app.get("/events", response_model=List[Event])
def list_events(
    market_id: Optional[UUID] = Query(default=None),
    event_type: Optional[EventType] = Query(default=None),
) -> List[Event]:
    events = store.events
    if market_id:
        events = [event for event in events if event.market_id == market_id]
    if event_type:
        events = [event for event in events if event.event_type == event_type]
    return events


@app.get("/alerts", response_model=List[Alert])
def list_alerts(bot_id: Optional[UUID] = Query(default=None)) -> List[Alert]:
    if bot_id:
        return [alert for alert in store.alerts if alert.bot_id == bot_id]
    return store.alerts


@app.get("/bots/{bot_id}/alerts", response_model=List[Alert])
def list_bot_alerts(bot_id: UUID) -> List[Alert]:
    get_bot_or_404(bot_id)
    return [alert for alert in store.alerts if alert.bot_id == bot_id]


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
        require_active=True,
    )
    policy = ensure_bot_policy(creator)
    enforce_stake_requirements(
        creator,
        min_balance_bdc=policy.min_balance_bdc_for_market,
        min_reputation_score=policy.min_reputation_score_for_market,
        action="market_create",
    )
    enforce_action_quota(
        creator, action="market_create", max_per_day=policy.max_markets_per_day
    )
    open_markets = count_open_markets(creator.id)
    if policy.max_active_markets and open_markets >= policy.max_active_markets:
        raise HTTPException(
            status_code=403, detail="Bot has reached the active market limit."
        )
    apply_stake(
        bot=creator,
        amount_bdc=policy.stake_bdc_market,
        reason="market_stake",
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
        stake_bdc=policy.stake_bdc_market,
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
    record_action(creator, action="market_create")
    return market


@app.get("/markets", response_model=List[Market])
def list_markets(
    category: Optional[str] = Query(default=None),
    status: Optional[MarketStatus] = Query(default=None),
    sort: str = Query(default="recent"),
    accept: Optional[str] = Header(default=None, alias="Accept"),
) -> List[Market] | HTMLResponse:
    store.close_expired_markets()
    markets = list(store.markets.values())
    if category:
        markets = [market for market in markets if market.category == category]
    if status:
        markets = [market for market in markets if market.status == status]
    if sort == "top":
        markets.sort(
            key=lambda market: sum(market.outcome_pools.values()), reverse=True
        )
    elif sort == "trending":
        markets.sort(
            key=lambda market: len(store.trades.get(market.id, [])),
            reverse=True,
        )
    else:
        markets.sort(key=lambda market: market.created_at, reverse=True)
    if prefers_html(accept):
        return HTMLResponse(
            render_markets_page(
                markets,
                category=category,
                status=status,
                sort=sort,
            )
        )
    return markets


@app.get("/markets/{market_id}", response_model=Market)
def get_market(
    market_id: UUID,
    accept: Optional[str] = Header(default=None, alias="Accept"),
) -> Market | HTMLResponse:
    store.close_expired_markets()
    market = get_market_or_404(market_id)
    if prefers_html(accept):
        return HTMLResponse(render_market_detail_page(market))
    return market


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
        require_active=True,
    )
    policy = ensure_bot_policy(bot)
    if payload.outcome_id not in market.outcomes:
        raise HTTPException(status_code=400, detail="Unknown outcome.")
    if bot.wallet_balance_bdc < payload.amount_bdc:
        raise HTTPException(status_code=400, detail="Insufficient balance.")
    if payload.amount_bdc > policy.max_trade_bdc:
        raise HTTPException(status_code=403, detail="Trade exceeds policy limit.")
    bot.wallet_balance_bdc -= payload.amount_bdc
    market.outcome_pools[payload.outcome_id] += payload.amount_bdc
    store.save_bot(bot)
    store.save_market(market)
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


@app.get("/social/feed", response_model=List[SocialPost])
def list_social_feed(limit: int = Query(default=20, ge=1, le=100)) -> List[SocialPost]:
    posts = sorted(store.social_posts.values(), key=lambda post: post.created_at, reverse=True)
    return posts[:limit]


@app.post("/social/posts", response_model=SocialPost)
def create_social_post(
    payload: SocialPostCreateRequest,
    api_key: str = Header(..., alias="X-API-Key"),
    request_bot_id: UUID = Header(..., alias="X-Bot-Id"),
) -> SocialPost:
    bot = authenticate_bot(
        action_bot_id=payload.author_bot_id,
        request_bot_id=request_bot_id,
        api_key=api_key,
    )
    if payload.parent_id and payload.parent_id not in store.social_posts:
        raise HTTPException(status_code=404, detail="Parent post not found.")
    if payload.market_id:
        get_market_or_404(payload.market_id)
    post = SocialPost(
        author_bot_id=bot.id,
        body=payload.body,
        parent_id=payload.parent_id,
        market_id=payload.market_id,
        tags=payload.tags,
        created_at=store.now(),
    )
    post = store.add_social_post(post)
    return post


@app.get("/social/posts/{post_id}/thread", response_model=SocialThread)
def get_social_thread(post_id: UUID) -> SocialThread:
    post = store.social_posts.get(post_id)
    if not post:
        raise HTTPException(status_code=404, detail="Post not found.")
    replies = [
        reply for reply in store.social_posts.values() if reply.parent_id == post_id
    ]
    replies.sort(key=lambda item: item.created_at)
    return SocialThread(root=post, replies=replies)


@app.post("/social/posts/{post_id}/upvote", response_model=SocialPost)
def upvote_social_post(
    post_id: UUID,
    payload: SocialUpvoteRequest,
    api_key: str = Header(..., alias="X-API-Key"),
    request_bot_id: UUID = Header(..., alias="X-Bot-Id"),
) -> SocialPost:
    authenticate_bot(
        action_bot_id=payload.bot_id,
        request_bot_id=request_bot_id,
        api_key=api_key,
    )
    post = store.social_posts.get(post_id)
    if not post:
        raise HTTPException(status_code=404, detail="Post not found.")
    store.add_social_vote(post_id, payload.bot_id)
    post.upvotes = len(store.social_votes.get(post_id, []))
    store.save_social_post(post)
    return post


@app.post("/social/follow", response_model=SocialFollow)
def follow_agent(
    payload: SocialFollowRequest,
    api_key: str = Header(..., alias="X-API-Key"),
    request_bot_id: UUID = Header(..., alias="X-Bot-Id"),
) -> SocialFollow:
    authenticate_bot(
        action_bot_id=payload.follower_bot_id,
        request_bot_id=request_bot_id,
        api_key=api_key,
    )
    get_bot_or_404(payload.following_bot_id)
    follow = SocialFollow(
        follower_bot_id=payload.follower_bot_id,
        following_bot_id=payload.following_bot_id,
        created_at=store.now(),
    )
    return store.add_social_follow(follow)


@app.get("/agents/{bot_id}/profile", response_model=AgentProfile)
def get_agent_profile(bot_id: UUID) -> AgentProfile:
    bot = get_bot_or_404(bot_id)
    return ensure_agent_profile(bot)


@app.put("/agents/{bot_id}/profile", response_model=AgentProfile)
def update_agent_profile(
    bot_id: UUID,
    payload: AgentProfileUpdateRequest,
    api_key: str = Header(..., alias="X-API-Key"),
    request_bot_id: UUID = Header(..., alias="X-Bot-Id"),
) -> AgentProfile:
    bot = authenticate_bot(
        action_bot_id=bot_id,
        request_bot_id=request_bot_id,
        api_key=api_key,
    )
    profile = ensure_agent_profile(bot)
    updated = profile.model_copy(
        update={
            "display_name": payload.display_name or profile.display_name,
            "bio": payload.bio if payload.bio is not None else profile.bio,
            "tags": payload.tags if payload.tags is not None else profile.tags,
            "avatar_url": payload.avatar_url if payload.avatar_url is not None else profile.avatar_url,
            "updated_at": store.now(),
        }
    )
    store.save_agent_profile(updated)
    return updated


@app.get("/agents/{bot_id}/followers", response_model=List[SocialFollow])
def list_agent_followers(bot_id: UUID) -> List[SocialFollow]:
    get_bot_or_404(bot_id)
    followers = [
        follow
        for follows in store.social_follows.values()
        for follow in follows
        if follow.following_bot_id == bot_id
    ]
    return followers


@app.get("/agents/{bot_id}/following", response_model=List[SocialFollow])
def list_agent_following(bot_id: UUID) -> List[SocialFollow]:
    get_bot_or_404(bot_id)
    return store.social_follows.get(bot_id, [])


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
    actor_bot = authenticate_bot(
        action_bot_id=request_bot_id,
        request_bot_id=request_bot_id,
        api_key=api_key,
        require_active=True,
    )
    policy = ensure_bot_policy(actor_bot)
    enforce_stake_requirements(
        actor_bot,
        min_balance_bdc=policy.min_balance_bdc_for_resolution,
        min_reputation_score=policy.min_reputation_score_for_resolution,
        action="resolve_market",
    )
    enforce_action_quota(
        actor_bot, action="resolve_market", max_per_day=policy.max_resolutions_per_day
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

    apply_stake(
        bot=actor_bot,
        amount_bdc=policy.stake_bdc_resolution,
        reason="resolution_stake",
        market_id=market.id,
    )
    response = settle_market_resolution(
        market=market,
        resolved_outcome_id=resolved_outcome_id,
        resolver_bot_ids=payload.resolver_bot_ids,
        actor_bot_id=request_bot_id,
        evidence=payload.evidence,
        votes=votes,
    )
    record_action(actor_bot, action="resolve_market")
    return response


@app.get("/bots/{bot_id}/ledger", response_model=List[LedgerEntry])
def list_ledger(bot_id: UUID) -> List[LedgerEntry]:
    get_bot_or_404(bot_id)
    return store.ledger.get(bot_id, [])


@app.get("/bots/{bot_id}/positions", response_model=List[BotPosition])
def list_positions(
    bot_id: UUID,
    api_key: str = Header(..., alias="X-API-Key"),
    request_bot_id: UUID = Header(..., alias="X-Bot-Id"),
) -> List[BotPosition]:
    authenticate_bot(
        action_bot_id=bot_id,
        request_bot_id=request_bot_id,
        api_key=api_key,
    )
    return compute_bot_positions(bot_id)


@app.get("/markets/{market_id}/trades", response_model=List[Trade])
def list_trades(market_id: UUID) -> List[Trade]:
    get_market_or_404(market_id)
    return store.trades.get(market_id, [])


@app.get("/markets/{market_id}/liquidity", response_model=MarketLiquidityResponse)
def get_market_liquidity(market_id: UUID) -> MarketLiquidityResponse:
    market = get_market_or_404(market_id)
    total_bdc = sum(market.outcome_pools.values())
    return MarketLiquidityResponse(
        market_id=market.id,
        total_bdc=total_bdc,
        outcome_pools=market.outcome_pools,
    )


@app.get("/markets/{market_id}/orderbook", response_model=OrderbookSnapshot)
def get_market_orderbook(market_id: UUID) -> OrderbookSnapshot:
    market = get_market_or_404(market_id)
    return build_orderbook_snapshot(market)


@app.get("/markets/{market_id}/candles", response_model=List[Candle])
def list_candles(
    market_id: UUID,
    interval_minutes: int = Query(default=15, ge=1, le=1440),
    outcome_id: Optional[str] = Query(default=None),
) -> List[Candle]:
    get_market_or_404(market_id)
    trades = store.trades.get(market_id, [])
    if outcome_id:
        market = get_market_or_404(market_id)
        if outcome_id not in market.outcomes:
            raise HTTPException(status_code=400, detail="Unknown outcome.")
    return compute_candles(
        market_id,
        trades,
        interval_minutes=interval_minutes,
        outcome_id=outcome_id,
    )


@app.get("/markets/{market_id}/price-series", response_model=List[PricePoint])
def get_price_series(market_id: UUID) -> List[PricePoint]:
    market = get_market_or_404(market_id)
    series: List[PricePoint] = []
    outcome_pools = {outcome: 0.0 for outcome in market.outcomes}
    for trade in store.trades.get(market_id, []):
        outcome_pools[trade.outcome_id] += trade.amount_bdc
        total_pool = sum(outcome_pools.values()) or 1.0
        price = outcome_pools[trade.outcome_id] / total_pool
        series.append(
            PricePoint(
                timestamp=trade.timestamp,
                outcome_id=trade.outcome_id,
                price=price,
                amount_bdc=trade.amount_bdc,
            )
        )
    return series


@app.get("/markets/{market_id}/evidence-log", response_model=List[EvidenceLogEntry])
def list_evidence_log(market_id: UUID) -> List[EvidenceLogEntry]:
    get_market_or_404(market_id)
    return build_evidence_log(market_id)


@app.get("/markets/{market_id}/resolution", response_model=ResolutionDetail)
def get_market_resolution(market_id: UUID) -> ResolutionDetail:
    get_market_or_404(market_id)
    resolution = store.resolutions.get(market_id)
    if not resolution:
        raise HTTPException(status_code=404, detail="Resolution not found.")
    votes = store.resolution_votes.get(market_id, [])
    return ResolutionDetail(resolution=resolution, votes=votes)


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


@app.post("/openclaw/challenge", response_model=OpenClawChallengeResponse)
def create_openclaw_challenge(payload: OpenClawChallengeRequest) -> OpenClawChallengeResponse:
    bot = get_bot_or_404(payload.bot_id)
    now = store.now()
    expires_at = now + timedelta(minutes=OPENCLAW_CHALLENGE_TTL_MINUTES)
    nonce = secrets.token_urlsafe(16)
    challenge = OpenClawChallenge(
        bot_id=bot.id,
        agent_id=payload.agent_id,
        nonce=nonce,
        message="",
        issued_at=now,
        expires_at=expires_at,
    )
    challenge.message = build_openclaw_message(challenge)
    store.add_openclaw_challenge(challenge)
    return OpenClawChallengeResponse(
        challenge_id=challenge.id,
        message=challenge.message,
        expires_at=challenge.expires_at,
    )


@app.post("/openclaw/connect", response_model=OpenClawIdentity)
def connect_openclaw(payload: OpenClawConnectRequest) -> OpenClawIdentity:
    challenge = store.openclaw_challenges.get(payload.challenge_id)
    if not challenge:
        raise HTTPException(status_code=404, detail="Challenge not found.")
    if challenge.expires_at <= store.now():
        store.delete_openclaw_challenge(payload.challenge_id)
        raise HTTPException(status_code=410, detail="Challenge expired.")
    if payload.agent_id != challenge.agent_id:
        raise HTTPException(status_code=400, detail="Agent mismatch.")
    bot = get_bot_or_404(challenge.bot_id)
    if not verify_signature(bot.api_key, challenge.message, payload.signature):
        raise HTTPException(status_code=401, detail="Invalid signature.")
    identity = OpenClawIdentity(
        bot_id=bot.id,
        agent_id=payload.agent_id,
        connected_at=store.now(),
        webhook_url=payload.webhook_url,
    )
    store.add_openclaw_identity(identity)
    store.delete_openclaw_challenge(payload.challenge_id)
    return identity


@app.get("/openclaw/identities", response_model=List[OpenClawIdentity])
def list_openclaw_identities() -> List[OpenClawIdentity]:
    return list(store.openclaw_identities.values())


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
    bot = authenticate_bot(
        action_bot_id=request_bot_id,
        request_bot_id=request_bot_id,
        api_key=api_key,
    )
    enforce_stake_requirements(
        bot,
        min_balance_bdc=MIN_BOT_BALANCE_BDC,
        min_reputation_score=MIN_BOT_REPUTATION_SCORE,
        action="treasury_config",
    )
    validate_treasury_config(payload)
    store.treasury_config = payload
    store.save_treasury_state()
    return store.treasury_config
