from __future__ import annotations

from collections import deque
from datetime import datetime, timedelta, timezone

from fastapi.testclient import TestClient
from uuid import UUID

from prediclaw import api
from prediclaw.storage import InMemoryStore


def build_bot(
    client: TestClient, name: str, activate: bool = True
) -> tuple[dict, dict]:
    response = client.post("/bots", json={"name": name, "owner_id": "owner"})
    assert response.status_code == 200
    bot = response.json()
    headers = {"X-API-Key": bot["api_key"], "X-Bot-Id": bot["id"]}
    if activate:
        policy_response = client.put(
            f"/bots/{bot['id']}/policy",
            json={
                "status": "active",
                "max_requests_per_minute": api.MAX_BOT_REQUESTS_PER_MINUTE,
                "max_active_markets": 5,
                "max_trade_bdc": 500.0,
                "notes": "test activation",
            },
            headers=headers,
        )
        assert policy_response.status_code == 200
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


def build_evidence(source: str, description: str) -> dict:
    return {
        "source": source,
        "description": description,
    }


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

        discussion_response = client.post(
            f"/markets/{market['id']}/discussion",
            json={
                "bot_id": bot["id"],
                "outcome_id": "YES",
                "body": "Bullish momentum",
                "confidence": 0.72,
            },
            headers=headers,
        )
        assert discussion_response.status_code == 200

        api.store.markets[UUID(market["id"])].closes_at = datetime.now(
            timezone.utc
        ) - timedelta(minutes=1)

        resolve_response = client.post(
            f"/markets/{market['id']}/resolve",
            json={
                "resolver_bot_ids": [bot["id"]],
                "resolved_outcome_id": "YES",
                "evidence": [build_evidence("oracle", "primary feed")],
            },
            headers=headers,
        )
        assert resolve_response.status_code == 200
        resolve_payload = resolve_response.json()
        assert resolve_payload["resolution"]["resolved_outcome_id"] == "YES"
        assert resolve_payload["resolution"]["evidence"]

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


def test_market_filters_liquidity_and_resolution_details() -> None:
    with setup_client() as client:
        bot, headers = build_bot(client, "alpha")
        client.post(
            f"/bots/{bot['id']}/deposit",
            json={"amount_bdc": 40.0, "reason": "seed"},
            headers=headers,
        )
        market = build_market(
            client,
            headers,
            bot["id"],
            datetime.now(timezone.utc) + timedelta(hours=2),
        )
        list_response = client.get("/markets", params={"status": "open"})
        assert list_response.status_code == 200
        assert any(item["id"] == market["id"] for item in list_response.json())

        trade_response = client.post(
            f"/markets/{market['id']}/trades",
            json={"bot_id": bot["id"], "outcome_id": "YES", "amount_bdc": 10.0},
            headers=headers,
        )
        assert trade_response.status_code == 200

        liquidity_response = client.get(f"/markets/{market['id']}/liquidity")
        assert liquidity_response.status_code == 200
        assert liquidity_response.json()["total_bdc"] == 10.0

        series_response = client.get(f"/markets/{market['id']}/price-series")
        assert series_response.status_code == 200
        assert series_response.json()

        api.store.markets[UUID(market["id"])].closes_at = datetime.now(
            timezone.utc
        ) - timedelta(minutes=1)

        resolve_response = client.post(
            f"/markets/{market['id']}/resolve",
            json={
                "resolver_bot_ids": [bot["id"]],
                "resolved_outcome_id": "YES",
                "evidence": [build_evidence("oracle", "primary feed")],
            },
            headers=headers,
        )
        assert resolve_response.status_code == 200

        resolution_response = client.get(
            f"/markets/{market['id']}/resolution"
        )
        assert resolution_response.status_code == 200
        assert (
            resolution_response.json()["resolution"]["resolved_outcome_id"]
            == "YES"
        )


def test_majority_policy_resolves_with_votes_and_evidence() -> None:
    with setup_client() as client:
        bot_alpha, headers_alpha = build_bot(client, "alpha")
        bot_beta, headers_beta = build_bot(client, "beta")
        bot_gamma, headers_gamma = build_bot(client, "gamma")
        for bot, headers in [
            (bot_alpha, headers_alpha),
            (bot_beta, headers_beta),
            (bot_gamma, headers_gamma),
        ]:
            client.post(
                f"/bots/{bot['id']}/deposit",
                json={"amount_bdc": 30.0, "reason": "seed"},
                headers=headers,
            )
        market = build_market(
            client,
            headers_alpha,
            bot_alpha["id"],
            datetime.now(timezone.utc) + timedelta(hours=1),
            resolver_policy="majority",
        )

        api.store.markets[UUID(market["id"])].closes_at = datetime.now(
            timezone.utc
        ) - timedelta(minutes=1)

        resolve_response = client.post(
            f"/markets/{market['id']}/resolve",
            json={
                "resolver_bot_ids": [
                    bot_alpha["id"],
                    bot_beta["id"],
                    bot_gamma["id"],
                ],
                "votes": [
                    {
                        "resolver_bot_id": bot_alpha["id"],
                        "outcome_id": "YES",
                        "evidence": [build_evidence("bot", "signal a")],
                    },
                    {
                        "resolver_bot_id": bot_beta["id"],
                        "outcome_id": "YES",
                        "evidence": [build_evidence("bot", "signal b")],
                    },
                    {
                        "resolver_bot_id": bot_gamma["id"],
                        "outcome_id": "NO",
                        "evidence": [build_evidence("bot", "signal c")],
                    },
                ],
            },
            headers=headers_alpha,
        )
        assert resolve_response.status_code == 200
        payload = resolve_response.json()
        assert payload["resolution"]["resolved_outcome_id"] == "YES"


def test_consensus_policy_resolves_by_weighted_reputation() -> None:
    with setup_client() as client:
        bot_alpha, headers_alpha = build_bot(client, "alpha")
        bot_beta, headers_beta = build_bot(client, "beta")
        client.post(
            f"/bots/{bot_alpha['id']}/deposit",
            json={"amount_bdc": 30.0, "reason": "seed"},
            headers=headers_alpha,
        )
        client.post(
            f"/bots/{bot_beta['id']}/deposit",
            json={"amount_bdc": 30.0, "reason": "seed"},
            headers=headers_beta,
        )
        api.store.bots[UUID(bot_alpha["id"])].reputation_score = 2.0
        api.store.bots[UUID(bot_beta["id"])].reputation_score = 1.0

        market = build_market(
            client,
            headers_alpha,
            bot_alpha["id"],
            datetime.now(timezone.utc) + timedelta(hours=1),
            resolver_policy="consensus",
        )

        api.store.markets[UUID(market["id"])].closes_at = datetime.now(
            timezone.utc
        ) - timedelta(minutes=1)

        resolve_response = client.post(
            f"/markets/{market['id']}/resolve",
            json={
                "resolver_bot_ids": [bot_alpha["id"], bot_beta["id"]],
                "votes": [
                    {
                        "resolver_bot_id": bot_alpha["id"],
                        "outcome_id": "NO",
                        "evidence": [build_evidence("bot", "strong signal")],
                    },
                    {
                        "resolver_bot_id": bot_beta["id"],
                        "outcome_id": "YES",
                        "evidence": [build_evidence("bot", "weak signal")],
                    },
                ],
            },
            headers=headers_alpha,
        )
        assert resolve_response.status_code == 200
        payload = resolve_response.json()
        assert payload["resolution"]["resolved_outcome_id"] == "NO"
