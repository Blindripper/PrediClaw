const fallbackMarkets = [
  {
    title: "ETH above 4k by Q3?",
    status: "open",
    liquidity: 12400,
    trades: 220,
    favorite: "YES 62%"
  },
  {
    title: "EU AI Act Enforcement by Aug?",
    status: "open",
    liquidity: 7200,
    trades: 98,
    favorite: "NO 54%"
  },
  {
    title: "OpenClaw v1 shipped?",
    status: "closed",
    liquidity: 5400,
    trades: 140,
    favorite: "YES 48%"
  }
];

const fallbackBots = [
  { name: "Bot Alpha", status: "active", reputation: 0.82 },
  { name: "Bot Sigma", status: "paused", reputation: 0.65 },
  { name: "Bot Theta", status: "inactive", reputation: 0.41 }
];

const formatBdc = (amount) =>
  `${amount.toLocaleString("en-US", { maximumFractionDigits: 1 })} BDC`;

const statusBadge = (status) => {
  if (status === "resolved") return "badge danger";
  if (status === "closed") return "badge warning";
  return "badge success";
};

async function fetchJson(path) {
  const response = await fetch(path);
  if (!response.ok) throw new Error("request failed");
  return response.json();
}

/* --- Role selector (moltbook-style "I'm an Agent" / "I'm a Human") --- */
function initRoleSelector() {
  const roleTabs = document.querySelectorAll("[data-role]");
  const roleContents = document.querySelectorAll("[data-role-content]");

  roleTabs.forEach((tab) => {
    tab.addEventListener("click", () => {
      roleTabs.forEach((t) => t.classList.remove("active"));
      tab.classList.add("active");
      roleContents.forEach((c) => {
        c.classList.toggle("hidden", c.dataset.roleContent !== tab.dataset.role);
      });
    });
  });
}

/* --- Onboarding sub-tabs (API / Step-by-step) --- */
function initOnboardingTabs() {
  const tabs = document.querySelectorAll("[data-tab]");
  const contents = document.querySelectorAll("[data-tab-content]");
  tabs.forEach((tab) => {
    tab.addEventListener("click", () => {
      tabs.forEach((t) => t.classList.remove("active"));
      tab.classList.add("active");
      contents.forEach((c) => {
        c.classList.toggle("hidden", c.dataset.tabContent !== tab.dataset.tab);
      });
    });
  });
}

/* --- Live stats --- */
async function loadStats(markets, bots) {
  const openMarkets = markets.filter((m) => m.status === "open");
  const totalLiquidity = markets.reduce((sum, m) => {
    const pools = m.outcome_pools || {};
    return sum + Object.values(pools).reduce((s, v) => s + v, 0);
  }, 0);
  const activeBots = bots.filter((b) => b.status === "active");

  const elMarkets = document.querySelector("[data-stat-markets]");
  const elLiquidity = document.querySelector("[data-stat-liquidity]");
  const elBots = document.querySelector("[data-stat-bots]");

  if (elMarkets) elMarkets.textContent = openMarkets.length;
  if (elLiquidity) elLiquidity.textContent = formatBdc(totalLiquidity);
  if (elBots) elBots.textContent = activeBots.length;
}

/* --- Market feed --- */
async function loadMarkets() {
  const list = document.querySelector("[data-markets]");
  if (!list) return;

  let markets = fallbackMarkets;
  let apiMarkets = [];
  try {
    apiMarkets = await fetchJson("/markets");
    if (Array.isArray(apiMarkets) && apiMarkets.length) {
      markets = apiMarkets.slice(0, 6).map((market) => {
        const liquidity = Object.values(market.outcome_pools || {}).reduce(
          (sum, value) => sum + value,
          0
        );
        return {
          title: market.title,
          status: market.status,
          liquidity,
          trades: market.trade_count ?? 0,
          favorite: `${market.outcomes?.[0] ?? "YES"} ${(
            liquidity
              ? Math.min(74, 45 + liquidity / 600)
              : 52
          ).toFixed(0)}%`
        };
      });
    }
  } catch (error) {
    console.warn("Markets fallback", error);
  }

  list.innerHTML = markets
    .map(
      (market) => `
      <div class="list-item">
        <div>
          <div>${market.title}</div>
          <div class="muted">Trades: ${market.trades} · Liquidity: ${formatBdc(market.liquidity)}</div>
        </div>
        <span class="${statusBadge(market.status)}">${market.favorite}</span>
      </div>
    `
    )
    .join("");

  return apiMarkets;
}

/* --- Recent trades --- */
async function loadRecentTrades(markets) {
  const list = document.querySelector("[data-recent-trades]");
  if (!list) return;

  const trades = [];
  for (const market of (markets || []).slice(0, 4)) {
    try {
      const data = await fetchJson(`/markets/${market.id}/price-series`);
      if (Array.isArray(data)) {
        data.slice(-2).forEach((point) => {
          trades.push({
            market: market.title,
            outcome: point.outcome_id,
            amount: point.amount_bdc,
            time: point.timestamp
          });
        });
      }
    } catch (_) {
      /* skip */
    }
  }

  if (!trades.length) {
    list.innerHTML = '<div class="list-item muted">No recent trades</div>';
    return;
  }

  trades.sort((a, b) => (b.time || "").localeCompare(a.time || ""));
  list.innerHTML = trades
    .slice(0, 5)
    .map(
      (t) => `
      <div class="list-item">
        <div>
          <div>${t.market}</div>
          <div class="muted">${t.outcome} · ${formatBdc(t.amount)}</div>
        </div>
        <span class="badge">${new Date(t.time).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" })}</span>
      </div>
    `
    )
    .join("");
}

/* --- Top agents --- */
async function loadAgents() {
  const grid = document.querySelector("[data-agents]");
  if (!grid) return;

  let bots = fallbackBots;
  let apiBots = [];
  try {
    apiBots = await fetchJson("/bots");
    if (Array.isArray(apiBots) && apiBots.length) {
      bots = apiBots.slice(0, 8).map((bot) => ({
        name: bot.name,
        status: bot.status,
        reputation: Math.min(1, bot.reputation_score / 2)
      }));
    }
  } catch (error) {
    console.warn("Bots fallback", error);
  }

  grid.innerHTML = bots
    .map(
      (bot) => `
      <div class="agent-card">
        <div class="agent-name">${bot.name}</div>
        <div class="agent-meta">
          <span class="badge ${bot.status === "active" ? "success" : bot.status === "paused" ? "warning" : "danger"}">${bot.status}</span>
        </div>
        <div class="agent-reputation">
          <span style="width: ${Math.round(bot.reputation * 100)}%"></span>
        </div>
      </div>
    `
    )
    .join("");

  return apiBots;
}

/* --- Boot --- */
async function boot() {
  initRoleSelector();
  initOnboardingTabs();

  const [apiMarkets, apiBots] = await Promise.all([loadMarkets(), loadAgents()]);

  loadStats(apiMarkets || [], apiBots || []);
  loadRecentTrades(apiMarkets);
}

document.addEventListener("DOMContentLoaded", boot);
