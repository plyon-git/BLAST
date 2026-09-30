# Deployment and existing-account setup

## Supported shape

One host, one API process, one separate worker, and one SQLite database on a persistent local filesystem. Both processes must use the same database path, persistent encryption key, mode, business identity, and public URL. WAL and transactional claims support this shape; they do not make network-mounted SQLite or multi-host replicas safe.

Python 3.11+ is required. Serve HTTPS through a reverse proxy. Bind Uvicorn to loopback; expose only the HTTPS proxy. Protect database files, evidence exports, and host backups because they contain personal data. Encryption in this application covers provider secrets, not the whole database.

## Local environment

```bash
python3 -m venv .venv
source .venv/bin/activate
python -m pip install -r requirements.txt
cp .env.example .env
```

Edit `.env` before use. Generate a persistent Fernet key:

```bash
python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
```

Put the resulting value in `BLASTIO_ENCRYPTION_KEY` using your local editor or secret manager. Never commit `.env` or copy a populated environment into an issue. This application reads the process environment; it does not automatically load `.env`. In a POSIX shell, export its entries before starting either process:

```bash
set -a
source .env
set +a
python -m blastio.cli init --email admin@example.com
python -m blastio.cli demo
uvicorn blastio.app:app --host 127.0.0.1 --port 8000
```

Use the prompted password to sign in. Open a second terminal, activate the virtual environment, export the same `.env`, and run `python -m blastio.worker`. The demo belongs only in a simulation database; its synthetic consent evidence cannot authorize production.

On Windows, activate `.venv\Scripts\Activate.ps1`. If local execution policy blocks this trusted activation script, run the virtual environment executables directly, for example `.\.venv\Scripts\python.exe -m blastio.worker`, instead of changing machine-wide policy. POSIX `source` syntax does not apply in PowerShell. Set the equivalent process variables explicitly:

```powershell
$env:BLASTIO_DB = 'data/blastio.sqlite3'
$env:BLASTIO_MODE = 'simulation'
$env:BLASTIO_ALLOW_PRODUCTION = 'false'
$env:BLASTIO_PUBLIC_URL = 'http://127.0.0.1:8000'
$env:BLASTIO_COOKIE_SECURE = 'false'
$env:BLASTIO_BUSINESS_NAME = '101XVC'
$env:BLASTIO_SEGMENT_COST = '0.015'
```

Set `BLASTIO_ENCRYPTION_KEY` from the persistent key you stored securely. For first creation, capture a generated key into the environment without printing it:

```powershell
$env:BLASTIO_ENCRYPTION_KEY = python -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())'
```

Save that first key securely and reuse it in both terminals and future starts. Generating a different key on each launch makes saved credentials unreadable. Credentials are entered through the administrator settings page, not hard-coded into these commands. Windows needs an IANA timezone database; the requirements include `tzdata` for `zoneinfo`.

Additional users are created locally with a password prompt:

```bash
python -m blastio.cli create-user --email reviewer@example.com --role reviewer
```

Available roles are `admin`, `reviewer`, `operator`, and `viewer`. Viewers read records. Operators can draft/import, classify inbox items, and pause/suppress. Reviewers can also approve templates/campaigns, verify consent/timezones, schedule, submit reviewed manual replies, and export evidence. Administrators control provider credentials, policy/readiness settings, and global resume. Server-side checks apply even if a browser displays an unavailable action.

