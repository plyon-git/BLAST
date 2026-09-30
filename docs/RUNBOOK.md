# Operating runbook

## Before each campaign

1. Confirm simulation or production in the campaign and dashboard. Production requires explicit deployment enablement, fresh provider verification, reviewed registration/configuration, and healthy signed inbound/callback paths.
2. Review the import report. Phone data and `consent=true` do not establish proof. Verify the named business, SMS channel, seller outreach purpose, source, timestamp, disclosure version, and evidence reference.
3. Confirm recipient timezone evidence. Hold records whose location is uncertain.
4. Write a truthful template with business identity and `Reply STOP to unsubscribe`. Preview actual selected contact/property records; resolve missing fields.
5. Approve each template version and any variants. Validate the campaign, recipient set, authorized service/sender, timing, rate, volume, and cost limits.
6. Approve and schedule. Monitor queue age, delivery diagnostics, classified replies, health, and unknown attempts while the worker runs.

Routine time-window, frequency, throughput, and budget gates defer a queued item within its original expiry. They do not extend expiry or authorize an after-hours send. Permanent eligibility/content failures remain blocked; unhealthy essentials pause dispatch for review. An unknown external outcome is never a routine deferral.

## STOP, removal requests, wrong numbers, and complaints

- Keep suppression authoritative by normalized phone number across business senders. Never clear it through a CSV reimport, recreated contact, campaign clone, or alternate number.
- Standard STOP events and clear requests such as “remove me” cancel pending outreach and record revocation. Advanced Opt-Out already sends its matched confirmation; Blastio sends no duplicate automatic confirmation.
- Wrong-number and complaint records require suppression and a recorded reason. Ambiguous requests or ordinary replies hold automated outreach and route to the inbox for review.
- Record the owner and lead status. Qualified interest, negative replies, wrong numbers, and opt-outs are separate categories. STOP is never successful engagement.
- If a message has already been accepted externally, suppression may not recall it. Preserve the provider SID, attempt timestamps, and suppression time for review.

Restore eligibility only after evidence of renewed consent is collected and reviewed. The evidence timestamp must be newer than the suppression. The provider's own opt-out state may also require the recipient to resubscribe through a supported path. Do not send an unsolicited text to obtain that resubscription.

An ordinary reply's automation hold has a separate reviewed release action. An authorized reviewer may resume automation only after verified, nonsuppressed consent and confirmed recipient timezone are present. Record a substantive review note. This clears the hold for future new campaigns; it does not revive canceled messages or old sequences. The API is `POST /api/contacts/{id}/resume-automation` with `review_note`.

## Pause and incident response

Use the global kill switch immediately for suspected registration restrictions, broken inbound processing, credential exposure, suppression failures, or a broad delivery incident. Use a campaign pause for an isolated campaign. Stop the worker process too if the application itself is unavailable or cannot persist state.

1. Preserve diagnostics, audit events, provider SIDs, message versions, and consent evidence. Do not include credentials in exported incident notes.
2. Review the first failure, affected senders/campaigns, thresholds, callback timestamps, unknown attempts, and queue age. Check Twilio Console alerts and [status](https://status.twilio.com/).
3. For filtering, inspect error 30007, message substance, registration fit, and proof. Follow [Twilio's diagnostic guidance](https://www.twilio.com/docs/api/errors/30007); collect at least three examples when reporting a suspected filtering mistake to support.
4. For 21610, preserve suppression and investigate the recipient state. Never retry from another sender.
5. Repair the cause, reverify the connection and signed webhook paths, revalidate queued content/timing, and review the pause reason. Resume explicitly only after that review. Campaign resume requires a recorded review note at `POST /api/campaigns/{id}/resume`; it preserves eligible queued work and may release reviewed, unattempted blocked items, within their original expiry. It never resends accepted, failed, unknown, canceled, or expired attempts. Global resume is a separate administrator action.

Do not resume by substituting phone numbers or changing wording to bypass restrictions. Configurable thresholds are internal triggers, not a guarantee against carrier action.

Fresh synthetic self-probes do not excuse overdue actual delivery callbacks. Unresolved accepted/sent production messages beyond the health interval pause their campaign. Provider authentication, permission, and rate-limit restrictions can pause the business globally; review the account condition before resuming other campaigns too.

## Unknown attempts and worker recovery

A transport timeout or process crash during dispatch can occur after provider acceptance. The application retains the attempt as **unknown** instead of resetting it to queued. Do not edit the database to requeue it.

1. Pause the affected campaign and inspect its attempt history.
2. If a provider SID is known, fetch/reconcile that exact resource through diagnostics. Check current provider status and price where available. `POST /api/messages/{id}/reconcile` performs a read-only provider fetch and records the result; it does not create a replacement message.
3. If no SID is known, correlate Console logs by recipient, service/sender, exact body, and dispatch time. Matching is an investigation aid, not proof of uniqueness. You may submit a manually evidenced SID and a review note; the application checks recipient, body, service, and time against the held attempt before binding it. Preserve uncertainty when you cannot prove an outcome.
4. Record findings in incident/audit evidence. Never blindly create a replacement message.
5. Restart the separate worker after storage and health are restored. Stale dispatch reservations recover into held unknown attempts; expired queued items must not be sent later.

Out-of-order and duplicate callbacks must not regress final state. `accepted` or `sent` does not prove delivery; a delivery receipt does not prove that a seller read or wanted the message.

## Credentials and revocation

1. Pause globally and stop dispatch before replacement.
2. Create a replacement appropriately scoped Twilio API key in Console. Keep the account Auth Token only for required account checks and signature validation, encrypted server-side.
3. Save replacement credentials in Twilio settings; verify read-only account/service/campaign/sender facts. Saving or verification does not send SMS.
4. For an Auth Token rotation, update application signature validation and perform signed probes before resuming. Ensure Twilio is using the matching token during the switch.
5. Revoke the old key in Console. Clearing Blastio's stored key does not revoke it at Twilio.
6. Review audit history and external Twilio logs for unauthorized use. Never place key secrets in tickets, URLs, exported reports, or source control.

## Evidence export, backup, and upgrades

Export contact/message diagnostics and audit history from the application, then preserve the associated consent evidence files referenced by the records. A reference is not a stored copy of its source document. Limit exports to authorized staff; they contain phone numbers and conversation content.

Back up the complete SQLite database using SQLite's backup API, with the credential encryption key secured separately. Keep suppression history, consents, attempts, inbound events, and audit history intact. Test a restore into an isolated simulation environment before relying on it. Follow [deployment instructions](DEPLOYMENT.md) for the backup procedure.

For upgrades: pause, stop the worker, take a verified backup, install the reviewed release, initialize/apply its migrations, run synthetic verification against a separate database, then start the API and worker. Recheck provider/webhook health before explicit resume.
