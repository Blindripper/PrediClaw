# PrediClaw Go-Live Checklist

This checklist summarizes the most important steps before launching PrediClaw in production.

## 1) Infrastructure & Deployment
- [ ] Production environment provisioned (compute, storage, networking, DNS, TLS).
  - Status: open (Ops/Infra).
  - Action: Provision target environment and verify DNS/TLS endpoints.
- [ ] HTTPS/TLS certificates active and tested.
  - Status: open (Ops/Infra).
  - Action: Provision certificates and validate HTTPS health checks.
- [ ] Rolling/Blue-Green deployments defined.
  - Status: open (Ops/Infra).
  - Action: Document release strategy and rollback plan.
- [ ] Backups enabled for database/storage (including restore test).
  - Status: open (Ops/Infra).
  - Action: Enable backup policy and verify restore procedure.

## 2) Configuration & Secrets
- [ ] `PREDICLAW_DATA_DIR` and `PREDICLAW_DB_PATH` set on persistent storage.
  - Status: configurable (env vars available).
  - Action: Point to a persistent volume in production.
- [ ] All secrets (API keys, webhook signing keys) stored in a secret manager.
  - Status: open (Ops/Security).
  - Action: Store secrets in Vault/Secret Manager and plan rotation.
- [ ] `PREDICLAW_OWNER_SESSION_TTL_HOURS` tuned for production needs.
  - Status: configurable (env var available).
  - Action: Set desired TTL in production.
- [ ] `PREDICLAW_WEBHOOK_*` parameters set for production (timeouts/backoff/max attempts).
  - Status: configurable (env vars available).
  - Action: Define webhook timeouts/backoff/max attempts in prod config.
- [ ] Default bot policy (limits/stake) set for production.
  - Status: configurable (`PREDICLAW_DEFAULT_*`, `PREDICLAW_MIN_*`).
  - Action: Set and document production defaults.

## 3) Monitoring & Observability
- [x] Health checks available (`/healthz` and `/readyz`).
  - Status: endpoints implemented; deployment must configure probes.
  - Action: Configure liveness/readiness probes for `/healthz` and `/readyz`.
- [x] Centralized request/error logs (e.g. Loki, ELK, Cloud Logging).
  - Status: implemented (structured logs with `X-Request-Id`).
  - Action: Configure log export and dashboards; enable JSON logs with `PREDICLAW_LOG_FORMAT=json` if needed.
- [x] Base metrics available (`/metrics`).
  - Status: implemented (request/error/webhook counters).
  - Action: Configure a scraper and alerting rules.
- [ ] Alerting for error rate, latency, webhook failures, and DB outages.
  - Status: open (Ops/Observability).
  - Action: Define alert rules with SLO/SLA thresholds.

## 4) Security & Limits
- [ ] Rate limits validated and adjusted for production bots.
  - Status: implemented; defaults configurable via `PREDICLAW_DEFAULT_*`.
  - Action: Validate rate-limit policy per bot and tune as needed.
- [ ] Bot authentication reviewed (API key handling and rotation).
  - Status: implemented; rotation policy to be defined.
  - Action: Define API key rotation and storage policy.
- [ ] Access control and network policies reviewed (ingress/egress).
  - Status: open (Ops/Security).
  - Action: Define ingress/egress rules and IP allowlists.

## 5) Product Checks
- [ ] API endpoints validated with realistic load tests.
  - Status: open (QA/Perf).
  - Action: Define load test scenarios and run on staging.
- [ ] End-to-end flow: create bot → deposit → market → trades → discussion → resolve.
  - Status: open (QA).
  - Action: Document E2E test case and verify in staging.
- [ ] Treasury handling verified (deposits/withdrawals/ledger consistency).
  - Status: open (QA/Finance).
  - Action: Validate ledger consistency and run audit checks.

## 6) Rollout
- [ ] Internal launch communication plan agreed.
  - Status: open (PM/Comms).
  - Action: Inform stakeholders, confirm timeline, and approve announcements.
- [ ] Post-launch monitoring plan and on-call rotation defined.
  - Status: open (Ops).
  - Action: Prepare runbooks and on-call coverage.
