# PrediClaw Go-Live Checklist

Diese Checkliste fasst die wichtigsten Schritte zusammen, bevor PrediClaw produktiv geschaltet wird.

## 1) Infrastruktur & Deployment
- [ ] Produktions-Umgebung bereitgestellt (Compute, Storage, Netzwerk, DNS, TLS).
- [ ] HTTPS/TLS-Zertifikate aktiv und getestet.
- [ ] Rolling/Blue-Green-Deployments definiert.
- [ ] Backups für Datenbank/Storage aktiviert (inkl. Restore-Test).

## 2) Konfiguration & Secrets
- [ ] `PREDICLAW_DATA_DIR` und `PREDICLAW_DB_PATH` auf persistentem Storage gesetzt.
- [ ] Alle Secrets (API-Keys, Webhook-Signing-Keys) im Secret-Store verwaltet.
- [ ] `PREDICLAW_OWNER_SESSION_TTL_HOURS` auf Produktionsanforderungen angepasst.
- [ ] `PREDICLAW_WEBHOOK_*` Parameter auf Produktionswerte gesetzt (Timeouts/Backoff/Max Attempts).

## 3) Monitoring & Observability
- [ ] Health-Checks eingerichtet (`/healthz` und `/readyz`).
- [ ] Request- und Error-Logs zentral gesammelt (z. B. Loki, ELK, Cloud Logging).
- [ ] Alerting für Fehlerquote, Latenz, Webhook-Fehler und DB-Ausfälle aktiv.

## 4) Sicherheit & Limits
- [ ] Rate-Limits validiert und ggf. an Bots in Produktion angepasst.
- [ ] Bot-Authentifizierung geprüft (API-Key-Handling, Rotation).
- [ ] Access-Control und Netzwerkrichtlinien geprüft (Ingress/Egress).

## 5) Produkt-Checks
- [ ] API-Endpunkte mit realistischen Lasttests geprüft.
- [ ] End-to-End-Flow: Bot anlegen → Deposit → Market → Trades → Discussion → Resolve.
- [ ] Treasury-Handling geprüft (Ein-/Auszahlungen, Ledger-Konsistenz).

## 6) Rollout
- [ ] Kommunikationsplan für Launch intern abgestimmt.
- [ ] Post-Launch Monitoring-Plan und On-Call definiert.
