# Policy decisions and sources

Research date: **2026-09-30**. Official sources below were retrieved on that date. The Messaging Policy states **Last Updated April 13, 2026**. The documentation pages do not provide a reliable page revision date; the retrieval date is not a publication date. Recheck these sources before production and when Twilio notifies you of changes.

## Provider requirements

| Requirement | Blastio implementation | Official source |
| --- | --- | --- |
| Retain recipient, sender, and subject-specific consent evidence; identify the sender. | Evidence records and explicit review; final-message identification gate. Purchased or uploaded phone data never verifies consent. | [Messaging Policy](https://www.twilio.com/en-us/legal/messaging-policy) |
| Provide clear, simple opt-out; honor withdrawal and subsequent renewed consent. | Default `Reply STOP to unsubscribe`; business-wide suppression; reviewed renewed evidence. | [Messaging Policy](https://www.twilio.com/en-us/legal/messaging-policy) |
| Do not evade detection with misspellings, misleading opt-out, snowshoeing, or unwanted bulk messaging. | Reviewed fixed templates; no evasion features or bypass routing. | [Messaging Policy](https://www.twilio.com/en-us/legal/messaging-policy) |
| Register the relevant US long-code brand, campaign, service, and senders. | Read existing registration associations; require usable status and operator review. No registration mutations. | [A2P overview](https://www.twilio.com/docs/messaging/compliance/a2p-10dlc), [Direct onboarding](https://www.twilio.com/docs/messaging/compliance/a2p-10dlc/direct-standard-onboarding) |
| Advanced Opt-Out sends `OptOutType`; Twilio has already replied for matched events. | Consume STOP/START/HELP signals; never issue duplicate automatic confirmations. | [Advanced Opt-Out](https://www.twilio.com/docs/messaging/tutorials/advanced-opt-out) |
| Throughput depends on registration and sender type; extra US long codes are not a campaign capacity workaround. | Reviewed authorized sender pool; internal rate limits. | [Scaling with Messaging Services](https://www.twilio.com/docs/messaging/guides/best-practices-at-scale) |
| Verify incoming requests against the exact public URL, parameters, and Auth Token. | Signed form-encoded inbound and status endpoints; reject invalid signatures. | [Webhook security](https://www.twilio.com/docs/usage/security) |

The originally requested [campaign onboarding help article](https://help.twilio.com/articles/11847054539547) returned no substantive text in retrieval. This implementation relies on the current official onboarding guides linked above instead of inventing the article's contents.

## API behavior informing implementation

| Topic | Relevant behavior | Source |
| --- | --- | --- |
| API keys | Preferred REST authentication; Restricted keys allow scoped permissions. Standard keys do not grant Accounts or Keys resource access. Account-status verification may use the separately encrypted Auth Token. | [API keys overview](https://www.twilio.com/docs/iam/api-keys) |
| Registration inspection | Existing USA2P resources expose campaign status, ID, message flow, and carrier rate-limit information. Such data does not prove the operator's evidence or templates match registration. | [UsAppToPerson resource](https://www.twilio.com/docs/messaging/api/usapptoperson-resource) |
| Sender consistency | Sticky Sender is a service capability requiring Console configuration; provider mappings can change when a sender becomes ineligible. | [Messaging Services](https://www.twilio.com/docs/messaging/services) |
| Queue expiry | `ValidityPeriod` bounds Twilio queue residence; it cannot guarantee handset delivery within the application window. `MaxPrice` is obsolete and is not a spending control. | [Messages resource](https://www.twilio.com/docs/messaging/api/message-resource) |
| Filtering | Error 30007 reports Twilio or carrier filtering; review evidence and collect examples for support instead of changing numbers to evade it. | [Error 30007](https://www.twilio.com/docs/api/errors/30007) |
| Recipient opt-out | Error 21610 means recipient opt-out for a sender/service; apply global local suppression too. | [Error 21610](https://www.twilio.com/docs/api/errors/21610) |

## Conservative product defaults

These defaults are internal operating choices, **not universal legal limits or carrier-approved safe values**. An administrator must review them against the actual consent, approved campaign, jurisdictions, provider limits, and existing account traffic. Account traffic originating outside Blastio is not counted by these local caps.

| Safeguard | Default | Rationale and adjustment |
| --- | --- | --- |
| Recipient-local sending window | 09:00 inclusive to 18:00 exclusive | Avoid broad evening sending. Requires a verified IANA timezone and its source; an address or area code alone is insufficient. |
| Recipient frequency | 1 outbound in a rolling 24 hours, 3 in a rolling 7 days | Limits repeated contact across campaigns. Both automated and manual messages pass the gate. |
| Application rate | 1 outbound/minute | Small initial operating rate; keep below actual segment-based provider allowances. |
| Application daily volume | 100 outbound per UTC day | Limits initial exposure; not a carrier quota. |
| Application daily estimated spend | $10 per UTC day | Uses configurable segment estimates, replaced by known actual costs where available; provider fees and actual billed costs may differ. |
| Campaign caps | 100 outbound per UTC day; $10 total campaign cost | Campaign approval is not authority to exceed application-wide limits. |
| Message length | 3 SMS segments | Segmentation is calculated after actual substitution, including GSM extended characters and UTF-16 units. |
| Local queue age | 1 hour from scheduled dispatch | Stale messages expire rather than being sent on a later convenient day. |
| Provider queue validity | At most 300 seconds, shortened by expiry/window end | Limits provider queue residence; carrier delivery time remains outside local control. |
| Incident triggers | 5% filtered or opt-out/complaint rate after at least 20 qualifying message outcomes in rolling 24 hours | Campaign-level internal review triggers; a smaller sample or lower percentage can still warrant an immediate manual pause. |
| Operational health | Fresh verified inbound/callback status; reviewable incident thresholds | Fail closed on unavailable essentials or breached configured thresholds. |
| Content | Approved immutable version; identified sender; STOP instruction | Reviewed variants improve relevance; selection never generates new text while dispatching. |
| Renewed consent | Evidence newer than suppression, reviewed by an authorized user | A toggle, import boolean, positive reply, or START alone does not restore eligibility. |

A direct response to a new inbound inquiry can be permitted by Twilio's policy without ongoing campaign consent. This release conservatively applies its evidence gate to manual replies as well; operators must not bypass it.

No unsolicited text is sent to acquire permission. Obtain appropriate evidence through a legitimate consent process before creating outbound eligibility. Natural-language requests are handled deterministically without relying on AI availability; ambiguous negative requests hold further automated contact for review.
