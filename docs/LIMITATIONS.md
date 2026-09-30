# Delivery status and limitations

## Implemented source

The repository contains the local FastAPI application, responsive operator interface, authentication and server-side roles, encrypted saved credentials, contacts/properties, CSV preview and confirmation, consent/suppression records, versioned templates, campaigns, inbox, diagnostics, audit history, SQLite initialization migration (`migrations/001_initial.sql`), and a separate durable worker.

Outbound paths use the same server-side policy gate. Production dispatch uses a real Twilio REST adapter, not the simulation adapter. Provider verification reads existing resources and never alters registration, sender pools, or Console settings. Sending remains disabled unless production prerequisites are met.

## What test evidence can establish

The automated suite uses synthetic data and controlled provider outcomes. It is intended to check policy blocking, suppression persistence, opt-out races, deduplication, consent restoration, template immutability, timezone handling, failed/unknown attempts, webhook signatures/state transitions, authorization, and stop controls.

Install `requirements-dev.txt` and run `python -m pytest -q` for the actual result of your checkout. No claim here substitutes for that result. Mocked responses and signed synthetic probes do not prove live account permissions, an actual carrier delivery, or protection from filtering/suspension. Live Twilio calls and handset tests must be performed by the account operator using [the smoke-test procedure](SMOKE_TEST.md).

## Manual production prerequisites

- Existing approved registration, correct messaging use case and message flow, service association, and eligible sender membership.
- Console review of Advanced Opt-Out, supported keywords/responses, Sticky Sender where applicable, inbound handling, callbacks, and provider validity period.
- Scoped API access and the account Auth Token needed for webhook signature validation.
- Public HTTPS, exact configured URL, secured cookies, persistent encryption key, protected host/disk, and tested backups.
- Documented recipient consent and current timezone evidence.
- Explicit operator review of daily caps, campaign limits, incident thresholds, and registration fit.
- Controlled tests with explicitly authorized recipients before any seller campaign.

## Known operational limits

1. **Single host and database.** SQLite WAL is suitable for this deployment shape. Multiple hosts, network-mounted SQLite, multiple API replicas, multiple worker replicas, and high-volume concurrency are unsupported. Move to a properly designed PostgreSQL/queue deployment before scaling; do not just duplicate containers.
2. **Suppression race boundary.** The database coordinates local suppression/dispatch. A STOP received after external provider acceptance may not recall that message. Holding a transaction while dispatching also delays concurrent local writes; keep timeouts short and monitor storage contention.
3. **No exactly-once promise.** Unknown provider outcomes stay held. A crash around acceptance and a callback without an identifiable local message can require manual reconciliation. End-to-end SMS delivery is not an atomic database transaction.
4. **Local caps are local.** Traffic sent by other tools/accounts/services is not incorporated. Segment cost estimates are not Twilio invoices and may omit carrier surcharges, registration fees, inbound charges, taxes, and later pricing updates.
5. **Read-only verification is limited.** API resource state does not establish legal consent, ownership of evidence, approved text compatibility, or all Console-only features. Manual attestations are identified as such; synthetic webhook checks prove reachability and local signature processing at that moment.
6. **Scheduling cannot control handset arrival.** Queue expiry limits provider residence, while networks can delay delivery. Verified timezones can become stale when recipients travel. Refresh evidence when uncertain.
7. **Evidence references need retention.** The application stores references and structured review history, not a full document evidence vault. Maintain original consent disclosures/files securely outside the database and ensure references remain resolvable.
8. **Reply interpretation is deterministic.** Clear stop/wrong-number/complaint terms are handled without AI. Ambiguous or unusual wording can require review. Leads and interest classifications need operator confirmation.
9. **Authentication scope.** Built-in credentials, role checks, sessions, and CSRF protection are implemented. SSO, MFA, enterprise identity lifecycle, host encryption, external log monitoring, and independent security review remain deployment work.
10. **Purpose and tenancy.** This release is a single-business 101XVC SMS application. It is not an email blast program, arbitrary-list prospecting tool, multi-tenant service, or automated legal/registration adviser.
11. **Import bounds.** Preview is in memory: at most 8 MiB, 50,000 rows, 100 columns, 16,384 characters per cell, and 50 custom-field keys. Large source datasets must be divided into supported batches. Malformed records are reported rather than silently corrected.
12. **External account safety.** No software or numerical threshold guarantees freedom from filtering, suspension, or carrier enforcement. Do not treat A2P registration, test credentials, a successful HTTP response, or a delivered test text as approval of a seller list.
13. **Explicit sender routing.** Campaigns choose an authorized sender and submit it as `From` with the registered service. The production policy gate preserves the existing sender across campaigns once a production attempt establishes routing, including unknown, accepted, filtered, and failed outcomes. Sticky Sender can only select automatically when the application leaves sender choice to the service; this release does not expose or manage Twilio Sticky Sender mappings. An unavailable existing sender blocks review instead of silently replacing it.

Reviewed campaign resume and contact automation release preserve policy gates and never revive unknown external outcomes. Manually evidenced SID reconciliation verifies available provider facts, but does not prove that a correlated text was unique or prevent all external duplicates. See [requirements traceability](TRACEABILITY.md) for source/test locations and the boundaries of their evidence.

See [POLICY.md](POLICY.md) for the dated official sources and the internal defaults that are deliberately more restrictive than some provider behaviors.
