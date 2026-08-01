"""Duty-cycle polling loop with soft-ban budget discipline and burst booking."""
import json
import logging
import random
import time
from datetime import datetime, timedelta

from . import booker, config, confirmer, detector, notifier, verifier
from .client import (AisClient, CaptchaDetected, LoginFailed, MaintenancePage,
                     NeedsRecon, SessionExpired)
from .store import Store

log = logging.getLogger(__name__)

SIGN_IN_URL = f"{config.BASE}/users/sign_in"


def next_delay_seconds(now: datetime | None = None) -> float:
    """5-9 min between polls, doubled overnight. Jitter also avoids a synchronised
    burst every time the daemon restarts."""
    now = now or datetime.now()
    delay = config.POLL_BASE_SECONDS + random.uniform(0, config.POLL_JITTER_SECONDS)
    night_start, night_end = config.NIGHT_HOURS
    if night_start <= now.hour < night_end:
        delay *= config.NIGHT_MULTIPLIER
    return delay


def _pace() -> None:
    """Small gap between the requests of one burst."""
    time.sleep(random.uniform(2.0, 5.0))


def check_post(client: AisClient, store: Store, post: str) -> list:
    """One days.json read for one consulate. Records history, returns parsed dates."""
    dates = detector.parse_days(client.get_days(config.FACILITIES[post]))
    store.record_availability({post: min(dates) if dates else None})
    store.record_date_count(post, len(dates))
    store.kv_set("last_poll_ts", datetime.now().isoformat(timespec="seconds"))
    log.info("%s: %d dates%s", post, len(dates),
             f", earliest {min(dates)}" if dates else " (nothing available)")
    return dates


class SoftBanned(Exception):
    """A canary consulate came back empty — AIS has silently rate-limited us."""


def poll_once(client: AisClient, store: Store) -> None:
    """One rotation step: check the next consulate in the round-robin."""
    client.ensure_session()
    post = detector.next_post(store)
    dates = check_post(client, store, post)

    if detector.ban_suspected(store, post, len(dates)):
        raise SoftBanned(f"{post} returned 0 dates but has shown up to "
                         f"{store.peak_dates(post)} — soft-ban")
    if dates:
        # Anything flowing means we are definitely not banned.
        store.set_ban_streak(0)

    cand = detector.earliest_in_window(dates)
    if cand:
        run_burst(client, store, post, cand)


def sweep(client: AisClient, store: Store) -> dict:
    """Check ALL 7 posts once (7 requests). Used by --sweep, not by the poller."""
    client.ensure_session()
    out = {}
    for i, post in enumerate(config.ROTATION):
        if i:
            _pace()
        dates = check_post(client, store, post)
        out[post] = min(dates) if dates else None
    return out


def run_burst(client: AisClient, store: Store, post: str, cand_date) -> None:
    notifier.send_alert(
        store, f"SLOT FOUND: {post} {cand_date} — booking now",
        f"{post} is showing {cand_date}, inside your window "
        f"{config.window()[0]} .. {config.window()[1]}.\n"
        f"The bot is attempting to book it RIGHT NOW.\n\n"
        f"If you want to try manually as backup: {SIGN_IN_URL}\n"
        f"(TEST_MODE={'ON — no booking will be submitted' if config.TEST_MODE else 'off'})",
        emergency=True,
    )
    date_str = cand_date.isoformat()
    _pace()
    time_str = confirmer.pick_time(client, post, date_str)
    if time_str is None:
        store.event("info", "sniped", f"{post} {date_str}: no times left")
        return
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
            emergency=True,
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
        notifier.send_alert(store, f"BOOKED & VERIFIED: {post} {date_str} {time_str}", body,
                            emergency=True)
    else:
        notifier.send_alert(
            store, f"BOOKING FAILED at {post} {date_str} — still polling",
            f"The booking POST did not verify.\nDetail: {detail}\nEvidence: {evidence}\n"
            f"Attempts today: {store.attempts_today()}/{config.MAX_BOOK_ATTEMPTS_PER_DAY}, "
            f"total: {store.attempts_total()}/{config.MAX_BOOK_ATTEMPTS_TOTAL}.\n"
            "Polling continues.",
            emergency=True,
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
            store.event("error", "runaway_guard",
                        f"{store.requests_today()} requests today — halting until midnight")
            notifier.send_alert(
                store, "Runaway guard tripped — polling halted",
                f"The bot made {store.requests_today()} requests today, which should not "
                "happen at normal cadence. Polling is halted until midnight as a safety "
                "stop. This usually means a bug or a restart loop — worth a look.",
                urgent=True)
            time.sleep(1800)
            continue

        try:
            poll_once(client, store)
            maintenance_streak = 0
            login_fail_streak = 0
        except SoftBanned as exc:
            # AIS bans on cumulative request count, per ACCOUNT — a new session or
            # a different IP does not clear it, only time does. Back off, then probe;
            # the streak resets as soon as any consulate returns dates again.
            streak = store.ban_streak() + 1
            store.set_ban_streak(streak)
            ladder = config.BAN_BACKOFF_LADDER
            wait = ladder[min(streak - 1, len(ladder) - 1)]
            store.event("warn", "softban", f"{exc} (streak {streak}, waiting {wait // 60}m)")
            store.set_backoff(datetime.now() + timedelta(seconds=wait),
                              f"soft-ban #{streak}")
            notifier.send_alert(
                store, f"Soft-banned by AIS — pausing {wait // 60} min",
                f"{exc}\n\nThis is AIS's silent rate limit: it answers with HTTP 200 and "
                "an empty list. It is keyed to the account and typically lifts in about "
                "5 hours, though often sooner.\n"
                f"Consecutive bans: {streak}. The bot will probe again in {wait // 60} "
                "minutes and resume full speed as soon as dates flow.")
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
        except LoginFailed as exc:
            # Never retry rejected credentials on a schedule — AIS locks accounts.
            store.event("error", "login_rejected", str(exc))
            store.set_backoff(datetime.now() + timedelta(hours=12), "credentials rejected")
            notifier.send_alert(
                store, "AIS LOGIN REJECTED — bot paused",
                f"The site rejected the sign-in ({exc}).\n\n"
                "The bot has STOPPED trying so your AIS account does not get locked out "
                "by repeated failures.\n"
                f"Fix AIS_EMAIL / AIS_PASSWORD in bot/.env (verify by signing in manually "
                f"at {SIGN_IN_URL}), then restart:\n"
                "  launchctl kickstart -k gui/$(id -u)/com.shawn.visabot.poller",
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
