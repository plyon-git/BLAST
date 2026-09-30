# Controlled production smoke test

This is a procedure for the account operator. Development tests use simulation. Do not run this procedure against a real seller list. The repository has not been validated with live Twilio credentials or real carrier delivery.

## 1. Prepare an isolated test

1. Choose one or two explicitly authorized test recipients controlled by the operator. Retain business/purpose-specific consent proof and verified recipient timezone.
2. Use a dedicated Blastio database and persistent encryption key. Keep global pause enabled and the worker stopped initially. Never point this test at the production recipient database.
3. Confirm the existing registered service/campaign and eligible sender in Console. Do not create, rotate, remove, or reregister production resources for this test.
4. Deploy exact public HTTPS webhook URLs and secure cookies. Set low reviewed application/campaign limits and an ending time for the test.

## 2. Prove no-send connection

1. Save Account SID, API Key SID/secret, encrypted account Auth Token, and selected Messaging Service in Twilio settings.
2. Run read-only verification. Compare timestamps, service/campaign IDs, and sender membership with Console. Complete the identified manual review items.
3. Check Twilio message logs: connecting, saving, and verification must have created no message. The worker is still stopped.
4. Review the saved settings response and browser source/network payloads after saving. Secrets must be masked and never returned as plain saved values.

## 3. Prove webhook reachability without SMS

Run the signed synthetic inbound and status callback probes in Twilio settings. They target `/webhooks/twilio/probe/inbound` and `/webhooks/twilio/probe/status`, exercise storage inside a rolled-back savepoint, and persist only health/probe metadata. These are HTTPS requests to Blastio, not Twilio SMS sends. Confirm separate successful timestamps and inspect diagnostics. In the isolated environment, send an invalid signature to the actual `/webhooks/twilio/inbound` and `/webhooks/twilio/status` endpoints and confirm rejection without creating an inbound record or changing a message.

Synthetic probes establish current reachability and local signature handling, not that Twilio will call the URL, that a carrier will deliver SMS, or that the account registration is safe. Review Console inbound/callback URLs and the exact proxy/public URL independently. Complete any manual Console setup explicitly; Blastio does not silently change it.

## 4. Exercise policy blocks

1. Import the authorized test contact with pending consent. A preview/validation must show blocked eligibility.
2. Review the original evidence and verify its business, channel, purpose, timestamp, disclosure version, source, and reference. Verify timezone from the recipient, not area code/property inference.
3. Create a short approved template, such as `{{business_name}} test for our authorized SMS check. Did this arrive? Reply STOP to unsubscribe`.
4. Verify that an unapproved new template version, missing timezone, missing merge field, active suppression, stale webhook health, campaign pause, and global pause each prevent dispatch. Perform these separately in the isolated test database.

## 5. Send only the authorized check

1. Review and record the authorized test plan, recipient proof, sender/service, exact message, timing, and one-message limit. Set `smoke_test_reviewed` to attest that this plan is reviewed. This prerequisite permits the initial controlled check and is not a claim that a live test already passed.
2. Complete production enablement and campaign validation/approval. Review all reasons and costs. Schedule within the verified recipient's local window.
3. Reverify provider resources and signed paths immediately before the send. Clear pause after recording test authorization, then start one worker.
4. Allow exactly the reviewed test message. Pause globally afterward and stop the worker. Inspect provider SID and states through callback reconciliation.
5. Confirm the handset text and business identity, response path, and sender consistency. Reconcile price when Twilio provides it. A single delivery demonstrates only that delivery.

## 6. Exercise opt-out and retry boundaries

1. With pending future test outreach and worker stopped, have the authorized recipient send `STOP`. Confirm a real signed inbound event, business-wide suppression, revocation history, and cancellation of pending sends.
2. Confirm Twilio's standard confirmation occurs according to Console settings. Blastio must not send an extra automatic confirmation.
3. Reimport that contact or duplicate its property; create another campaign on another authorized sender. Validation must remain blocked by suppression.
4. Do not automatically restore on START or a positive reply. If restoration is needed, obtain and review newer consent evidence and inspect the provider's own opt-out state.
5. Exercise timeout/crash/duplicate/out-of-order conditions with simulation or automated tests, not by repeatedly sending real messages. Unknown attempts must remain held.

## 7. Record acceptance evidence

Record release commit, test time, responsible operator, authorized test contact evidence, verified timezone source, service/campaign/sender identifiers, manual configuration review, webhook probe timestamps, provider SIDs, callback state, error/price details, suppression result, and any unresolved issues. Do not record secrets.

Update the readiness review note with the observed results and review any failures. Clear `smoke_test_reviewed` if prerequisites or results remain unresolved. Leave global pause enabled after the test until the operator separately reviews a real campaign. A completed smoke test is not authorization to contact arbitrary uploaded recipients.
