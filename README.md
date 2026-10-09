# LabReach Auto

A fully local outreach agent that finds engineering professors and emails them on behalf of a high school student
(Summer 2027 research, mechanical / electrical / computer engineering).

Everything runs on your computer. No API keys, no cloud services, no extra cost. All language work goes through the
Claude Code CLI (`claude`) that is already logged in on your machine, so it uses your existing Claude usage.
**Dry-run is the default. Nothing is sent until you confirm live mode, read a 5-email pilot, and approve it.**

## What it does

1. **Discovers** professors by itself: official faculty directories, profile and lab pages, hard exclusions
   (quoted with URL), addresses taken only from official pages, recent work verified by re-fetching the page.
2. **Drafts** from three locked template variants (A mechanical/prosthetics/robotics, B electrical/embedded/sensors,
   C computer engineering/ML). The model writes only two sentences; code fills and checks everything else.
3. **Gates** every email (8 gates, below). Anything that fails goes to `needs-human` with reasons.
4. **Sends** from Gmail inside recipient-local windows, with caps and jitter; sends two follow-ups in the same thread.
5. **Watches** replies and bounces read-only, never auto-replies, saves Gmail **drafts** for you, and stops itself
   when anything looks wrong.

## One-time setup

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,tools]"
labreach init                 # creates ./labreach_data (gitignored)
labreach doctor               # confirms your installed `claude` supports every flag we use
pytest                        # 178 tests, all against mocks; no network, no Gmail
```

Private files that are never committed: `data/private/contact.yaml` (phone, date of birth) and the resume PDF.
Rebuild the one-page resume any time with `python tools/build_resume.py`.

### Gmail (app password, no Google Cloud project)

1. Turn on **2-Step Verification** for the sending Gmail account.
2. Google Account → Security → **App passwords** → create one named `LabReach`.
3. In Gmail settings → Forwarding and POP/IMAP → **enable IMAP**.
4. `labreach auth` (stores the password in your OS keychain; never in a file, never logged), then
   `labreach auth-check` (logs in only; sends and reads nothing).

You can revoke the app password at any time. If Google refuses app passwords for the account, tell Claude and ask
for the OAuth (Gmail API) fallback; it is intentionally not built.

## Rollout, in order

| Step | Command | What you check |
|---|---|---|
| 1. Facts | `labreach profile show` → `labreach profile approve` | Every fact is accurate. `needs_confirmation` facts stay out of emails. |
| 2. Templates | `labreach templates build` (reads `docs/template_preview.txt`), edit `config/templates/*.yaml`, then `labreach templates approve --yes` | Wording. Locks skeletons by hash; any later edit needs approval again. |
| 3. Smoke test | `LABREACH_MODE=live labreach smoke-test` | Sends 2 emails **to yourself**; confirms they land in Sent and thread. |
| 4. Discovery | `labreach discover --max 15` (repeat) → `labreach report --targets` | Keeps ≥30 eligible targets queued, then stops. |
| 5. Dry run | `labreach run --drafts 10` | Read `out/dry_run/*.txt`. Nothing is sent. |
| 6. Go live | `export LABREACH_MODE=live` → `labreach go-live` → `labreach run` | First 5 emails are written to `out/pilot/*.txt` and **not sent**. |
| 7. Approve | read `out/pilot/*.txt` → `labreach approve-pilot` | Turns on auto-sending: 5/day for two weeks, then up to 10/day. |
| 8. Schedule | see below | |

Discovery uses the Claude budget (`max_claude_calls_per_day`, default 60): roughly 1 call per university for directories,
1 per promising professor for sources, 1 per draft. Expect the first 30 eligible targets to take a couple of days.

## The 8 gates (all must pass; code decides, never the model)

1. **Template conformance**: skeleton hash matches the locked template; fixed slots recomputed and compared exactly.
2. **Lint**: 120-180 words, subject specificity, banned/flattery/exaggeration words, exactly one ask, no links in initial
   emails, no other people's names, ≤2 attachments / 2 MB, <40% n-gram overlap with recent emails.
3. **Claim verification**: titles, years, numbers and named terms in the text must appear verbatim in stored sources or
   approved facts; the text that is sent must equal the text that was verified.
4. **Address provenance**: address on an allowlisted domain, found literally in the stored official-page snippet.
5. **Eligibility**: not do-not-contact, not already contacted (email or name+university), no other active contact in the
   same lab, status allows sending; follow-ups also need no reply/bounce and the right timing.
6. **Limits**: daily cap (5 → 10, hard max 10), ≤3 per university and 1 per department per day.
7. **Send window**: Tue-Thu 8:00-10:00 am **recipient-local**, no US holidays, Thanksgiving week, or Dec 12 - Jan 5;
   per-university blackouts in `config/settings.yaml`.
8. **Kill switch** not engaged.

## Stopping, and what stops itself

- `labreach stop` or create `./labreach_data/STOP`: all sending halts at once. `labreach resume` clears it.
- Automatic stops (console message + a clearly labeled **draft** in the student's Gmail Drafts folder):
  bounce rate >15% of the last 20 sends; SMTP errors about limits or account problems; Gmail login failure or `claude`
  not logged in; 3 consecutive claim-verification failures from one source type; any complaint, stop request,
  compliance/minors-office reply, or reply that cannot be classified confidently.
- A polite "we don't take high school students" closes that lead, adds them to do-not-contact and saves a thank-you
  **draft**; it does not halt everything (set `replies.halt_on_hs_decline: true` if you want it to).
- Replies are never answered automatically. Positive/ambiguous replies stop that person's sequence and create a Gmail
  draft with three call times in their time zone for you to edit and send yourself.

## Keeping the computer awake and scheduling

The computer must be **awake and online** during send windows: 8-10 am in the *recipient's* time zone (Eastern
recipients are 5-7 am Pacific). If a window is missed, the email waits for the next valid window; nothing is ever sent
outside one. Run `labreach run` every 20 minutes.

**macOS** (keep awake, then schedule with launchd):

```bash
# keep the Mac awake for the next 10 hours, or schedule a wake at 4:50 am Tue-Thu:
caffeinate -i -t 36000 &
sudo pmset repeat wakeorpoweron TWR 04:50:00
```

`~/Library/LaunchAgents/com.labreach.run.plist` (edit the paths), then `launchctl load` it:

```xml
<?xml version="1.0" encoding="UTF-8"?>
<!DOCTYPE plist PUBLIC "-//Apple//DTD PLIST 1.0//EN" "http://www.apple.com/DTDs/PropertyList-1.0.dtd">
<plist version="1.0"><dict>
  <key>Label</key><string>com.labreach.run</string>
  <key>ProgramArguments</key><array>
    <string>/path/to/LabReach/.venv/bin/labreach</string><string>run</string></array>
  <key>WorkingDirectory</key><string>/path/to/LabReach</string>
  <key>EnvironmentVariables</key><dict><key>LABREACH_MODE</key><string>live</string></dict>
  <key>StartInterval</key><integer>1200</integer>
  <key>StandardOutPath</key><string>/path/to/LabReach/labreach_data/launchd.log</string>
  <key>StandardErrorPath</key><string>/path/to/LabReach/labreach_data/launchd.log</string>
</dict></plist>
```

**Linux/macOS cron**: `*/20 * * * * cd /path/to/LabReach && LABREACH_MODE=live .venv/bin/labreach run >> labreach_data/cron.log 2>&1`

**Windows**: Task Scheduler → repeat every 20 minutes running `.venv\Scripts\labreach.exe run` in the project folder
(set `LABREACH_MODE=live` as a system environment variable); Settings → Power → set sleep to "Never" while plugged in
(or use "Wake the computer to run this task").

A lock file (`labreach_data/run.lock`) guarantees two runs never overlap; a lock left by a dead process is recovered.

## Daily operations

```bash
labreach report --targets     # queue, statuses, Claude budget, kill switch
labreach needs-human          # everything the gates refused, with reasons (never sent, never silently fixed)
labreach status               # mode, approvals, kill switch
labreach budget               # Claude calls used today
labreach dnc add x@school.edu # never contact this address (or --domain school.edu)
```

Check the student's Gmail **Drafts** folder daily: reply drafts and alert drafts appear there.

## Where things live

- `./labreach_data/` (gitignored): SQLite database (`events` is the append-only audit trail), logs without email
  bodies, cached pages, `STOP`, `run.lock`.
- `config/settings.yaml`: caps, windows, blackouts, institutions, allowlists. `config/templates/`: templates + `LOCK.json`.
- `data/student_profile.yaml`: approved facts.

## Known limits

- Discovery heuristics (faculty directories, profile pages) were developed against fixtures, not live university sites;
  the first `labreach discover --max 5` run on your machine is the real test. Failures are conservative: pages that
  cannot be understood are skipped, and addresses are never guessed.
- A lab manager designated for joining/minors paperwork is routed to `needs-human`; no template exists for that yet.
- Gmail may rewrite a Message-ID. The smoke test checks this; if it did, replies would not be matched.
