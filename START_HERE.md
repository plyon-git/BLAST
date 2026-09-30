# Start here: 101XVC Blastio

This guide takes you from the ZIP to a working local application on Windows, then through the operator workflow. Start with the synthetic demo. The default environment is **SIMULATION**, so campaign activity uses a simulated provider and sends no carrier SMS.

`101XVC_Blastio_Full_Source.zip` contains the complete source, interface, database migration, sample CSV, tests, and documentation. It is a source distribution, not a standalone `.exe`. You install Python and download dependencies the first time. After installation, the local simulation runs on your computer. Live Twilio verification and sending need network access and the production setup described below.

## 1. Extract the ZIP

1. Create `C:\101XVC` in File Explorer.
2. Right-click `101XVC_Blastio_Full_Source.zip` and choose **Extract All**.
3. Set the extraction destination to `C:\101XVC`.
4. Open `C:\101XVC\Blastio`. You should see `requirements.txt`, `START_HERE.md`, and the `blastio` folder directly inside it.

If File Explorer created an extra nested directory, move the extracted `Blastio` folder so the path above is correct. If you choose another location, replace `C:\101XVC\Blastio` in every command with your actual code folder. Run the extracted copy, not files viewed inside the ZIP.

## 2. Install Python 3.12

Open **Windows PowerShell** from the Start menu. A normal user window is sufficient for the application commands.

Check whether Python 3.12 is already installed:

```powershell
py -3.12 --version
```

If this prints `Python 3.12.x`, continue to step 3. Otherwise:

