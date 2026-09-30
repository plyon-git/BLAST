# Release verification

Verified on 2026-09-30 with Python 3.12.14. The environment defaults to simulation.

- Automated Python suite: 137 passed. Includes real SQLite and authenticated HTTP tests, simulated provider attempts, HTTP transport mocks, and deterministic quoted-JSON CSV parsing with CRLF and LF line endings.
- JavaScript syntax: `node --check blastio/static/app.js` passed.
- Python compilation and installed dependency consistency checks passed.
- Browser verification uses an isolated synthetic database. See the browser smoke results recorded below.

The suite exercises missing consent, unverified timezone/DST, suppression across senders/reimports/clones, STOP concurrent with dispatch, worker crash and ambiguous timeout, invalid signatures, duplicate/out-of-order callbacks, budgets and pauses, immutable template approval, credential encryption/access control, stale callback health, review/resume, verification races, and read-only reconciliation.

One dependency warning remains: Starlette's TestClient warns that its httpx adapter is deprecated. The tests complete successfully; the production adapter uses httpx directly.

No real Twilio credentials or live carrier messages were used. Production setup, recipient evidence review, and the authorized controlled smoke-test procedure remain operator responsibilities. Mocked and synthetic results do not establish real carrier delivery, account safety, or enforcement behavior.

## Operator browser verification

Passed in Chromium 153 using a freshly created temporary SQLite database, generated synthetic operator credentials, and simulation-only server configuration. The browser and server were shut down after the test. The fixture contains only synthetic recipients and consent records.

- Desktop at 1440 × 1000: login and all eight operator views loaded successfully.
- CSV wizard: upload, suggested/manual mappings, revalidation, normalized preview, rejected-row report, confirmation, and commit completed with one accepted and one rejected synthetic row.
- Template workflow: saved an immutable draft version, rendered an exact property-specific preview, displayed segment/encoding/cost metrics, and approved the reviewed version.
- Campaign workflow: selected a synthetic recipient and property context, validated the audience and message, approved the campaign, and scheduled it for the current recipient-local window.
- Dispatch: the simulation provider accepted the queued message and persisted its synthetic provider SID and final rendered body. No real provider request was made.
- STOP handling: a simulated STOP reply classified as `opt_out`, activated authoritative suppression, and appeared in recipient review.
- Mobile at 390 × 844: dashboard, navigation, and settings rendered without horizontal page overflow. Screenshots were visually inspected.
- No browser runtime errors or failed network requests occurred. The initial unauthenticated session check correctly returned 401 before login.

Desktop and mobile previews: `docs/preview.png` and `docs/preview-mobile.png`.

To repeat this optional browser check, run `npm install`, `npx playwright install chromium`, and `npm run test:ui` after installing the Python development dependencies. Node and npm are only needed for this optional browser check. The script creates and removes its own isolated simulation fixture and cannot target an existing instance. Optional environment settings are `BLASTIO_PYTHON`, `BLASTIO_BROWSER_PATH`, `BLASTIO_BROWSER_LIBRARY_PATH`, and `BLASTIO_PLAYWRIGHT_MODULE`. This check never connects a Twilio account or authorizes real outreach.
