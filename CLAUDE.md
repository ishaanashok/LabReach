# LabReach Auto: handoff for the next session

A parent and their son (Ishaan Ashok, 11th grade, Fremont CA) want a **fully local, autonomous outreach agent** that finds
engineering professors and emails them for Summer 2027 research. All language work goes through the local `claude` CLI
(`claude -p`); no API keys, no cloud services, no extra cost. Read `README.md` for operations; this file is state + rules.

## Rules that outrank everything
- **Never touch the live Gmail account, send mail, or run `smoke-test`/`go-live`/`approve-pilot` without the parent's
  explicit OK in this conversation.** Dry-run is the default and must stay the default.
- **The tool sends ONE initial email per person and never a follow-up.** The parent writes every follow-up. Replies only ever
  produce Gmail *drafts*. `labreach followups` is a read-only reminder list.
- Emails may cite only approved facts in `data/student_profile.yaml`, in their exact `email_phrase` wording. Never add
  anything not on the resume. Never guess an address; addresses come only from official pages, verified by code.
- **Initial emails contain no links** (the resume PDF carries them). No dates for summer availability ("full time over the
  summer"). Private details (citizenship, date of birth, phone, parents' driving) never go in an email body.
- Never use `pkill -f` with a pattern that matches your own command line (it killed the shell twice).
- Do not create a pull request unless asked.

## State (branch `claude/labreach-auto-setup-6annas`)
- Built and mock-tested (177 tests, `pytest`, ruff clean): DB + migrations, claude wrapper, profile approval, 3 locked-template
  variants (A mechanical/prosthetics, B electrical/embedded/sensors, C computer/ML), linter, claim verifier, discovery
  (fetch/parsers/extraction/fit score/verified sources), 8 gates, scheduler (windows/holidays/caps/jitter), SMTP/IMAP layer,
  replies/bounces/kill switch/alerts, run loop (dry-run, pilot, live), CLI, README.
- **Not yet done / not yet run on a real machine:** Gmail auth + `smoke-test`; discovery against real university sites
  (the cloud sandbox blocked them, so parsers were only tested on fixtures); real web-search research calls; template
  approval/lock (`labreach templates approve --yes` has NOT been run, `config/templates/LOCK.json` does not exist);
  `labreach profile approve` (stored in the local DB, so it must be run on the parent's machine).
- Real `claude -p` drafting was verified against a *fictional* source: 3/3 drafts passed every check
  (see `docs/sample_real_draft.txt`).

## Decisions made with the parent
- Student: Ishaan Ashok, junior, American High School (Fremont). Gmail `ishaan.ashok123@gmail.com`. US citizen (not for emails).
  Available all summer; ~10 hrs/week in school; unpaid is fine; parents can drive to Bay Area campuses; out of state: online
  or a week-to-a-month in person.
- Resume: one page, built by `python tools/build_resume.py` into `data/private/` (gitignored; phone and date of birth live in
  `data/private/contact.yaml`). GPA 3.9. UC Scout removed. ReStep wording: "giving to underserved communities in India,
  currently working with the state government there" (no Tamil Nadu). ReStep site: https://projectrestep.netlify.app/ (on the
  resume only). BioWrap: it happened and he presented (fact `F_BIOWRAP`, now confirmed, in pools A and C).
- Polite "we don't take HS students" replies close the lead + thank-you draft (no global halt); complaints, stop requests,
  compliance/minors offices and unclear replies always halt (`replies.halt_on_hs_decline` flips the first).
- Reply drafts offer 3 after-school call times (4 pm Pacific) in the recipient's time zone; confirm his real availability.

## Next steps (do in order, asking before anything live)
1. `python3 -m venv .venv && source .venv/bin/activate && pip install -e ".[dev,tools]" && pytest && labreach doctor`.
2. `labreach profile show` then `labreach profile approve` with the parent.
3. `labreach discover --max 5` on the real machine; fix parser/heuristic problems against real HTML (biggest risk). Report
   outcomes per candidate. Keep Claude calls under `max_claude_calls_per_day` (60).
4. Draft real emails (`labreach run --drafts 10`, dry-run) and refine the template wording with the parent (summer wording
   first). Only then `labreach templates approve --yes`.
5. Gmail: 2-Step Verification, app password, enable IMAP, `labreach auth`, `labreach auth-check`; then, with OK,
   `LABREACH_MODE=live labreach smoke-test` (two emails to himself; checks Sent, threading, and that Gmail kept our Message-ID).
6. Pilot of 5 (`out/pilot/`), parent reads them, `labreach approve-pilot`; then schedule (README has launchd/cron/Windows).

## Map
`labreach/`: `cli.py`, `run.py` (loop), `gates.py`, `scheduler.py`, `replies.py`, `bounces.py`, `killswitch.py`, `budget.py`,
`claude_cli.py`, `mail_smtp.py`, `mail_imap.py`, `notify.py`, `discovery/{fetch,parse_directory,parse_profile,email_extract,run}.py`,
`personalize/{sources,generate,verify_claims,samples}.py`, `compose/{templates,render,lint,mime}.py`.
`config/settings.yaml`, `config/templates/*.yaml`, `data/student_profile.yaml`. Tests mirror modules under `tests/`
(fakes in `tests/fakes.py`; `tests/world.py` builds ready-to-send targets).
