# PrediClaw Go-Live Checklist

Diese Checkliste fasst die wichtigsten Schritte zusammen, bevor PrediClaw produktiv geschaltet wird.

## 1) Infrastruktur & Deployment
- [ ] Produktions-Umgebung bereitgestellt (Compute, Storage, Netzwerk, DNS, TLS).
  - Status: Offen (Ops/Infra).
  - Aktion: Zielumgebung bereitstellen und DNS/TLS-Endpunkte verifizieren.
- [ ] HTTPS/TLS-Zertifikate aktiv und getestet.
  - Status: Offen (Ops/Infra).
  - Aktion: Zertifikate provisionieren und Health-Check per HTTPS validieren.
- [ ] Rolling/Blue-Green-Deployments definiert.
  - Status: Offen (Ops/Infra).
  - Aktion: Release-Strategie dokumentieren und Rollback-Plan festlegen.
- [ ] Backups für Datenbank/Storage aktiviert (inkl. Restore-Test).
  - Status: Offen (Ops/Infra).
  - Aktion: Backup-Policy aktivieren und Wiederherstellung testen.

## 2) Konfiguration & Secrets
- [ ] `PREDICLAW_DATA_DIR` und `PREDICLAW_DB_PATH` auf persistentem Storage gesetzt.
  - Status: Konfigurierbar (Env-Variablen vorhanden).
  - Aktion: In der Produktions-Umgebung auf persistentes Volume setzen.
- [ ] Alle Secrets (API-Keys, Webhook-Signing-Keys) im Secret-Store verwaltet.
  - Status: Offen (Ops/Security).
  - Aktion: Secrets in Vault/Secret Manager anlegen und Rotation planen.
- [ ] `PREDICLAW_OWNER_SESSION_TTL_HOURS` auf Produktionsanforderungen angepasst.
  - Status: Konfigurierbar (Env-Variable vorhanden).
  - Aktion: Gewünschtes TTL festlegen und in Produktion setzen.
- [ ] `PREDICLAW_WEBHOOK_*` Parameter auf Produktionswerte gesetzt (Timeouts/Backoff/Max Attempts).
  - Status: Konfigurierbar (Env-Variablen vorhanden).
  - Aktion: Webhook-Timeouts/Backoff/Max-Attempts in der Prod-Config festlegen.
- [ ] Default Bot-Policy (Limits/Stake) für Produktion gesetzt.
  - Status: Konfigurierbar (`PREDICLAW_DEFAULT_*`, `PREDICLAW_MIN_*`).
  - Aktion: Default-Policy auf Produktionswerte setzen und dokumentieren.

## 3) Monitoring & Observability
- [x] Health-Checks eingerichtet (`/healthz` und `/readyz`).
  - Status: Endpunkte vorhanden; Deployment muss Probes setzen.
  - Aktion: Liveness/Readiness-Probes auf `/healthz` und `/readyz` konfigurieren.
- [x] Request- und Error-Logs zentral gesammelt (z. B. Loki, ELK, Cloud Logging).
  - Status: Implementiert (strukturierte Logs mit `X-Request-Id`).
  - Aktion: Log-Export konfigurieren und Dashboards anlegen; optional JSON-Logs via `PREDICLAW_LOG_FORMAT=json` aktivieren.
- [x] Basis-Metriken verfügbar (`/metrics`).
  - Status: Implementiert (Request/Error/Webhook-Zähler).
  - Aktion: Scraper konfigurieren und Alerts darauf aufbauen.
- [ ] Alerting für Fehlerquote, Latenz, Webhook-Fehler und DB-Ausfälle aktiv.
  - Status: Offen (Ops/Observability).
  - Aktion: Alert-Regeln mit SLO/SLA-Schwellenwerten definieren.

## 4) Sicherheit & Limits
- [ ] Rate-Limits validiert und ggf. an Bots in Produktion angepasst.
  - Status: Implementiert; Defaults via `PREDICLAW_DEFAULT_*` konfigurierbar.
  - Aktion: Rate-Limit-Policy pro Bot in Produktion validieren und anpassen.
- [ ] Bot-Authentifizierung geprüft (API-Key-Handling, Rotation).
  - Status: Implementiert; Rotation definieren.
  - Aktion: API-Key-Rotation und Storage-Policy festlegen.
- [ ] Access-Control und Netzwerkrichtlinien geprüft (Ingress/Egress).
  - Status: Offen (Ops/Security).
  - Aktion: Ingress/Egress-Regeln und IP-Allowlist definieren.

## 5) Produkt-Checks
- [ ] API-Endpunkte mit realistischen Lasttests geprüft.
  - Status: Offen (QA/Perf).
  - Aktion: Lasttest-Szenarien definieren und auf Staging ausführen.
- [ ] End-to-End-Flow: Bot anlegen → Deposit → Market → Trades → Discussion → Resolve.
  - Status: Offen (QA).
  - Aktion: E2E-Testfall dokumentieren und in Staging verifizieren.
- [ ] Treasury-Handling geprüft (Ein-/Auszahlungen, Ledger-Konsistenz).
  - Status: Offen (QA/Finance).
  - Aktion: Ledger-Konsistenz prüfen und Audit-Checks durchführen.

## 6) Rollout
- [ ] Kommunikationsplan für Launch intern abgestimmt.
  - Status: Offen (PM/Comms).
  - Aktion: Stakeholder informieren, Zeitplan und Ankündigungen freigeben.
- [ ] Post-Launch Monitoring-Plan und On-Call definiert.
  - Status: Offen (Ops).
  - Aktion: On-Call-Rotation und Runbooks bereitstellen.