| Variable | Local default / role |
| --- | --- |
| `BLASTIO_DB` | `data/blastio.sqlite3`; use an absolute persistent path in deployment. |
| `BLASTIO_MODE` | `simulation`; production requires the deployment switch and campaign mode too. |
| `BLASTIO_ALLOW_PRODUCTION` | `false`; set `true` only for a reviewed production deployment. |
| `BLASTIO_PUBLIC_URL` | `http://127.0.0.1:8000`; production must use the exact external HTTPS origin without a trailing slash. |
| `BLASTIO_COOKIE_SECURE` | `false` for loopback HTTP; set `true` when served over HTTPS. |
| `BLASTIO_BUSINESS_NAME` | `101XVC`; must match the business identity in consent and approved messages. |
| `BLASTIO_ENCRYPTION_KEY` | Empty in the template; persistent Fernet key for encrypted provider secrets. Back it up separately. |
| `BLASTIO_SEGMENT_COST` | `0.015`; a planning assumption in USD per segment, not a quoted Twilio rate. Review fees and use your own conservative estimate. |

Internal provider verification freshness is 24 hours; signed webhook health freshness is 15 minutes. The production worker refreshes signed synthetic reachability probes periodically. These intervals are internal fail-closed rules, not Twilio service-level guarantees.

## Linux service installation

The example service units in `ops/` assume code at `/opt/blastio`, an unprivileged `blastio` user, a virtual environment at `/opt/blastio/.venv`, an environment file at `/etc/blastio/blastio.env`, and persistent data at `/var/lib/blastio`.

1. Create the user and directories with ownership appropriate to your host. Keep code read-only to the service user if operationally possible; only `/var/lib/blastio` needs application writes.
2. Install requirements into `/opt/blastio/.venv`. Copy reviewed code and the unit files to the host.
3. Create `/etc/blastio/blastio.env` from `.env.example`, with restrictive permissions. Use `BLASTIO_DB=/var/lib/blastio/blastio.sqlite3`, the persistent key, exact external HTTPS URL, and `BLASTIO_COOKIE_SECURE=true`. Start in simulation.
4. Initialize the database and administrator with that environment using a trusted maintenance shell. Do not place the password in command arguments or a systemd unit.
5. Install `ops/blastio-api.service` and `ops/blastio-worker.service` in `/etc/systemd/system/`, reload systemd, and start the services. There is intentionally one API and one worker.
6. Configure your HTTPS proxy to forward requests to `127.0.0.1:8000`. Preserve paths, queries, and form bodies; do not rewrite webhook paths. Restrict operator access with your normal network controls while allowing Twilio to reach the signed webhook paths.

The units are deployment examples. Verify host ownership, secret readability, proxy behavior, firewall, TLS renewal, service restart behavior, and backup retention on your actual host. Do not assume installation on your operating system was tested.

## Existing Twilio account: explicit manual checklist

Connecting Blastio performs read-only verification. It does not register a brand/campaign, edit sender pools, enable Console features, or send a text. An administrator must intentionally review any required Console changes so existing operations are protected.

1. In **Twilio Console → Messaging → Services**, open the existing registered Messaging Service. Record its `MG...` SID, associated A2P campaign, approved use case/message flow, and eligible SMS-capable senders.
2. Confirm that intended seller messages and actual consent acquisition match that approved campaign. Keep the existing registration intact. Stop and resolve uncertainty with the account's messaging administrator rather than creating another registration.
3. Review the **Sender Pool** and **Sender Selection Settings**. Use only approved eligible senders; enable/review Sticky Sender if your conversation routing relies on it. Never add numbers to bypass limits or opt-outs.
4. Review **Opt-Out Management / Advanced Opt-Out**. It requires Console configuration and is not enabled by this app. Preserve supported STOP/START/HELP behavior and review any localized overrides/confirmation text. Record `advanced_optout_reviewed` only after inspecting the actual service.
5. Review **Integration / incoming message handling**. Configure the exact form-encoded HTTP POST URL:

   ```text
   https://your-blastio-host.example/webhooks/twilio/inbound
   ```

   This may replace the service's existing inbound destination. Coordinate intentionally with the operator of that destination. If the account needs an existing relay or another application too, implement and review the routing before switching it; this release does not silently multiplex other systems.
