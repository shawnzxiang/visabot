# AIS Canada Visa Appointment Bot

Watches all 7 US consulates in Canada on `ais.usvisa-info.com/en-ca` and auto-books
the first H-1B slot inside the window **[today + 5 days → Aug 21, 2026]** (rolling:
on Aug 5 it accepts Aug 10 onward). Pure HTTP — no browser, no Selenium.

Emails `shawnzxiang@gmail.com`: instant alerts (slot found / booked / problems) and a
daily 8:00 AM digest that doubles as a watchdog.

## One-time setup

1. **Gmail App Password**: Google Account → Security → 2-Step Verification (must be on)
   → App passwords → create one for "Mail". Copy the 16 characters.
2. Run `./setup.sh` — it creates `.env` from the template.
3. Edit `.env`: put in your **AIS login** (the email/password you use on
   ais.usvisa-info.com) and the **Gmail app password**. Keep `TEST_MODE=true` for now.

## Rollout (in order)

```bash
.venv/bin/python -m visabot.main --test-email   # 1. email plumbing works?
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

- Daily digest at 8:00 AM: mode, budget use, per-post availability history, errors.
  Subject shows **⚠ POLLER STALE** if the poller has been silent >2 h.
- On a booked slot the bot verifies it actually landed (the portal is known to
  produce phantom "successes"), emails **BOOKED & VERIFIED**, then stops polling.
  If it books a non-Calgary post, the email reminds you to file a new DS-160 for
  that post (your current DS-160 names Calgary).
- Soft-ban protection: max 40 requests/day on a jittered duty cycle (20 min evenings,
  60 min daytime, 180 min overnight). If the site soft-bans anyway, the bot backs
  off 8 h and tells you.
- If a captcha ever appears, the bot stops and emails you — log in manually once in
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
