# AIS Canada Visa Appointment Bot

Watches all 7 US consulates in Canada on `ais.usvisa-info.com/en-ca` and auto-books
the first H-1B slot inside the window **[today + 5 days → Aug 21, 2026]** (rolling:
on Aug 5 it accepts Aug 10 onward). Pure HTTP — no browser, no Selenium.

Pushes to your iPhone via **Pushover**: instant alerts (slot found / booked / problems)
and a daily 8:00 AM digest that doubles as a heartbeat — if the digest doesn't arrive,
the bot is down. Slot-found and booked alerts use Pushover's *Emergency* priority:
your phone re-rings every 30 s until you tap Acknowledge, even if the Mac dies.

## How it polls

AIS has **no** "earliest date at every consulate" endpoint for an account that has
paid but not yet booked — `/schedule/{id}/payment` just redirects to the booking
form. Availability can only be read one consulate at a time from
`days/{facility_id}.json`, so the poller **round-robins** through the 7 posts, one
request per cycle, and the rotation position survives restarts.

**Cadence: one consulate every 5–9 minutes** (300 s + 0–240 s jitter), doubled
overnight (01:00–06:00). That's ~150–180 checks/day, so **each consulate is checked
roughly every 45–70 minutes**. These are the values an actively-maintained Canada
fork (`sunjogthind/usvisa-ca-script`) converged on after weeks of real use. There is
no daily quota; `DAILY_RUNAWAY_GUARD = 400` is only a bug-stop.

**Soft-ban handling.** AIS rate-limits by *cumulative request count per account*
(~50–110 requests within a few hours) and answers with HTTP 200 and an empty array —
byte-identical to "no appointments." It is keyed to the account, so a new session,
a re-login, or a different IP does **not** clear it; only time does (~5 h typical,
range 3–24 h).

The bot tells a ban from genuine emptiness with a **canary**: Vancouver, Ottawa and
Toronto normally return 139/123/24 open dates, so if one of them returns zero we are
banned — a calendar holding 100+ days cannot empty between two polls. Calgary,
Montreal, Quebec City and Halifax are empty all the time and are never used as
canaries. The canaries sit in the normal rotation (which interleaves them with the
empty posts), so detection costs no extra requests. On detection the bot backs off
30 min → 1 h → 2 h → 4 h → 5 h, probing at each step, and resets to full speed the
moment any consulate returns dates again.

⚠️ **AIS prohibits automated access.** Its Terms of Use, its FAQ, and a notice linked
from the sign-in page all say so, and the stated remedy is account suspension or
termination. The FAQ adds that "repeatedly refreshing a page inside the system over
an extended period of time can lead to the deactivation of your account." No numeric
limit is published anywhere. Running this tool accepts that risk on your own account.
The bot will not solve CAPTCHAs, rotate user-agents, or use proxies to evade
detection — when the site says stop, it stops.

⚠️ **This bot must not live in `~/Documents`, `~/Desktop`, or `~/Downloads`.** macOS
TCC blocks launchd background agents from reading those folders — the process hangs
during Python startup with no error anywhere. That's why it lives in `~/visabot`.

## Deploying to the Mac mini

Run these **on the mini**. Nothing secret is in the repo — `.env` is gitignored — so
you enter credentials once, there.

```bash
git clone https://github.com/shawnzxiang/visabot.git ~/visabot && cd ~/visabot && ./setup.sh
```

`setup.sh` finds a Python 3.10+, builds the venv, generates launchd plists for the
mini's own paths, and **refuses to install** inside `~/Documents`/`~/Desktop`/`~/Downloads`
(see the TCC warning below). Then:

```bash
nano ~/visabot/.env      # AIS_EMAIL, AIS_PASSWORD, PUSHOVER_USER_KEY, PUSHOVER_APP_TOKEN
```

Verify, then start it:

```bash
cd ~/visabot && .venv/bin/python -m visabot.main --test-notify && .venv/bin/python -m visabot.main --sweep && ./setup.sh --load
```

**Run exactly one poller at a time.** AIS's rate limit is per *account*, not per
machine, so two pollers double the request rate and roughly halve the time to a
soft-ban — and both could race to book the same slot. Stop one with
`launchctl bootout gui/$(id -u)/com.shawn.visabot.poller`.

Make sure the mini never sleeps: **System Settings → Energy → Prevent automatic
sleeping when the display is off**, or `sudo pmset -c sleep 0`.

