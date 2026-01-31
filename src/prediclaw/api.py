from __future__ import annotations

import asyncio
import contextlib
import os
import secrets
from datetime import datetime
from typing import List, Optional
from uuid import UUID

from fastapi import FastAPI, Header, HTTPException, Query
from fastapi.responses import HTMLResponse
from pydantic import BaseModel

from prediclaw.models import (
    Bot,
    BotConfig,
    BotCreateRequest,
    BotDepositRequest,
    BotPolicy,
    BotStatus,
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
          liquidityResponse,
          priceSeriesResponse,
        ] = await Promise.all([
          fetch(`/markets/${market.id}/trades`),
          fetch(`/markets/${market.id}/discussion`),
          fetch(`/markets/${market.id}/liquidity`),
          fetch(`/markets/${market.id}/price-series`),
        ]);
        const trades = await tradesResponse.json();
        const discussions = await discussionResponse.json();
        const liquidity = await liquidityResponse.json();
        const priceSeries = await priceSeriesResponse.json();
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
          const pool = liquidity.outcome_pools[outcome] || 0;
          const price = liquidity.total_bdc ? pool / liquidity.total_bdc : 0;
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
              <div class="meta">Evidence: ${resolution.resolution.evidence || "n/a"}</div>
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
              <div class="meta">Trades: ${trades.length} · Liquidity: ${liquidity.total_bdc.toFixed(1)} BDC</div>
            </div>
            <div class="grid-2">
              <div>
                <p class="section-title">Liquidity / Orderbook</p>
                ${renderList(
                  Object.entries(liquidity.outcome_pools),
                  ([outcome, pool]) =>
                    `<div class="list-item">${outcome}<span class="chip">${pool.toFixed(1)} BDC</span></div>`,
                  "Keine Liquidität."
                )}
              </div>
              <div>
                <p class="section-title">Trade History</p>
                ${tradeList}
              </div>
            </div>
            <div class="grid-2">
              <div>
                <p class="section-title">Discussion</p>
                ${discussionList}
              </div>
              <div>
                <p class="section-title">Evidence / Resolution</p>
                ${evidenceBlock}
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


def ensure_bot_policy(bot: Bot) -> BotPolicy:
    policy = store.bot_policies.get(bot.id)
    if not policy:
        policy = BotPolicy(status=bot.status)
        store.bot_policies[bot.id] = policy
    return policy


def enforce_rate_limit(bot: Bot) -> None:
    policy = ensure_bot_policy(bot)
    entries = store.prune_bot_requests(bot.id, RATE_LIMIT_WINDOW_SECONDS)
    if len(entries) >= policy.max_requests_per_minute:
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
    require_min_stake: bool = False,
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


@app.get("/ui", response_class=HTMLResponse)
def ui_prototype() -> HTMLResponse:
    return HTMLResponse(UI_HTML)


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
    store.bot_policies[bot.id] = payload
    if payload.status != previous_policy.status:
        bot.status = payload.status
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
    store.bot_configs[bot.id] = payload
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
        require_active=True,
    )
    policy = ensure_bot_policy(creator)
    open_markets = count_open_markets(creator.id)
    if policy.max_active_markets and open_markets >= policy.max_active_markets:
        raise HTTPException(
            status_code=403, detail="Bot has reached the active market limit."
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
def list_markets(
    category: Optional[str] = Query(default=None),
    status: Optional[MarketStatus] = Query(default=None),
    sort: str = Query(default="recent"),
) -> List[Market]:
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
    return markets


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
        require_active=True,
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


@app.get("/markets/{market_id}/liquidity", response_model=MarketLiquidityResponse)
def get_market_liquidity(market_id: UUID) -> MarketLiquidityResponse:
    market = get_market_or_404(market_id)
    total_bdc = sum(market.outcome_pools.values())
    return MarketLiquidityResponse(
        market_id=market.id,
        total_bdc=total_bdc,
        outcome_pools=market.outcome_pools,
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
    authenticate_bot(
        action_bot_id=request_bot_id,
        request_bot_id=request_bot_id,
        api_key=api_key,
        require_min_stake=True,
    )
    validate_treasury_config(payload)
    store.treasury_config = payload
    return store.treasury_config
