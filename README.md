# AIS Canada Visa Appointment Bot

Watches all 7 US consulates in Canada on `ais.usvisa-info.com/en-ca` and auto-books
the first slot inside your window. Pure HTTP — no browser. Pushes to your iPhone via
Pushover.

```
                    ┌─────────────────────────────────────────┐
  launchd (poller)  │  every 3-5 min: all 7 consulates,        │
  KeepAlive ───────►│  back-to-back — GET days/{facility}.json │
                    └──────────────┬──────────────────────────┘
                                   │ per consulate
                     ┌─────────────┴─────────────┐
                     ▼                           ▼
             date in window?                nothing yet
                     │                           │
                     ▼                           └─► next consulate,
   ① push "SLOT FOUND", burst NOW                    then sleep 3-5 min
              │
              ▼
   GET times/{facility}.json
              │
              ▼
   POST /schedule/{id}/appointment      ② push "BOOKED & VERIFIED"
              │                              (or "BOOKING FAILED")
              ▼                                      ▲
   re-read account: did the date actually appear? ───┘

  launchd (digest) ── 21:00 daily ──► ③ push summary + heartbeat
```

Three ways you hear from it: **①** the instant slot alert (fires *before* the booking
attempt, so you can also try manually), **②** the booking result, verified against
your account because the portal is known to report phantom successes, and **③** the
9 PM digest — if it doesn't arrive, the bot is down.

## Cadence

**All 7 consulates every 3–5 minutes** (180 s + 0–120 s jitter between cycles;
posts within a cycle are checked back-to-back, 2–5 s apart, Calgary first since
the DS-160 on file names it). Each consulate is seen every cycle — ~2,500
requests/day. Inside a cycle the bot bursts the moment any post shows a date in
the window, rather than finishing the scan first.

There is no aggregate "all consulates" endpoint for an unbooked account, so posts
are fetched one days.json at a time. AIS rate-limits by *cumulative request count
per account* and answers a ban with HTTP 200 and an empty list — indistinguishable
from "no appointments." **This bot makes no attempt to detect that**: an empty
answer is simply recorded as "none available" and polling continues. A ban is
per-account (re-login, new session and new IP do nothing; only time — reportedly
~5 h — clears it), so if every post suddenly reads empty for hours, consider
slowing `POLL_BASE_SECONDS` down.

⚠️ AIS prohibits automated access in its Terms of Use, FAQ, and a notice on the
sign-in page; the stated remedy is account suspension. No numeric limit is published.
Running this accepts that risk on your own account. The bot will not solve CAPTCHAs,
rotate user-agents, or use proxies.

## Configuration — `.env`

| Key | Meaning |
|---|---|
| `AIS_EMAIL` / `AIS_PASSWORD` | your ais.usvisa-info.com login |
| `PUSHOVER_USER_KEY` / `PUSHOVER_APP_TOKEN` | from pushover.net (30 chars each) |
| `EARLIEST_OFFSET_DAYS` | earliest acceptable date = today + this (default 5) |
| `LATEST_DATE` | latest acceptable date, `YYYY-MM-DD` |
| `TEST_MODE` | `true` = do everything except the final booking POST |
| `POLL_BASE_SECONDS` / `POLL_JITTER_SECONDS` | cadence, default 180 / 120 |
| `NIGHT_MULTIPLIER` | delay multiplier 01:00–06:00; `1` = full speed all night (default 2) |
| `DASHBOARD_PORT` | local dashboard port (default 8765) |

Restart after editing: `launchctl kickstart -k gui/$(id -u)/com.shawn.visabot.poller`
— or edit and restart from the dashboard instead.

## Dashboard

`com.shawn.visabot.dashboard` serves **http://127.0.0.1:8765** (loopback only):
polling history, events, earliest date per consulate, and a `.env` editor —
secrets stay masked until revealed, saves validate and can kickstart the poller.
Reveal/save need the `X-Visabot: 1` header plus a localhost `Host`, so random
web pages can't reach it cross-origin.

Pushover setup: install "Pushover Notifications" on iPhone → create account in-app →
**buy the $4.99 unlock immediately** (trial expiry fails *silently*) → copy the User
Key and a new API Token from pushover.net → iPhone Settings → Notifications →
Pushover → enable **Critical Alerts** + **Time Sensitive**.

## Install

Must not live in `~/Documents`, `~/Desktop` or `~/Downloads` — macOS blocks
background agents from reading those, and the bot hangs silently. `setup.sh` refuses
to install there.

```bash
git clone https://github.com/shawnzxiang/visabot.git ~/visabot && cd ~/visabot && ./setup.sh
```

```bash
nano ~/visabot/.env
```

```bash
cd ~/visabot && .venv/bin/python -m visabot.main --test-notify
```

```bash
cd ~/visabot && .venv/bin/python -m visabot.main --recon
```

```bash
cd ~/visabot && .venv/bin/python -m visabot.main --sweep
```

```bash
cd ~/visabot && ./setup.sh --load
```

Keep the machine awake: System Settings → Energy → prevent sleeping, or
`sudo pmset -c sleep 0`. **Run only one poller across all your machines** — the rate
limit is per account, so two double the ban rate and can race to book the same slot.

## Commands

```bash
.venv/bin/python -m visabot.main --sweep        # earliest date at all 7 posts, now
```

```bash
.venv/bin/python -m visabot.main --digest       # send the daily summary immediately
```

```bash
.venv/bin/python -m visabot.main --once         # one rotation step
```

```bash
.venv/bin/python -m visabot.main --recon        # read-only site walk, saves recon/*.html
```

```bash
.venv/bin/python -m pytest tests/ -q            # 41 offline tests
```

```bash
tail -f logs/app.log                            # live activity
```

```bash
sqlite3 state/bot.db 'SELECT * FROM events ORDER BY ts DESC LIMIT 20;'
```

## Control

Easiest: the dashboard's **Agents** panel starts, stops, and restarts the poller
and digest. Stop also removes the agent's plist from `~/Library/LaunchAgents`,
so it stays stopped across logins; Start copies it back and bootstraps it.
Terminal equivalents:

```bash
launchctl print gui/$(id -u)/com.shawn.visabot.poller
```

```bash
launchctl kickstart -k gui/$(id -u)/com.shawn.visabot.poller
```

```bash
launchctl bootout gui/$(id -u)/com.shawn.visabot.poller
```

```bash
launchctl bootout gui/$(id -u)/com.shawn.visabot.digest
```

```bash
cd ~/visabot && git pull && ./setup.sh --load    # update to latest
```

After a verified booking the poller stops on its own and the confirmation push tells
you what to do next — including filing a new DS-160 if it booked somewhere other than
the post your current DS-160 names.
