"""Duty-cycle polling loop with soft-ban budget discipline and burst booking."""
import json
import logging
import random
import time
from datetime import datetime, timedelta

from . import booker, config, confirmer, detector, notifier, verifier
from .client import AisClient, CaptchaDetected, MaintenancePage, NeedsRecon, SessionExpired
from .store import Store

log = logging.getLogger(__name__)

SIGN_IN_URL = f"{config.BASE}/users/sign_in"


def next_delay_seconds(now: datetime | None = None) -> float:
    now = now or datetime.now()
    cadence_min = 60
    for start, end, cadence in config.TIERS:
        if start <= now.hour < end:
            cadence_min = cadence
            break
    jitter = random.uniform(-config.JITTER_FRAC, config.JITTER_FRAC)
    return cadence_min * 60 * (1 + jitter)


def _pace() -> None:
    """Human-ish gap between consecutive requests inside a burst."""
    time.sleep(random.uniform(2.0, 5.0))


def poll_once(client: AisClient, store: Store) -> None:
    client.ensure_session()
    html = client.get_payment_page()
    avail = detector.parse_payment_page(html)
    if not avail:
        path = client.save_debug("payment_unparseable", html)
        raise NeedsRecon(f"payment page had no recognizable posts; saved {path}")
    store.record_availability(avail)
    store.kv_set("last_poll_ts", datetime.now().isoformat(timespec="seconds"))

    seen = {p: d.isoformat() for p, d in avail.items() if d}
    log.info("poll ok — dates seen: %s", seen or "none")

    cand = detector.pick_candidate(avail)
    if cand:
        run_burst(client, store, *cand)


def run_burst(client: AisClient, store: Store, post: str, cand_date) -> None:
    notifier.send_alert(
        store, f"SLOT FOUND: {post} {cand_date} — booking now",
        f"The payment page shows {cand_date} at {post}, inside your window "
        f"{config.window()[0]} .. {config.window()[1]}.\n"
        f"The bot is attempting to book it RIGHT NOW.\n\n"
        f"If you want to try manually as backup: {SIGN_IN_URL}\n"
        f"(TEST_MODE={'ON — no booking will be submitted' if config.TEST_MODE else 'off'})",
        urgent=True,
    )
    _pace()
    res = confirmer.confirm(client, post)
    if res is None:
        store.event("info", "sniped", f"{post} {cand_date}: gone or out of window at days/times check")
        return
    date_str, time_str = res
    _pace()
    outcome, payload = booker.book(client, store, post, date_str, time_str)

    if outcome == "test_mode":
        notifier.send_alert(
            store, f"TEST_MODE: would have booked {post} {date_str} {time_str}",
            "TEST_MODE is ON, so the bot stopped right before the booking POST.\n"
            f"It would have submitted: {post} (facility {config.FACILITIES[post]}) "
            f"on {date_str} at {time_str}.\n\nEvidence: {payload}\n\n"
            "If this looks right, set TEST_MODE=false in bot/.env and restart the poller:\n"
            "  launchctl kickstart -k gui/$(id -u)/com.shawn.visabot.poller",
            urgent=True,
        )
        return

    _pace()
    confirmed, evidence, detail = verifier.verify(client, store, post, date_str, time_str, payload)
    if confirmed:
        body = (
            f"BOOKED AND VERIFIED: {post} on {date_str} at {time_str}.\n\n"
            f"Verification: {detail}\n\n"
            "Next steps:\n"
            "1. Log in and print the appointment confirmation: " + SIGN_IN_URL + "\n"
        )
        if post != "Calgary":
            body += (
                f"2. IMPORTANT: your DS-160 was filed for Calgary. File a new DS-160 for {post} "
                "at https://ceac.state.gov/genniv/ before the interview (free, ~30 min), and bring "
                "the new confirmation page.\n"
            )
        body += (
            "3. Retire the bot:\n"
            "   launchctl bootout gui/$(id -u)/com.shawn.visabot.poller\n"
            "   launchctl bootout gui/$(id -u)/com.shawn.visabot.digest\n"
        )
        notifier.send_alert(store, f"BOOKED & VERIFIED: {post} {date_str} {time_str}", body, urgent=True)
    else:
        notifier.send_alert(
            store, f"BOOKING FAILED at {post} {date_str} — still polling",
            f"The booking POST did not verify.\nDetail: {detail}\nEvidence: {evidence}\n"
            f"Attempts today: {store.attempts_today()}/{config.MAX_BOOK_ATTEMPTS_PER_DAY}, "
            f"total: {store.attempts_total()}/{config.MAX_BOOK_ATTEMPTS_TOTAL}.\n"
            "Polling continues.",
            urgent=True,
        )


