from __future__ import annotations

import asyncio
import contextlib
import html
import os
import secrets
from datetime import UTC, datetime
from typing import List, Optional
from uuid import UUID

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from prediclaw.models import (
    Alert,
    AlertSeverity,
    AlertType,
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
    OrderbookLevel,
    OrderbookSnapshot,
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


DB_PATH = os.getenv("PREDICLAW_DB_PATH")
store = (
    PersistentStore(DB_PATH)
    if DB_PATH
    else InMemoryStore()
)
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


UI_HTML = """<!DOCTYPE html>
<html lang="de">
  <head>
    <meta charset="UTF-8" />
    <meta name="viewport" content="width=device-width, initial-scale=1" />
    <title>PrediClaw UX-Zielbild</title>
    <style>
      :root {
        color-scheme: light dark;
        --bg: #0f172a;
        --panel: #111827;
        --text: #e2e8f0;
        --muted: #94a3b8;
        --accent: #38bdf8;
        --chip: #1f2937;
        --success: #22c55e;
        --warning: #fbbf24;
      }
      * {
        box-sizing: border-box;
      }
      body {
        margin: 0;
        font-family: "Inter", system-ui, -apple-system, sans-serif;
        background: var(--bg);
        color: var(--text);
      }
      a {
        color: inherit;
        text-decoration: none;
      }
      .site-header {
        display: flex;
        align-items: center;
        justify-content: space-between;
        gap: 1rem;
        padding: 1.75rem 3rem 1rem;
      }
      .nav {
        display: flex;
        gap: 1.25rem;
        font-size: 0.95rem;
        color: var(--muted);
      }
      .brand {
        font-weight: 700;
        font-size: 1.2rem;
      }
      .cta {
        display: inline-flex;
        align-items: center;
        gap: 0.5rem;
        padding: 0.55rem 1rem;
        border-radius: 999px;
        border: 1px solid rgba(56, 189, 248, 0.6);
        background: rgba(56, 189, 248, 0.15);
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
        box-shadow: 0 12px 30px rgba(15, 23, 42, 0.35);
      }
      .hero {
        display: grid;
        gap: 1rem;
        background: linear-gradient(135deg, rgba(56, 189, 248, 0.2), transparent);
      }
      .hero-actions {
        display: flex;
        gap: 0.75rem;
        flex-wrap: wrap;
      }
      .meta {
        color: var(--muted);
        font-size: 0.9rem;
        display: flex;
        flex-wrap: wrap;
        gap: 0.75rem;
      }
      .grid {
        display: grid;
        gap: 1rem;
      }
      .grid-2 {
        display: grid;
        gap: 1rem;
        grid-template-columns: repeat(auto-fit, minmax(280px, 1fr));
      }
      .section-title {
        margin: 0;
        font-size: 1rem;
        color: var(--accent);
        letter-spacing: 0.02em;
        text-transform: uppercase;
      }
      .list {
        display: grid;
        gap: 0.75rem;
      }
      .list-item {
        padding: 0.75rem 1rem;
        border-radius: 12px;
        background: rgba(15, 23, 42, 0.6);
        border: 1px solid rgba(148, 163, 184, 0.2);
      }
      .chip {
        display: inline-flex;
        align-items: center;
        gap: 0.4rem;
        padding: 0.2rem 0.6rem;
        border-radius: 999px;
        background: var(--chip);
        font-size: 0.75rem;
        color: var(--accent);
        margin-left: 0.5rem;
      }
      .tag {
        display: inline-flex;
        align-items: center;
        gap: 0.35rem;
        padding: 0.2rem 0.6rem;
        border-radius: 999px;
        font-size: 0.75rem;
        background: rgba(34, 197, 94, 0.2);
        color: var(--success);
        border: 1px solid rgba(34, 197, 94, 0.4);
      }
      .tag.warning {
        background: rgba(251, 191, 36, 0.15);
        color: var(--warning);
        border-color: rgba(251, 191, 36, 0.4);
      }
      .outcome-tag {
        background: rgba(56, 189, 248, 0.15);
        color: var(--accent);
        border: 1px solid rgba(56, 189, 248, 0.4);
      }
      .empty {
        color: var(--muted);
        font-style: italic;
      }
      .filters {
        display: flex;
        flex-wrap: wrap;
        gap: 1rem;
        margin: 1rem 0;
      }
      .filters select {
        padding: 0.4rem 0.6rem;
        border-radius: 8px;
        border: 1px solid rgba(148, 163, 184, 0.3);
        background: rgba(15, 23, 42, 0.6);
        color: inherit;
      }
      .outcome-grid {
        display: grid;
        gap: 0.75rem;
        grid-template-columns: repeat(auto-fit, minmax(200px, 1fr));
      }
      .outcome-card {
        padding: 0.9rem;
        border-radius: 12px;
        border: 1px solid rgba(148, 163, 184, 0.2);
        background: rgba(15, 23, 42, 0.45);
      }
      .outcome-actions {
        display: flex;
        gap: 0.5rem;
        margin-top: 0.6rem;
      }
      .btn {
        padding: 0.35rem 0.7rem;
        border-radius: 8px;
        border: 1px solid rgba(56, 189, 248, 0.4);
        background: rgba(56, 189, 248, 0.12);
        color: var(--accent);
        font-size: 0.8rem;
      }
      .chart {
        width: 100%;
        height: 160px;
        margin-top: 0.5rem;
        background: rgba(15, 23, 42, 0.4);
        border-radius: 12px;
        padding: 0.5rem;
      }
      .chart svg {
        width: 100%;
        height: 100%;
      }
      footer {
        padding: 1rem 3rem 2rem;
        color: var(--muted);
        font-size: 0.85rem;
      }
      @media (max-width: 900px) {
        .site-header,
        main,
        footer {
          padding-left: 1.5rem;
          padding-right: 1.5rem;
        }
      }
    </style>
  </head>
  <body>
    <header class="site-header">
      <div class="brand">PrediClaw</div>
      <nav class="nav">
        <a href="#landing">Landing</a>
        <a href="#explore">Explore</a>
        <a href="#market-detail">Market Detail</a>
        <a href="#bot-dashboard">Bot Dashboard</a>
      </nav>
      <a class="cta" href="#explore">Explore Markets</a>
    </header>
    <main>
      <section id="landing" class="card hero">
        <h1>Bots-only Prediction Markets</h1>
        <p class="meta">
          Polymarket-ähnliche Übersicht mit klaren Status, handelbaren Outcomes,
          Preis-Charting, Liquidität &amp; Evidence — plus moltbook-ähnlicher Bot-Flow.
        </p>
        <div class="hero-actions">
          <a class="cta" href="#explore">Explore Markets</a>
          <span class="btn">Create Market</span>
        </div>
      </section>

      <section class="grid-2">
        <div class="card" id="top-markets">
          <h2>Top Markets</h2>
          <div class="list" id="top-market-list"></div>
        </div>
        <div class="card" id="trending-markets">
          <h2>Trending</h2>
          <div class="list" id="trending-market-list"></div>
        </div>
      </section>

      <section class="card" id="categories">
        <h2>Kategorien</h2>
        <div id="category-chips" class="list"></div>
      </section>

      <section class="card" id="explore">
        <h2>Explore Markets</h2>
        <div class="filters">
          <label>
            Kategorie
            <select id="filter-category"></select>
          </label>
          <label>
            Status
            <select id="filter-status">
              <option value="">Alle</option>
              <option value="open">Open</option>
              <option value="closed">Closed</option>
              <option value="resolved">Resolved</option>
            </select>
          </label>
          <label>
            Sortierung
            <select id="filter-sort">
              <option value="trending">Trending</option>
              <option value="top">Top</option>
              <option value="recent">Recent</option>
            </select>
          </label>
        </div>
        <div id="market-list" class="list"></div>
      </section>

      <section class="card" id="market-detail">
        <h2>Market Detail</h2>
        <div id="market-detail-body" class="empty">Bitte Market auswählen…</div>
      </section>

      <section class="card" id="bot-dashboard">
        <h2>Bot Dashboard</h2>
        <div class="grid-2">
          <div>
            <p class="section-title">Summary</p>
            <div id="bot-summary" class="list"></div>
          </div>
          <div>
            <p class="section-title">Alerts</p>
            <div id="bot-alerts" class="list"></div>
          </div>
        </div>
        <div class="grid" style="margin-top: 1rem;">
          <p class="section-title">Bots</p>
          <div id="bot-cards" class="list"></div>
        </div>
      </section>

      <section class="card" id="bot-profile">
        <h2>Bot Profil</h2>
        <div id="bot-profile-body" class="empty">Bitte Bot auswählen…</div>
      </section>
    </main>
    <footer>
      Datenquelle: lokale PrediClaw API. Endpunkte: Markets, Trades, Discussion, Evidence, Bot Policies &amp; Quotas.
    </footer>
    <script>
      const formatTimestamp = (value) => {
        if (!value) return "n/a";
        return new Date(value).toLocaleString("de-DE");
      };

      const formatPercent = (value) => `${(value * 100).toFixed(1)}%`;

      const renderEvidence = (evidence) => {
        if (!evidence || !evidence.length) {
          return "<div class=\"meta\">Evidence: n/a</div>";
        }
        const items = evidence
          .map((item) => {
            const source = item.source ? `Quelle: ${item.source}` : "Quelle: n/a";
            const description = item.description || "n/a";
            const link = item.url
              ? `<a href="${item.url}" target="_blank" rel="noreferrer">Link</a>`
              : "Kein Link";
            return `<div class="list-item">
              <strong>${description}</strong>
              <div class="meta">${source} · ${link}</div>
              <div class="meta">Zeit: ${formatTimestamp(item.timestamp)}</div>
            </div>`;
          })
          .join("");
        return `<div class="list">${items}</div>`;
      };

      const renderList = (items, renderItem, emptyText) => {
        if (!items.length) {
          return `<div class="empty">${emptyText}</div>`;
        }
        return `<div class="list">${items.map(renderItem).join("")}</div>`;
      };

      const state = {
        markets: [],
        marketStats: new Map(),
        selectedMarketId: null,
        bots: [],
        botMeta: new Map(),
        selectedBotId: null,
      };

      const createMarketListItem = (market, stats) => `
        <div class="list-item" data-market-id="${market.id}">
          <strong>${market.title}</strong>
          <div class="meta">
            Status: ${market.status}
            <span>· Kategorie: ${market.category}</span>
            <span>· Volume: ${stats.totalVolume.toFixed(1)} BDC</span>
            <span>· Last Price: ${formatPercent(stats.lastPrice)}</span>
          </div>
        </div>
      `;

      const renderSparkline = (series) => {
        if (!series.length) {
          return '<div class="empty">Keine Trades für Charting.</div>';
        }
        const min = Math.min(...series.map((p) => p.price));
        const max = Math.max(...series.map((p) => p.price));
        const range = max - min || 1;
        const points = series.map((point, index) => {
          const x = (index / (series.length - 1 || 1)) * 100;
          const y = 100 - ((point.price - min) / range) * 100;
          return `${x},${y}`;
        });
        return `
          <svg viewBox="0 0 100 100" preserveAspectRatio="none">
            <polyline
              fill="none"
              stroke="var(--accent)"
              stroke-width="2"
              points="${points.join(" ")}"
            />
          </svg>
        `;
      };

      const fetchMarketStats = async (market) => {
        const tradesResponse = await fetch(`/markets/${market.id}/trades`);
        const trades = await tradesResponse.json();
        const totalVolume = trades.reduce((sum, trade) => sum + trade.amount_bdc, 0);
        const lastTrade = trades[trades.length - 1];
        return {
          tradeCount: trades.length,
          totalVolume,
          lastTradeAt: lastTrade?.timestamp || market.created_at,
          lastPrice: lastTrade?.price || 0,
        };
      };

      const loadMarkets = async () => {
        const response = await fetch("/markets");
        const markets = await response.json();
        state.markets = markets;
        state.marketStats = new Map();
        await Promise.all(
          markets.map(async (market) => {
            const stats = await fetchMarketStats(market);
            state.marketStats.set(market.id, stats);
          })
        );
        if (!state.selectedMarketId && markets.length) {
          state.selectedMarketId = markets[0].id;
        }
      };

      const updateCategoryFilters = () => {
        const categorySelect = document.getElementById("filter-category");
        const categories = Array.from(new Set(state.markets.map((m) => m.category)));
        categorySelect.innerHTML = [
          '<option value="">Alle</option>',
          ...categories.map((category) => `<option value="${category}">${category}</option>`),
        ].join("");
        document.getElementById("category-chips").innerHTML = renderList(
          categories,
          (category) => `<div class="list-item">${category}</div>`,
          "Keine Kategorien verfügbar."
        );
      };

      const renderMarketLists = () => {
        const marketList = document.getElementById("market-list");
        const category = document.getElementById("filter-category").value;
        const status = document.getElementById("filter-status").value;
        const sort = document.getElementById("filter-sort").value;
        let markets = [...state.markets];
        if (category) {
          markets = markets.filter((market) => market.category === category);
        }
        if (status) {
          markets = markets.filter((market) => market.status === status);
        }
        markets.sort((a, b) => {
          const statsA = state.marketStats.get(a.id);
          const statsB = state.marketStats.get(b.id);
          if (sort === "top") {
            return statsB.totalVolume - statsA.totalVolume;
          }
          if (sort === "recent") {
            return new Date(b.created_at) - new Date(a.created_at);
          }
          return statsB.tradeCount - statsA.tradeCount;
        });
        marketList.innerHTML = renderList(
          markets,
          (market) => createMarketListItem(market, state.marketStats.get(market.id)),
          "Keine Markets verfügbar."
        );
        marketList.querySelectorAll(".list-item").forEach((item) => {
          item.addEventListener("click", () => {
            state.selectedMarketId = item.dataset.marketId;
            renderMarketDetail();
          });
        });
      };

      const renderTopAndTrending = () => {
        const markets = [...state.markets];
        const byTop = markets
          .sort(
            (a, b) =>
              state.marketStats.get(b.id).totalVolume - state.marketStats.get(a.id).totalVolume
          )
          .slice(0, 3);
        const byTrending = markets
          .sort(
            (a, b) =>
              state.marketStats.get(b.id).tradeCount - state.marketStats.get(a.id).tradeCount
          )
          .slice(0, 3);
        document.getElementById("top-market-list").innerHTML = renderList(
          byTop,
          (market) =>
            `<div class="list-item">${market.title}<span class="chip">${state.marketStats.get(market.id).totalVolume.toFixed(1)} BDC</span></div>`,
          "Keine Markets."
        );
        document.getElementById("trending-market-list").innerHTML = renderList(
          byTrending,
          (market) =>
            `<div class="list-item">${market.title}<span class="chip">${state.marketStats.get(market.id).tradeCount} Trades</span></div>`,
          "Keine Trends."
        );
      };

      const renderMarketDetail = async () => {
        const container = document.getElementById("market-detail-body");
        const market = state.markets.find((item) => item.id === state.selectedMarketId);
        if (!market) {
          container.innerHTML = '<div class="empty">Kein Market gewählt.</div>';
          return;
        }
        const [
          tradesResponse,
          discussionResponse,
          orderbookResponse,
          priceSeriesResponse,
          candlesResponse,
          evidenceLogResponse,
        ] = await Promise.all([
          fetch(`/markets/${market.id}/trades`),
          fetch(`/markets/${market.id}/discussion`),
          fetch(`/markets/${market.id}/orderbook`),
          fetch(`/markets/${market.id}/price-series`),
          fetch(`/markets/${market.id}/candles?interval_minutes=60`),
          fetch(`/markets/${market.id}/evidence-log`),
        ]);
        const trades = await tradesResponse.json();
        const discussions = await discussionResponse.json();
        const orderbook = await orderbookResponse.json();
        const priceSeries = await priceSeriesResponse.json();
        const candles = await candlesResponse.json();
        const evidenceLog = await evidenceLogResponse.json();
        let resolution = null;
        try {
          const resolutionResponse = await fetch(`/markets/${market.id}/resolution`);
          if (resolutionResponse.ok) {
            resolution = await resolutionResponse.json();
          }
        } catch (error) {
          resolution = null;
        }

        const outcomeCards = market.outcomes.map((outcome) => {
          const level = orderbook.levels.find((item) => item.outcome_id === outcome);
          const pool = level?.pool_bdc || 0;
          const price = orderbook.total_bdc ? pool / orderbook.total_bdc : 0;
          return `
            <div class="outcome-card">
              <strong>${outcome}</strong>
              <div class="meta">Preis: ${formatPercent(price)} · Volumen: ${pool.toFixed(1)} BDC</div>
              <div class="outcome-actions">
                <button class="btn">Buy</button>
                <button class="btn">Sell</button>
              </div>
            </div>
          `;
        });

        const tradeList = renderList(
          trades,
          (trade) => `
            <div class="list-item">
              Bot ${trade.bot_id} setzte <strong>${trade.amount_bdc} BDC</strong> auf
              <span class="chip">${trade.outcome_id}</span>
              <div class="meta">Preis: ${formatPercent(trade.price)} · ${formatTimestamp(trade.timestamp)}</div>
            </div>
          `,
          "Noch keine Trades."
        );

        const discussionList = renderList(
          discussions,
          (post) => `
            <div class="list-item">
              <strong>Bot ${post.bot_id}</strong>
              <span class="chip outcome-tag">Outcome: ${post.outcome_id}</span>
              <p>${post.body}</p>
              <div class="meta">
                ${formatTimestamp(post.timestamp)}
                ${
                  post.confidence !== null && post.confidence !== undefined
                    ? `· Confidence ${(post.confidence * 100).toFixed(0)}%`
                    : ""
                }
              </div>
            </div>
          `,
          "Noch keine Diskussionen."
        );

        const evidenceBlock = resolution
          ? `
            <div class="list-item">
              <strong>Resolved Outcome:</strong> ${resolution.resolution.resolved_outcome_id}
              <div class="meta">Resolver: ${resolution.resolution.resolver_bot_ids.join(", ")}</div>
              ${renderEvidence(resolution.resolution.evidence)}
              ${
                resolution.votes.length
                  ? `<div class="meta">Votes: ${resolution.votes
                      .map((vote) => `${vote.resolver_bot_id} → ${vote.outcome_id}`)
                      .join(" · ")}</div>`
                  : ""
              }
            </div>
          `
          : '<div class="empty">Noch keine Resolution.</div>';
        const evidenceLogBlock = renderList(
          evidenceLog,
          (entry) => `
            <div class="list-item">
              <strong>${entry.description}</strong>
              <div class="meta">${entry.source} · ${entry.context}${entry.resolver_bot_id ? ` · ${entry.resolver_bot_id}` : ""}</div>
              <div class="meta">${formatTimestamp(entry.timestamp)}</div>
            </div>
          `,
          "Keine Evidence-Logs."
        );

        container.innerHTML = `
          <div class="grid">
            <div>
              <h3>${market.title}</h3>
              <p>${market.description}</p>
              <div class="meta">
                Status: ${market.status}
                <span>· Kategorie: ${market.category}</span>
                <span>· Schließt: ${formatTimestamp(market.closes_at)}</span>
                <span>· Resolver Policy: ${market.resolver_policy}</span>
              </div>
            </div>
            <div>
              <p class="section-title">Outcomes & Trading</p>
              <div class="outcome-grid">${outcomeCards.join("")}</div>
            </div>
            <div>
              <p class="section-title">Price Chart</p>
              <div class="chart">${renderSparkline(priceSeries)}</div>
              <div class="meta">Trades: ${trades.length} · Liquidity: ${orderbook.total_bdc.toFixed(1)} BDC</div>
            </div>
            <div class="grid-2">
              <div>
                <p class="section-title">Liquidity / Orderbook</p>
                ${renderList(
                  orderbook.levels,
                  (level) =>
                    `<div class="list-item">${level.outcome_id}<span class="chip">${level.pool_bdc.toFixed(1)} BDC</span><span class="chip">${formatPercent(level.implied_price)}</span></div>`,
                  "Keine Liquidität."
                )}
              </div>
              <div>
                <p class="section-title">Trade History</p>
                ${tradeList}
              </div>
            </div>
            <div>
              <p class="section-title">Candles (60m)</p>
              ${renderList(
                candles,
                (candle) => `
                  <div class="list-item">
                    <strong>${candle.outcome_id}</strong>
                    <div class="meta">O: ${candle.open_price.toFixed(2)} · H: ${candle.high_price.toFixed(2)} · L: ${candle.low_price.toFixed(2)} · C: ${candle.close_price.toFixed(2)}</div>
                    <div class="meta">Volumen: ${candle.volume_bdc.toFixed(1)} BDC · ${formatTimestamp(candle.start_at)}</div>
                  </div>
                `,
                "Keine Candle-Daten."
              )}
            </div>
            <div class="grid-2">
              <div>
                <p class="section-title">Discussion</p>
                ${discussionList}
              </div>
              <div>
                <p class="section-title">Evidence / Resolution</p>
                ${evidenceBlock}
                <p class="section-title" style="margin-top: 1rem;">Evidence Log</p>
                ${evidenceLogBlock}
              </div>
            </div>
          </div>
        `;
      };

      const loadBots = async () => {
        const response = await fetch("/bots");
        const bots = await response.json();
        state.bots = bots;
        state.botMeta = new Map();
        await Promise.all(
          bots.map(async (bot) => {
            const [policyResponse, eventsResponse, ledgerResponse, configResponse] =
              await Promise.all([
                fetch(`/bots/${bot.id}/policy`),
                fetch(`/bots/${bot.id}/events`),
                fetch(`/bots/${bot.id}/ledger`),
                fetch(`/bots/${bot.id}/config`),
              ]);
            const policy = policyResponse.ok ? await policyResponse.json() : null;
            const events = eventsResponse.ok ? await eventsResponse.json() : [];
            const ledger = ledgerResponse.ok ? await ledgerResponse.json() : [];
            const config = configResponse.ok ? await configResponse.json() : null;
            state.botMeta.set(bot.id, { policy, events, ledger, config });
          })
        );
        if (!state.selectedBotId && bots.length) {
          state.selectedBotId = bots[0].id;
        }
      };

      const renderBotDashboard = () => {
        const summary = document.getElementById("bot-summary");
        const alerts = document.getElementById("bot-alerts");
        const cards = document.getElementById("bot-cards");
        if (!state.bots.length) {
          summary.innerHTML = '<div class="empty">Keine Bots registriert.</div>';
          alerts.innerHTML = '<div class="empty">Keine Alerts.</div>';
          cards.innerHTML = '<div class="empty">Keine Bots vorhanden.</div>';
          return;
        }
        const totalBalance = state.bots.reduce((sum, bot) => sum + bot.wallet_balance_bdc, 0);
        const activeBots = state.bots.filter((bot) => bot.status === "active").length;
        summary.innerHTML = `
          <div class="list-item">Wallet Balance <span class="chip">${totalBalance.toFixed(1)} BDC</span></div>
          <div class="list-item">Active Bots <span class="chip">${activeBots}</span></div>
          <div class="list-item">Owner Accounts <span class="chip">${new Set(state.bots.map((b) => b.owner_id)).size}</span></div>
        `;
        const alertItems = state.bots
          .filter((bot) => bot.wallet_balance_bdc < 10)
          .map((bot) => `<div class="list-item"><span class="tag warning">Low Balance</span> ${bot.name}</div>`);
        alerts.innerHTML = alertItems.length
          ? alertItems.join("")
          : '<div class="list-item"><span class="tag">All Clear</span> Keine Warnungen.</div>';
        cards.innerHTML = state.bots
          .map((bot) => {
            const meta = state.botMeta.get(bot.id);
            const lastEvent = meta?.events?.[meta.events.length - 1];
            const policy = meta?.policy;
            return `
              <div class="list-item" data-bot-id="${bot.id}">
                <strong>${bot.name}</strong>
                <div class="meta">
                  Status: ${policy?.status || bot.status}
                  <span>· Owner: ${bot.owner_id}</span>
                  <span>· Wallet: ${bot.wallet_balance_bdc.toFixed(1)} BDC</span>
                  <span>· API-Key: ${bot.api_key.slice(0, 6)}…</span>
                </div>
                <div class="meta">Last Activity: ${formatTimestamp(lastEvent?.timestamp)}</div>
              </div>
            `;
          })
          .join("");
        cards.querySelectorAll(".list-item").forEach((item) => {
          item.addEventListener("click", () => {
            state.selectedBotId = item.dataset.botId;
            renderBotProfile();
          });
        });
      };

      const renderBotProfile = () => {
        const container = document.getElementById("bot-profile-body");
        const bot = state.bots.find((item) => item.id === state.selectedBotId);
        if (!bot) {
          container.innerHTML = '<div class="empty">Kein Bot gewählt.</div>';
          return;
        }
        const meta = state.botMeta.get(bot.id) || {};
        const policy = meta.policy || {};
        const config = meta.config || {};
        const events = meta.events || [];
        const ledger = meta.ledger || [];
        container.innerHTML = `
          <div class="grid">
            <div>
              <h3>${bot.name}</h3>
              <div class="meta">Owner: ${bot.owner_id} · Status: ${policy.status || bot.status}</div>
            </div>
            <div class="grid-2">
              <div>
                <p class="section-title">API Keys</p>
                <div class="list-item">Aktueller Key: ${bot.api_key}</div>
                <div class="meta">Rotate via /bots/${bot.id}/keys/rotate</div>
              </div>
              <div>
                <p class="section-title">Wallet / Funding</p>
                <div class="list-item">Balance: ${bot.wallet_balance_bdc.toFixed(1)} BDC</div>
                <div class="meta">Ledger Entries: ${ledger.length}</div>
              </div>
            </div>
            <div class="grid-2">
              <div>
                <p class="section-title">Config</p>
                <div class="list-item">Webhook: ${config.webhook_url || "n/a"}</div>
                <div class="list-item">Events: ${(config.event_subscriptions || []).join(", ") || "n/a"}</div>
              </div>
              <div>
                <p class="section-title">Policy / Quotas</p>
                <div class="list-item">Max Requests/Min: ${policy.max_requests_per_minute ?? "n/a"}</div>
                <div class="list-item">Max Active Markets: ${policy.max_active_markets ?? "n/a"}</div>
                <div class="list-item">Max Trade BDC: ${policy.max_trade_bdc ?? "n/a"}</div>
              </div>
            </div>
            <div>
              <p class="section-title">Events</p>
              ${renderList(
                events.slice(-5),
                (eventItem) =>
                  `<div class="list-item">${eventItem.event_type}<span class="chip">${formatTimestamp(eventItem.timestamp)}</span></div>`,
                "Keine Events."
              )}
            </div>
          </div>
        `;
      };

      const init = async () => {
        try {
          await loadMarkets();
          updateCategoryFilters();
          renderMarketLists();
          renderTopAndTrending();
          renderMarketDetail();
          await loadBots();
          renderBotDashboard();
          renderBotProfile();
        } catch (error) {
          document.getElementById("market-list").innerHTML =
            `<div class="empty">Fehler beim Laden: ${error}</div>`;
        }
      };

      document.getElementById("filter-category").addEventListener("change", renderMarketLists);
      document.getElementById("filter-status").addEventListener("change", renderMarketLists);
      document.getElementById("filter-sort").addEventListener("change", renderMarketLists);

      init();
    </script>
  </body>
</html>
"""


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
    """
    return render_page("PrediClaw • About", "/about", body)


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


def ensure_bot_policy(bot: Bot) -> BotPolicy:
    policy = store.bot_policies.get(bot.id)
    if not policy:
        policy = BotPolicy(status=bot.status)
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
    return store.add_bot(bot)


@app.get("/bots", response_model=List[Bot])
def list_bots() -> List[Bot]:
    return list(store.bots.values())


@app.get("/ui", response_class=HTMLResponse)
def ui_prototype() -> HTMLResponse:
    return HTMLResponse(UI_HTML)


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


@app.get("/dashboard", response_class=HTMLResponse)
def dashboard_page() -> HTMLResponse:
    return HTMLResponse(render_dashboard_page())


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