1. Open [Python's official downloads](https://www.python.org/downloads/).
2. Install the **Python install manager** for Windows.
3. Close and reopen PowerShell, then run:

```powershell
py install 3.12
py -3.12 --version
```

If `py install 3.12` reports that it cannot open a file named `install`, an older Python launcher is taking that command. Use the installed manager directly:

```powershell
pymanager install 3.12
py -3.12 --version
```

The official [Windows Python guide](https://docs.python.org/3/using/windows.html) describes the install manager and these commands. Use a normal Python runtime, rather than the embeddable ZIP package.

## 3. Perform first-time setup

In PowerShell, run these commands in order:

```powershell
Set-Location "C:\101XVC\Blastio"
py -3.12 -m venv .venv
.\.venv\Scripts\python.exe -m pip install -r requirements.txt
.\.venv\Scripts\python.exe -m blastio.cli init --email "parrish@101xvc.local"
```

`parrish@101xvc.local` is the local login identifier used throughout this guide. You may replace it in the command with your own email, then sign in using that exact value. Initialization does not send an email or require email verification.

The command prompts twice for a new password. Choose at least **12 characters**. Password characters may not appear while typing. Enter the same password at both prompts and keep it securely.

Seed the practice records once:

```powershell
.\.venv\Scripts\python.exe -m blastio.cli demo
```

This creates five synthetic contacts, sample properties, two template versions, a draft campaign, and an interested inbox reply. Some records deliberately lack consent or have an opt-out, so you can inspect blocking behavior.

You do not need to activate the virtual environment. Every command in this guide invokes its Python executable directly, which also avoids PowerShell activation-script policy issues. Local demo setup needs no `.env`, encryption key, or Twilio credentials. The application reads process environment variables and **does not automatically load `.env`**.

## 4. Start the application in the first window

Use this PowerShell window for the web application:

```powershell
Set-Location "C:\101XVC\Blastio"
.\.venv\Scripts\python.exe -m uvicorn blastio.app:app --host 127.0.0.1 --port 8000
```

Wait for the startup message. Leave this window open. It will stay busy while the server runs, which is normal.

## 5. Start the worker in a second window

Open another PowerShell window and run:

```powershell
Set-Location "C:\101XVC\Blastio"
.\.venv\Scripts\python.exe -m blastio.worker
```

Leave this window open too. The worker processes scheduled queue items independently of the browser. Run one worker and one application server.

Now open [http://127.0.0.1:8000](http://127.0.0.1:8000) in your browser. Use that exact address, including `127.0.0.1`, for the default configuration.

Enter `parrish@101xvc.local` or the email you chose during initialization, enter your password, and click **Sign in**. Confirm that the top bar says **SIMULATION**.

## 6. Find your way around

| Sidebar view | What you do there |
| --- | --- |
| **Overview** | Inspect outcomes, queued work, eligible contacts, estimated costs, and next steps. **Diagnostics** opens the message ledger; **New campaign** opens Campaigns. |
| **Contacts & properties** | Search recipients and click **Review** to inspect properties, evidence, suppression, and holds. |
| **Import contacts** | Upload CSV, map fields, preview validation, and explicitly commit accepted rows. |
| **Messages & variants** | Write versions, render exact previews, and approve reviewed content. |
| **Campaigns** | Select approved versions and recipients, then validate, approve, schedule, pause, or resume. |
| **Conversation inbox** | Read replies, assign an owner/lead status, and submit reviewed manual replies through the policy gate. |
| **Twilio & safeguards** | Connect encrypted credentials, inspect verification/health, review limits, and manage global resume. |
| **Diagnostics & audit** | Inspect delivery records, audit history, performance, simulation tools, and evidence exports. |

The top-right **Pause all sending** button stops further dispatch throughout the business. A paused campaign, a recipient hold, and global pause are separate controls.

## 7. Run your first simulated campaign

Use **Jordan Reed, +12025550101**, for this first practice campaign. Jordan has synthetic reviewed evidence. The other demo contacts include intentional hold and suppression examples.

### Write and preview

1. Open **Messages & variants**.
2. In **Template name**, enter `Practice seller inquiry`.
3. In **Message**, enter:

   ```text
   Hi {{first_name}}, this is {{business_name}}. Would you like to discuss {{street_address}}? Reply STOP to unsubscribe
   ```

4. Click **Save new version**.
5. In **Exact recipient preview**, select the new **Saved template version**, Jordan as **Recipient**, and Jordan's **Property context**.
6. Click **Render exact preview**. Inspect the exact text, encoding, segments, estimated cost, and any eligibility reasons.
7. Find the new draft in **Version library**. Click **Review & approve**, enter a substantive **Review note**, and click **Approve immutable version**.

Saved versions are immutable. To revise one, use **Use as new draft**, edit the composer, and **Save new version**. Approve that new version separately. Optional reviewed A/B variants are selected deliberately in the campaign; sending does not generate new wording.

### Build, approve, and schedule

1. Open **Campaigns**, then click **New campaign**.
2. Enter a **Campaign name**, such as `First simulation`.
3. Select `Practice seller inquiry` under **Primary approved template**.
4. In simulation, retain the synthetic **Messaging Service SID** and **Sender routing** values.
5. Select only Jordan in **Recipient audience**. Confirm the property dropdown is correct. Leave optional variants unselected for this first run.
6. Click **Create draft campaign**.
7. On its row, click **Validate**. Read the result. Resolve reasons before approval.
8. Click **Approve**, enter an **Approval review note**, and click **Approve campaign**.
9. Click **Schedule**. Choose a start time in **Dispatch start in your browser timezone**, then click **Schedule campaign**.
10. With the worker running, inspect **Overview** and **Diagnostics & audit** for the resulting queue and outcome. Reopen a view or refresh the browser to see current records.

The demo recipients use `America/Denver`. The default sending window is 09:00 to 18:00 in the recipient's verified local timezone. If it is outside that window, schedule during the next valid window. The scheduling picker uses your browser timezone and converts the selected instant; the recipient window is checked separately.

Simulation still enforces consent, suppression, approved content, local hours, frequency, rate, budget, expiry, and pauses. By default, one outbound message per recipient in 24 hours and one dispatch per minute can defer repeated practice attempts. Queue expiry stays fixed. A simulated acceptance is a synthetic outcome, not proof of handset delivery.

For a manual single dispatch, stop the worker with **Ctrl+C** in its window, then open **Diagnostics & audit → Simulation lab** and click **Run one simulated dispatch**. Start the worker again when finished. Keeping the worker stopped lets you observe a queued item before dispatch.

## 8. Import a CSV and review evidence separately

Start with `examples\import_contacts.csv` to practice importing synthetic data. For operational imports, use your actual contact/property data and genuine consent evidence.

1. Open **Import contacts** or click **Import CSV** from Contacts.
2. Choose the CSV file.
3. Set **Country for national-format phone numbers** explicitly when numbers omit their country code. For fully specified `+` numbers, leave **Require internationally formatted numbers** selected.
4. Click **Preview file →**.
5. Inspect **Column mapping**. Change suggestions as needed and click **Revalidate mapping**.
6. Inspect **Validated preview** and row reasons. Use **Download rejected rows** if shown. A fatal malformed file must be corrected and previewed again.
7. Click **Review import →**. Check accepted, rejected, and duplicate counts.
8. Click **Commit [number] rows** to save. No record is saved merely by previewing.
9. Click **Review contacts**.

Imports organize phone identities and separate properties. Multiple properties do not create multiple outreach recipients. The importer never treats a CSV consent claim or a supplied timezone as verified proof.

### Review the recipient's timezone

1. In **Contacts & properties**, click **Review** on the recipient.
2. Click **Record recipient timezone**.
3. Enter the actual **IANA timezone**, such as `America/Denver`, and its documented **Evidence source**.
4. Click **Save evidence**.

A property address or area code alone is insufficient. For real recipients, use genuine recipient-location evidence. For synthetic practice, record the training context clearly and keep all consent evidence references under `simulation://`.

### Record and verify consent

1. In the recipient review, click **Add consent evidence**.
2. Enter **Business identity**, **Channel**, **Purpose**, **Source**, **Permission timestamp**, **Disclosure version**, and **Evidence reference**. The relevant defaults are `101XVC`, `sms`, and `seller_outreach`.
3. Click **Record evidence**. Its status is pending.
4. Reopen the contact review to inspect the saved evidence.
5. Read the original evidence at its referenced location. When it establishes the required permission, click **Review & verify this evidence**.
6. Enter an **Evidence review note**, then click **Verify evidence**.

For real records, do not invent permission or certify a phone list as consent. Retain the original disclosure and proof. Purchased, scraped, skip-traced, or uploaded phone data alone does not authorize messaging. An unsolicited permission-request text is not part of this workflow.

An opted-out recipient remains suppressed after reimport, recreation, or campaign cloning. Restoration requires a new, reviewed consent record dated after suppression. **Review automation hold** only releases an ordinary reply hold for future campaigns; it cannot clear suppression or revive canceled messages.

## 9. Practice replies, STOP, and manual responses

1. Open **Diagnostics & audit → Simulation lab**.
2. Under **Inbound simulation**, enter a seeded synthetic recipient phone and a practice **Incoming message**, such as `Yes, I am interested`.
3. Click **Process simulated inbound**.
4. Open **Conversation inbox** and select that thread.
5. Set **Conversation owner** and **Lead status**, then click **Save assignment**.

Replies hold further automated outreach for inbox review. Qualified interest, negative replies, complaints, wrong numbers, and opt-outs are tracked separately.

For a manual response, enter final text in **Manual reply**, including `101XVC` and `Reply STOP to unsubscribe`, and click **Queue simulated reply**. In production the button says **Queue reviewed reply**. Manual replies still require verified consent and pass suppression, local-window, frequency, sender, and health checks. A recent practice send may make a same-day response defer or expire under the default frequency cap.

To test opt-out, process another synthetic inbound containing `STOP`. Review the contact's suppression/history and pending-message cancellation. Further manual or campaign outreach should be blocked. Do not count STOP as an interested lead. In live mode, Twilio may send its own configured keyword confirmation; Blastio does not add a duplicate automatic confirmation.

For an ordinary reply hold that can legitimately be released, go to **Contacts & properties → Review → Review automation hold**. Document the **Review and next-step evidence** and click **Resume future automation**. Verified consent, absence of suppression, and confirmed timezone remain prerequisites. Canceled messages stay canceled.

## 10. Pause, resume, and inspect problems

### Global pause

1. Click **Pause all sending** in the top bar.
2. Enter a **Pause reason** and click **Pause operations**.
3. Investigate the cause and correct it.
4. As an administrator, open **Twilio & safeguards** and click **Review & resume operations**.
5. Record **Cause, resolution, and verification performed**, then click **Resume after review**.

Global resume does not clear campaign pauses or recipient suppression. Already accepted provider messages may still arrive after a pause.

### Campaign pause

On a campaign row, click **Pause**, record the reason, and click **Pause campaign**. After reviewing and resolving the cause, use **Resume**, fill **Cause, resolution, and review**, and click **Resume reviewed campaign**. Resume preserves the original expiry and reconsideration is limited to eligible queued or unattempted work. Unknown, failed, accepted, canceled, or expired attempts are not resent.

### Diagnostics

Open **Diagnostics & audit**:

- **Delivery diagnostics → Inspect:** exact persisted message, routing, outcomes, reasons, and attempts.
- **Audit history:** actor-attributed decisions and changes.
- **Performance:** metrics by campaign, template version, and sender, with adverse replies separate from interest.
- **Simulation lab:** synthetic dispatch/inbound tools available in simulation.
- **Export evidence:** downloadable operational records. Treat the export as sensitive; it does not contain the original external consent documents.

**Unknown** means provider acceptance is ambiguous. Do not clone or recreate a campaign to force another send. In a production unknown record, use **Inspect → Reconcile with provider record**, supply an evidenced **Provider Message SID** if needed, record the lookup/review note, and click **Reconcile read-only**. The app checks available matching facts and never resends through reconciliation. If no reliable provider record is known, keep the attempt held.

## 11. Stop and restart without losing records

To stop, press **Ctrl+C** in the worker window, then **Ctrl+C** in the application-server window. Closing the browser alone does not stop either process.

Your contacts, queue, suppression, users, and audit history persist in:

```text
C:\101XVC\Blastio\data\blastio.sqlite3
```

To restart, open two PowerShell windows in the same code folder and run the server/worker commands from steps 4 and 5. Reuse any configured environment and the same encryption key.

Do not run `init` or `demo` again for an existing database. `init` is first-time administrator setup; `demo` requires an empty contacts database. Do not delete the database to bypass a hold. Follow [backup/restore instructions](docs/DEPLOYMENT.md#back-up-and-restore) before replacing or moving operational data.

Additional staff accounts can be created locally while preserving existing data:

```powershell
Set-Location "C:\101XVC\Blastio"
.\.venv\Scripts\python.exe -m blastio.cli create-user --email "reviewer@101xvc.local" --role reviewer
```

The command prompts for that user's password. Available roles are `admin`, `reviewer`, `operator`, and `viewer`; their permissions are described in [deployment instructions](docs/DEPLOYMENT.md).

## 12. Connect Twilio deliberately

You can inspect or store a real Twilio connection while the server remains in simulation, but simulation campaign dispatch uses the simulated provider. Saving and read-only verification do not send SMS or alter your registration.

### Keep a persistent encryption key

Stop both processes before changing their environment. Provider credentials need a persistent Fernet key. The following PowerShell commands create it once in your local user profile, then reuse it. Run from the code folder after installing requirements:

```powershell
Set-Location "C:\101XVC\Blastio"
$blastioKeyPath = Join-Path $env:LOCALAPPDATA '101XVC\Blastio\fernet.key'
New-Item -ItemType Directory -Force (Split-Path $blastioKeyPath) | Out-Null
if (-not (Test-Path $blastioKeyPath)) {
    .\.venv\Scripts\python.exe -c 'from cryptography.fernet import Fernet; print(Fernet.generate_key().decode())' | Set-Content -Encoding ascii $blastioKeyPath
}
$env:BLASTIO_ENCRYPTION_KEY = (Get-Content $blastioKeyPath -Raw).Trim()
```

Protect and securely back up this key file separately from the database. Do not upload it, include it in a shared ZIP, or commit it. Losing or replacing the key makes saved credentials unreadable.

In the second PowerShell window, load the **same** key before starting the worker:

```powershell
$blastioKeyPath = Join-Path $env:LOCALAPPDATA '101XVC\Blastio\fernet.key'
$env:BLASTIO_ENCRYPTION_KEY = (Get-Content $blastioKeyPath -Raw).Trim()
```

Start the server and worker again. On future starts, load the existing key before launching each process; do not generate a replacement. The server and worker must share database path, mode, business identity, public URL, and key. Creating a `.env` file alone does not export those values.

### Store and inspect the connection

1. Open **Twilio & safeguards → Connect credentials**.
2. Enter **Account SID**, your appropriately scoped **API Key SID** and **API Key secret** where supported, **Account Auth Token for webhook validation**, and the **Existing registered Messaging Service SID**.
3. Click **Store encrypted credentials**.
4. Click **Verify read-only connection**. Read provider-observed facts, timestamps, and manual review requirements.
5. Use **Replace** for replacement or **Revoke** to remove Blastio's saved connection. Actual API-key revocation is performed separately in Twilio Console.

For a loopback-only local instance, public webhook verification will remain incomplete. That is expected. Do not mark Console readiness items reviewed to hide a missing requirement.

### Prepare live production

Read [DEPLOYMENT.md](docs/DEPLOYMENT.md), [POLICY.md](docs/POLICY.md), [SMOKE_TEST.md](docs/SMOKE_TEST.md), and [LIMITATIONS.md](docs/LIMITATIONS.md) before enabling live sending.

Production requires a stable public HTTPS origin, explicit Console review of existing registration/service/senders and effective inbound routing, signed inbound/status health, genuine recipient consent/timezone evidence, reviewed templates/campaigns, and deliberate application limits. Both processes must receive the same deployment environment, including:

```text
BLASTIO_MODE=production
BLASTIO_ALLOW_PRODUCTION=true
BLASTIO_PUBLIC_URL=https://your-actual-blastio-host
BLASTIO_COOKIE_SECURE=true
BLASTIO_DB=the-same-persistent-database-path
BLASTIO_ENCRYPTION_KEY=the-same-persistent-key
```

These are deployment values to configure, not a copy-paste command to enable the local demo. Use a separate production database and keep synthetic evidence out of production eligibility.

In **Twilio & safeguards**, **Probe webhook paths** performs signed HTTPS probes without sending SMS. Under **Sending safeguards & readiness**, review the Console/readiness checklist, enter a **Safeguard review note**, and use **Save reviewed safeguards**. The initial smoke-test review attests to the authorized test plan; record the actual results afterward and recheck readiness before real campaigns.

The first live check must use explicitly authorized test recipients and a reviewed one-message plan. A2P registration does not authorize arbitrary imported lists, and software cannot guarantee freedom from filtering or suspension. Use the full [controlled smoke-test procedure](docs/SMOKE_TEST.md); no real seller campaign is part of installation testing.

## Common setup problems

| What you see | What to check |
| --- | --- |
| `py` is not recognized | Install the official Python install manager, then reopen PowerShell. |
| Cannot find `.venv\Scripts\python.exe` | Use the code directory containing `requirements.txt`; create `.venv` in step 3. |
| `No module named blastio` | `Set-Location` to `C:\101XVC\Blastio`, not its parent or ZIP viewer. |
| `No module named uvicorn` | Run `.\.venv\Scripts\python.exe -m pip install -r requirements.txt` and inspect any installation failure. |
| Dependency download fails | First installation needs internet/package access. Retry after resolving that access; do not assume a failed install completed. |
| Port 8000 already in use | Stop another running copy of Blastio if it is yours. Keep one server; consult deployment configuration before changing the port. |
| Browser cannot connect | Keep the server window open, check startup errors, and use `http://127.0.0.1:8000`. |
| Login origin mismatch | Use the exact configured public origin. For defaults, use `127.0.0.1`, rather than a different hostname. |
| `Already initialized` / demo needs empty contacts | Existing setup is intact. Start the app and sign in; do not rerun first-time commands. |
| Nothing dispatches | Check worker, schedule, recipient local hours, consent/timezone, approved versions, pauses, caps, and expiry in Diagnostics. |
| Credential encryption error | Export the same saved Fernet key before starting both processes; do not replace the key to silence the error. |
| Repeated sign-in failure / wait message | Verify the chosen email/password; the login throttle requires waiting before trying again. |

## macOS and Linux appendix

Install Python 3.12, extract the source, and open a terminal in the folder containing `requirements.txt`. If your executable is named `python3` rather than `python3.12`, substitute it when creating the environment.

First-time setup:

```bash
python3.12 -m venv .venv
.venv/bin/python -m pip install -r requirements.txt
.venv/bin/python -m blastio.cli init --email parrish@101xvc.local
.venv/bin/python -m blastio.cli demo
.venv/bin/python -m uvicorn blastio.app:app --host 127.0.0.1 --port 8000
```

In a second terminal, change to the same folder and run:

```bash
.venv/bin/python -m blastio.worker
```

Open [http://127.0.0.1:8000](http://127.0.0.1:8000). Stop each process with Ctrl+C and restart using its final command, preserving the database and exported configuration. The operator workflow is the same. Persistent secrets, HTTPS hosting, backups, and Linux service examples are in [DEPLOYMENT.md](docs/DEPLOYMENT.md).
