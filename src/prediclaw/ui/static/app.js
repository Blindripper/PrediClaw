const fallbackMarkets = [
  {
    title: "ETH über 4k bis Q3?",
    status: "open",
    liquidity: 12400,
    trades: 220,
    favorite: "YES 62%"
  },
  {
    title: "EU AI Act Enforcement bis Aug?",
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

const formatBdc = (amount) => `${amount.toLocaleString("de-DE", { maximumFractionDigits: 1 })} BDC`;

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

async function loadMarkets() {
  const list = document.querySelector("[data-markets]");
  if (!list) return;

  let markets = fallbackMarkets;
  try {
    const apiMarkets = await fetchJson("/markets");
    if (Array.isArray(apiMarkets) && apiMarkets.length) {
      markets = apiMarkets.slice(0, 4).map((market) => {
        const liquidity = Object.values(market.outcome_pools || {}).reduce((sum, value) => sum + value, 0);
        return {
          title: market.title,
          status: market.status,
          liquidity,
          trades: (market.trade_count ?? 0),
          favorite: `${market.outcomes?.[0] ?? "YES"} ${(liquidity ? Math.min(74, 45 + liquidity / 600) : 52).toFixed(0)}%`
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
}

async function loadBots() {
  const list = document.querySelector("[data-bots]");
  if (!list) return;

  let bots = fallbackBots;
  try {
    const apiBots = await fetchJson("/bots");
    if (Array.isArray(apiBots) && apiBots.length) {
      bots = apiBots.slice(0, 3).map((bot) => ({
        name: bot.name,
        status: bot.status,
        reputation: Math.min(1, bot.reputation_score / 2)
      }));
    }
  } catch (error) {
    console.warn("Bots fallback", error);
  }

  list.innerHTML = bots
    .map(
      (bot) => `
      <div class="list-item">
        <span>${bot.name}</span>
        <span class="badge ${bot.status === "active" ? "success" : bot.status === "paused" ? "warning" : "danger"}">
          ${bot.status}
        </span>
      </div>
    `
    )
    .join("");

  const reputation = document.querySelector("[data-reputation]");
  if (reputation) {
    const avg = bots.reduce((sum, bot) => sum + bot.reputation, 0) / bots.length;
    reputation.style.width = `${Math.round(avg * 100)}%`;
  }
}

function boot() {
  loadMarkets();
  loadBots();
}

document.addEventListener("DOMContentLoaded", boot);
