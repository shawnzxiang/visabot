"""Entry point: python -m visabot.main [--poll|--once|--recon|--digest|--test-email|--simulate F]."""
import argparse
import logging
import sys
import traceback
from logging.handlers import RotatingFileHandler
from pathlib import Path

from . import config, detector, digest, notifier, recon, scheduler, store as store_mod
from .store import Store


def setup_logging() -> None:
    config.LOGS_DIR.mkdir(parents=True, exist_ok=True)
    fmt = logging.Formatter("%(asctime)s %(levelname)s %(name)s: %(message)s")
    root = logging.getLogger()
    root.setLevel(logging.INFO)
    fh = RotatingFileHandler(config.LOGS_DIR / "app.log", maxBytes=5_000_000, backupCount=5)
    fh.setFormatter(fmt)
    root.addHandler(fh)
    sh = logging.StreamHandler()
    sh.setFormatter(fmt)
    root.addHandler(sh)


def check_env() -> list[str]:
    missing = []
    if not config.AIS_EMAIL or "@" not in config.AIS_EMAIL or "example.com" in config.AIS_EMAIL:
        missing.append("AIS_EMAIL")
    if not config.AIS_PASSWORD:
        missing.append("AIS_PASSWORD")
    if not config.GMAIL_USER:
        missing.append("GMAIL_USER")
    if not config.GMAIL_APP_PASSWORD or config.GMAIL_APP_PASSWORD.startswith("xxxx"):
        missing.append("GMAIL_APP_PASSWORD")
    return missing


def simulate(store: Store, fixture: Path) -> None:
    html = fixture.read_text()
    avail = detector.parse_payment_page(html)
    print("parsed availability:")
    for post, d in sorted(avail.items()):
        print(f"  {post:<12} {d or 'no appointments'}")
    lo, hi = config.window()
    print(f"window: {lo} .. {hi}")
    cand = detector.pick_candidate(avail)
    if not cand:
        print("no candidate in window -> the poller would keep polling.")
        return
    post, d = cand
    print(f"CANDIDATE: {post} {d}")
    print("the poller would now: email SLOT FOUND -> days.json -> times.json -> "
          f"POST facility_id={config.FACILITIES[post]} date={d} time=<first available> "
          f"(TEST_MODE={'ON, stops before POST' if config.TEST_MODE else 'OFF — would submit'})")


def main() -> int:
    p = argparse.ArgumentParser(prog="visabot")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--poll", action="store_true", help="run the polling daemon (launchd entry)")
    g.add_argument("--once", action="store_true", help="one poll cycle, then exit")
    g.add_argument("--recon", action="store_true", help="read-only site walk, saves snapshots")
    g.add_argument("--digest", action="store_true", help="send the daily digest email")
    g.add_argument("--test-email", action="store_true", help="send a test email and exit")
    g.add_argument("--simulate", metavar="FIXTURE", help="run detector+decision on a saved payment page, no network")
    args = p.parse_args()

    setup_logging()
    log = logging.getLogger("visabot")

    missing = check_env()
    needs_ais = args.poll or args.once or args.recon
    fatal = [m for m in missing if needs_ais or m.startswith("GMAIL")]
    if args.simulate:
        fatal = []
    if fatal:
        print(f"bot/.env is incomplete — fill in: {', '.join(fatal)} "
              f"(copy .env.example to .env first)", file=sys.stderr)
        return 2

    # Only modes that generate AIS traffic are mutually exclusive; the digest and
    # offline modes must be able to run while the poller daemon is up.
    if needs_ais and not store_mod.acquire_lock():
        print("another visabot instance holds the lock — exiting "
              "(stop the launchd poller first: launchctl bootout gui/$(id -u)/com.shawn.visabot.poller)",
              file=sys.stderr)
        return 3
    store = Store()

    try:
        if args.test_email:
            notifier.send_test(store)
            print(f"test email sent to {config.ALERT_TO}")
        elif args.simulate:
            simulate(store, Path(args.simulate))
        elif args.recon:
            recon.run(store)
        elif args.once:
            from .client import AisClient
            client = AisClient(store)
            scheduler.poll_once(client, store)
            print(f"poll ok. budget used today: {store.requests_today()}/{config.DAILY_REQUEST_BUDGET}")
        elif args.digest:
            digest.run(store)
            print("digest sent")
        elif args.poll:
            scheduler.run_loop(store)
    except KeyboardInterrupt:
        return 130
    except Exception:
        tb = traceback.format_exc()
        log.error("fatal: %s", tb)
        if args.poll:
            notifier.send_alert(store, "Poller CRASHED — launchd will restart it",
                                tb[-3000:], urgent=True)
        raise
    return 0


if __name__ == "__main__":
    sys.exit(main())