To pick up later changes: `cd ~/visabot && git pull && ./setup.sh --load`.

## One-time setup

1. **Pushover** (~5 min, no 2FA anywhere):
   - iPhone: install **"Pushover Notifications"** from the App Store, create the
     account in-app (email + password), and **buy the $4.99 unlock immediately** —
     if the 30-day trial lapses, delivery fails *silently*.
   - Browser: log in at [pushover.net](https://pushover.net) → copy the 30-char
     **User Key** (top of dashboard) → "Create an Application/API Token" (name it
     `h1b-watcher`) → copy the **API Token**.
   - iPhone Settings → Notifications → Pushover → enable **Critical Alerts** and
     **Time Sensitive** so Focus/Do-Not-Disturb can't swallow an emergency alert.
2. Edit `.env`: your **AIS login** (the email/password for ais.usvisa-info.com) and
   the two **Pushover keys**. Keep `TEST_MODE=true` for now.

## Rollout (in order)

```bash
.venv/bin/python -m visabot.main --test-notify  # 1. push plumbing works? (check phone)
.venv/bin/python -m visabot.main --recon        # 2. read-only site walk, saves recon/*.html
.venv/bin/python -m visabot.main --once         # 3. one real poll, check output + DB
```

Review what `--recon` prints (schedule_id found? facility list matches? payment page
parsed?). If the payment-page parse looks wrong, the raw HTML is in `recon/` — fix
`visabot/detector.py` and re-run tests: `.venv/bin/python -m pytest tests/ -q`.

Go live:

```bash
cp launchd/*.plist ~/Library/LaunchAgents/
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.shawn.visabot.poller.plist
launchctl bootstrap gui/$(id -u) ~/Library/LaunchAgents/com.shawn.visabot.digest.plist
tail -f logs/app.log
```

After 1–2 clean days (digest arrives, no errors), set `TEST_MODE=false` in `.env` and:

```bash
launchctl kickstart -k gui/$(id -u)/com.shawn.visabot.poller
```

With TEST_MODE on, the bot does everything except the final booking POST — if a slot
appears it emails "TEST_MODE: would have booked …" instead. Given how contested slots
are, flip to live as soon as `--recon`/`--once` look right.

## Day-to-day

- Daily digest push at 8:00 AM: mode, budget use, per-post availability history,
  errors. Title shows **⚠ POLLER STALE** if the poller has been silent >2 h.
  Long digests arrive split into 2–3 numbered messages (Pushover's 1024-char cap).
- On a booked slot the bot verifies it actually landed (the portal is known to
  produce phantom "successes"), sends **BOOKED & VERIFIED** (emergency priority),
  then stops polling. If it books a non-Calgary post, the message reminds you to
  file a new DS-160 for that post (your current DS-160 names Calgary).
- Soft-ban protection: max 40 requests/day on a jittered duty cycle (20 min evenings,
  60 min daytime, 180 min overnight). If the site soft-bans anyway, the bot backs
  off 8 h and tells you.
- If a captcha ever appears, the bot stops and notifies you — log in manually once in
  a normal browser, which usually clears it. It never attempts to bypass captchas.

## Controls

```bash
launchctl print gui/$(id -u)/com.shawn.visabot.poller        # status
launchctl kickstart -k gui/$(id -u)/com.shawn.visabot.poller # restart (after .env edits)
launchctl bootout gui/$(id -u)/com.shawn.visabot.poller      # stop for good
launchctl bootout gui/$(id -u)/com.shawn.visabot.digest
sqlite3 state/bot.db 'SELECT * FROM events ORDER BY ts DESC LIMIT 20;'
```

To extend the deadline past Aug 21: edit `LATEST_DATE` in `.env`, restart the poller.

## Caveats (known going in)

- Soft-ban thresholds are community anecdotes (~50–110 requests/day); 40/day is
  conservative but not guaranteed safe.
- The site's HTML shifts every few months; parsers are isolated in `detector.py` /
  `client.py` and failures email you with the saved HTML.
- Policy: since late 2025 the State Dept. directs applicants to interview in their
  country of nationality/residence; third-country booking in Canada still works at
  the booking layer but carries interview-level risk. Your call, ideally with counsel.
- An Aug 5–21 window across 7 posts is a long shot — the bot maximizes your odds;
  it cannot create slots.
