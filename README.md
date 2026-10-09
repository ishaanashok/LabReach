# LabReach Auto

A fully local outreach agent that finds engineering professors and emails them on behalf of a high school student.
Everything runs on your computer: no API keys, no cloud services, no extra cost. Language work goes through the
Claude Code CLI (`claude`) that is already logged in on your machine. **Dry-run is the default; nothing is sent
until you switch to live and approve the pilot.**

> Status: steps 1-4 of the rollout (scaffold, profile, templates, linter, claim verifier) are built and tested.
> Discovery, Gmail (SMTP/IMAP), scheduling and the live pilot come next. The full operations guide (keeping the
> computer awake in send windows, scheduler install, how to stop everything) is written at the end.

## Quick start

```bash
python3 -m venv .venv && source .venv/bin/activate
pip install -e ".[dev,tools]"
labreach init            # creates ./labreach_data (gitignored)
labreach doctor          # confirms your installed `claude` supports every flag we use
pytest                   # all tests run against mocks; no network, no Gmail
```

Private files (never committed): `data/private/contact.yaml` (phone, date of birth) and the resume PDF.
Rebuild the one-page resume with `python tools/build_resume.py`.

## Approval gates

1. `labreach profile show` / `labreach profile approve`: the facts emails may cite (`data/student_profile.yaml`).
2. `labreach templates build` then `labreach templates approve --yes`: locks template skeletons by hash.
   Any later template edit is rejected at render time until approved again.

## Safety rules enforced in code

- Initial emails contain no links (the resume carries them) and attach only the resume.
- Recipients and addresses come only from code that reads official pages, never from model output.
- `ANTHROPIC_API_KEY` is stripped from every `claude` subprocess so usage counts against your subscription.
- The kill switch (`labreach stop`, `./labreach_data/STOP`, or any automatic stop condition) halts all sending.