6. Review service **status callback** configuration. Verification reports the existing default as a fact, but does not require replacing it. Blastio's own outbound API requests include an individual callback URL for the local message:

   ```text
   https://your-blastio-host.example/webhooks/twilio/status?message_id=<local-id>
   ```

   The per-message callback overrides the service default for Blastio's own sends. Keep the existing default callback for other tools unless the account operator separately plans a change. Existing messages created by other tools remain outside Blastio's queue. This app does not edit the service default or claim visibility into all account traffic. Unmatched callback records require operator investigation.
7. Review provider queue validity settings. The adapter sends a bounded `ValidityPeriod` for its own message requests. Keep the application window and queued-message expiry under review; delayed carrier delivery remains possible.
8. In Console IAM/API Keys, create appropriately scoped REST credentials. Blastio needs reads of its Messaging Service, service phone numbers, USA2P campaign, and message resources, plus message creation after explicit launch. Standard API keys lack Accounts-resource access; account-status reads may use the separately stored account Auth Token, which is also required for signatures. Do not grant key/registration mutation permissions unnecessarily.
9. In **Blastio → Twilio settings**, save the Account SID, API Key SID/secret, account Auth Token, and selected service SID. Secrets are encrypted in the database and masked in saved responses. Review timestamps and resource facts from verification.
10. Complete the explicit manual readiness flags: `advanced_optout_reviewed`, `sender_associations_reviewed`, `campaign_content_reviewed`, and `smoke_test_reviewed`. They are operator attestations, not API-proven facts. For the first controlled send, `smoke_test_reviewed` means an authorized operator has reviewed the single-recipient smoke-test plan, recipient consent, and exact proposed message. It does not assert that live testing has already passed. After following [SMOKE_TEST.md](SMOKE_TEST.md), record the actual results and recheck readiness before any seller campaign.
11. Run signed inbound/status probes through the settings page. The app calls `/webhooks/twilio/probe/inbound` and `/webhooks/twilio/probe/status` with synthetic signed forms. No contact, conversation, or Twilio SMS is created by these probes. They verify public reachability/signature handling and have timestamps; they do not prove Twilio-origin delivery.
12. Perform the controlled production smoke test with documented, explicitly authorized recipients. Enable production in environment and the campaign only after review, with global pause retained until the approved check is ready.

## Back up and restore

Pause and stop the worker for an operationally quiet backup. SQLite's backup API includes a coherent view of WAL-managed data; copying the live `.sqlite3` file alone can omit WAL changes. With `BLASTIO_DB` exported:

```bash
python - <<'PY'
import os
import sqlite3
from datetime import datetime, timezone
from pathlib import Path

source_path = Path(os.environ['BLASTIO_DB'])
backup_dir = source_path.parent / 'backups'
backup_dir.mkdir(mode=0o700, parents=True, exist_ok=True)
stamp = datetime.now(timezone.utc).strftime('%Y%m%dT%H%M%SZ')
target = backup_dir / f'blastio-{stamp}.sqlite3'
with sqlite3.connect(source_path) as source:
    with sqlite3.connect(target) as destination:
        source.backup(destination)
        result = destination.execute('PRAGMA integrity_check').fetchone()[0]
        if result != 'ok':
            raise RuntimeError(result)
os.chmod(target, 0o600)
print(target)
PY
```

Protect and transfer the backup according to your retention policy. Secure the Fernet key separately; losing it loses access to saved credentials. Restore into a new database path with mode `simulation` and production disabled, validate integrity and suppression/attempt history, then reenter or decrypt credentials only on a trusted host. An older snapshot must not replace newer suppression events without reconciliation. Never start a restored stale queue in production automatically.

## Release and recovery

Take a verified backup before an upgrade. Pause, stop the worker, install the reviewed release, run database initialization/migrations, and validate the new release against an isolated simulation database. Start one API and one worker. Inspect recovered unknown attempts and expired queued messages before explicit resume. Database or suppression-store failures require dispatch to remain stopped; restarting the process is not consent or incident clearance.
