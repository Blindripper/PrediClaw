from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from uuid import UUID

from prediclaw import api
from prediclaw.storage import InMemoryStore


def build_bot(client: TestClient, name: str) -> tuple[dict, dict]:
    response = client.post("/bots", json={"name": name, "owner_id": "owner"})
    assert response.status_code == 200
    bot = response.json()
    headers = {"X-API-Key": bot["api_key"], "X-Bot-Id": bot["id"]}
    return bot, headers


def build_market(
    client: TestClient,
    headers: dict,
    bot_id: str,
    closes_at: datetime,
    resolver_policy: str = "single",
) -> dict:
    payload = {
        "creator_bot_id": bot_id,
        "title": "BTC > 80k?",
        "description": "Short test market",
        "category": "crypto",
        "outcomes": ["YES", "NO"],
        "closes_at": closes_at.isoformat(),
        "resolver_policy": resolver_policy,
    }
    response = client.post("/markets", json=payload, headers=headers)
    assert response.status_code == 200
    return response.json()


def setup_client() -> TestClient:
    api.store = InMemoryStore()
    return TestClient(api.app)


def test_core_flow_bot_deposit_market_trade_resolve_ledger() -> None:
    with setup_client() as client:
        bot, headers = build_bot(client, "alpha")
        deposit_response = client.post(
            f"/bots/{bot['id']}/deposit",
            json={"amount_bdc": 50.0, "reason": "seed"},
            headers=headers,
        )
        assert deposit_response.status_code == 200

        market = build_market(
            client,
            headers,
            bot["id"],
            datetime.now(timezone.utc) + timedelta(hours=1),
        )

        trade_response = client.post(
            f"/markets/{market['id']}/trades",
            json={"bot_id": bot["id"], "outcome_id": "YES", "amount_bdc": 20.0},
            headers=headers,
        )
        assert trade_response.status_code == 200

        api.store.markets[UUID(market["id"])].closes_at = datetime.now(
            timezone.utc
        ) - timedelta(minutes=1)

        resolve_response = client.post(
            f"/markets/{market['id']}/resolve",
            json={
                "resolver_bot_ids": [bot["id"]],
                "resolved_outcome_id": "YES",
                "evidence": "oracle",
            },
            headers=headers,
        )
        assert resolve_response.status_code == 200
        resolve_payload = resolve_response.json()
        assert resolve_payload["resolution"]["resolved_outcome_id"] == "YES"

        ledger_response = client.get(f"/bots/{bot['id']}/ledger")
        assert ledger_response.status_code == 200
        reasons = [entry["reason"] for entry in ledger_response.json()]
        assert reasons == ["seed", "trade", "payout"]


def test_bot_auth_mismatch_rejected() -> None:
    with setup_client() as client:
        bot, _ = build_bot(client, "alpha")
        _, headers_other = build_bot(client, "beta")
        response = client.post(
            f"/bots/{bot['id']}/deposit",
            json={"amount_bdc": 10.0, "reason": "seed"},
            headers=headers_other,
        )
        assert response.status_code == 403


def test_rate_limit_blocks_excess_requests() -> None:
    with setup_client() as client:
        bot, headers = build_bot(client, "alpha")
        now = api.store.now()
        api.store.bot_request_log[UUID(bot["id"])] = deque(
            [now] * api.MAX_BOT_REQUESTS_PER_MINUTE
        )
        response = client.post(
            f"/bots/{bot['id']}/deposit",
            json={"amount_bdc": 5.0, "reason": "seed"},
            headers=headers,
        )
        assert response.status_code == 429


def test_majority_policy_requires_multiple_resolvers() -> None:
    with setup_client() as client:
        bot, headers = build_bot(client, "alpha")
        client.post(
            f"/bots/{bot['id']}/deposit",
            json={"amount_bdc": 20.0, "reason": "seed"},
            headers=headers,
        )
        market = build_market(
            client,
            headers,
            bot["id"],
            datetime.now(timezone.utc) + timedelta(hours=1),
            resolver_policy="majority",
        )

        response = client.post(
            f"/markets/{market['id']}/resolve",
            json={"resolver_bot_ids": [bot["id"]]},
            headers=headers,
        )
        assert response.status_code == 400