def run_loop(store: Store) -> None:
    client = AisClient(store)
    maintenance_streak = 0
    login_fail_streak = 0
    store.event("info", "poller_start", f"TEST_MODE={config.TEST_MODE}")

    while True:
        if store.booked():
            details = store.kv_get("booked_details", "{}")
            log.info("booked — idling. %s", details)
            time.sleep(3600)
            continue

        if not config.window_open():
            if store.kv_get("window_closed_notified") != "1":
                notifier.send_alert(
                    store, "WINDOW CLOSED — bot idle",
                    f"Today + {config.EARLIEST_OFFSET_DAYS} days is past {config.LATEST_DATE}; "
                    "no bookable window remains. The bot has stopped polling.\n"
                    "Edit LATEST_DATE in bot/.env and restart if you want to extend.",
                    urgent=True)
                store.kv_set("window_closed_notified", "1")
            time.sleep(3600)
            continue

        backoff = store.backoff_until()
        if backoff:
            time.sleep(min((backoff - datetime.now()).total_seconds() + 5, 600))
            continue

        if store.budget_remaining() <= config.BURST_RESERVE:
            log.info("budget nearly exhausted (%d used) — waiting for midnight reset",
                     store.requests_today())
            time.sleep(1800)
            continue

        try:
            poll_once(client, store)
            maintenance_streak = 0
            login_fail_streak = 0
        except CaptchaDetected as exc:
            store.event("error", "captcha", str(exc))
            store.set_backoff(datetime.now() + timedelta(hours=12), "captcha")
            notifier.send_alert(
                store, "CAPTCHA on sign-in — human needed",
                "The AIS sign-in page rendered a captcha, which this bot will not bypass.\n"
                f"Please log in manually once in a browser ({SIGN_IN_URL}) — that usually "
                "clears the flag. The bot backs off for 12 h and then retries.\n"
                f"Detail: {exc}",
                urgent=True)
        except confirmer.SoftBanSuspected as exc:
            store.event("warn", "softban", str(exc))
            store.set_backoff(datetime.now() + timedelta(hours=config.SOFTBAN_BACKOFF_HOURS),
                             "suspected soft-ban")
            notifier.send_alert(
                store, "Suspected soft-ban — backing off 8h",
                f"{exc}\nThis is the documented AIS soft-ban signature. The bot pauses "
                f"{config.SOFTBAN_BACKOFF_HOURS} h to let it reset.",
                urgent=True)
        except SessionExpired as exc:
            login_fail_streak += 1
            store.event("warn", "session_expired", f"{exc} (streak {login_fail_streak})")
            client.logged_in = False
            if login_fail_streak >= 3:
                store.set_backoff(datetime.now() + timedelta(hours=config.LOGIN_FAIL_BACKOFF_HOURS),
                                 "repeated login failures")
                notifier.send_alert(
                    store, "LOGIN FAILING — check AIS credentials",
                    f"3 consecutive session failures ({exc}).\nPossible causes: wrong AIS_EMAIL/"
                    "AIS_PASSWORD in bot/.env, site change, or account flag. Try logging in "
                    f"manually: {SIGN_IN_URL}",
                    urgent=True)
                login_fail_streak = 0
        except MaintenancePage as exc:
            maintenance_streak += 1
            store.event("warn", "maintenance", f"{exc} (streak {maintenance_streak})")
            if maintenance_streak >= 3:
                store.set_backoff(
                    datetime.now() + timedelta(minutes=config.MAINTENANCE_BACKOFF_MINUTES),
                    "maintenance page streak")
                maintenance_streak = 0
        except NeedsRecon as exc:
            store.event("error", "needs_recon", str(exc))
            notifier.send_alert(
                store, "Site structure changed — bot needs an update",
                f"A page did not look as expected: {exc}\n"
                "The offending HTML is saved under bot/recon/. Polling continues, but "
                "detection may be blind until the parser is updated.")
        except Exception as exc:  # noqa: BLE001 - network blips etc.; keep the daemon alive
            store.event("error", "poll_error", repr(exc))
            log.exception("poll failed")

        time.sleep(next_delay_seconds())
