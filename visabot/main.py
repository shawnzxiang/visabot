"""Entry point: python -m visabot.main [--poll|--once|--recon|--digest|--test-email|--simulate F]."""
import argparse
import json
import logging
import sys
import traceback
from logging.handlers import RotatingFileHandler
from pathlib import Path

from . import client as client_mod
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


def check_env(needs_ais: bool) -> list[str]:
    from . import notifier
    missing = []
    if needs_ais:
        if not config.AIS_EMAIL or "@" not in config.AIS_EMAIL or "example.com" in config.AIS_EMAIL:
            missing.append("AIS_EMAIL")
        if not config.AIS_PASSWORD or config.AIS_PASSWORD == "your-ais-password":
            missing.append("AIS_PASSWORD")
    if not (notifier._pushover_configured() or notifier._gmail_configured()):
        missing.append("PUSHOVER_USER_KEY + PUSHOVER_APP_TOKEN")
    return missing


def simulate(store: Store, fixture: Path) -> None:
    """Feed a saved days.json response through the decision logic. No network."""
    payload = json.loads(fixture.read_text())
    dates = detector.parse_days([item["date"] for item in payload])
    lo, hi = config.window()
    print(f"days.json had {len(dates)} dates: {[d.isoformat() for d in dates][:8]}")
    print(f"window: {lo} .. {hi}")
    cand = detector.earliest_in_window(dates)
    if not cand:
        print("nothing in window -> the poller would move to the next consulate.")
        return
    post = "Toronto"  # illustrative; the real post comes from the rotation
    print(f"CANDIDATE: {cand}")
    print(f"the poller would now: push SLOT FOUND -> times.json -> "
          f"POST facility_id={config.FACILITIES[post]} date={cand} time=<first available> "
          f"(TEST_MODE={'ON, stops before POST' if config.TEST_MODE else 'OFF — would submit'})")


def main() -> int:
    p = argparse.ArgumentParser(prog="visabot")
    g = p.add_mutually_exclusive_group(required=True)
    g.add_argument("--poll", action="store_true", help="run the polling daemon (launchd entry)")
    g.add_argument("--once", action="store_true", help="one rotation step (1 consulate), then exit")
    g.add_argument("--sweep", action="store_true",
                   help="check all 7 consulates once and print earliest dates (7 requests)")
    g.add_argument("--recon", action="store_true", help="read-only site walk, saves snapshots")
    g.add_argument("--digest", action="store_true", help="send the daily digest email")
    g.add_argument("--test-notify", action="store_true", help="send a test push notification and exit")
    g.add_argument("--test-email", dest="test_notify", action="store_true",
                   help=argparse.SUPPRESS)  # legacy alias
    g.add_argument("--simulate", metavar="FIXTURE", help="run detector+decision on a saved payment page, no network")
    args = p.parse_args()

    setup_logging()
    log = logging.getLogger("visabot")

    needs_ais = args.poll or args.once or args.recon or args.sweep
    fatal = [] if args.simulate else check_env(needs_ais)
    if fatal:
        print(f"bot/.env is incomplete — fill in: {', '.join(fatal)}", file=sys.stderr)
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
        if args.test_notify:
            notifier.send_test(store)
            print("test push sent — check your phone (Pushover app)")
        elif args.simulate:
            simulate(store, Path(args.simulate))
        elif args.recon:
            recon.run(store)
        elif args.once:
            client = client_mod.AisClient(store)
            scheduler.poll_once(client, store)
            print(f"poll ok. budget used today: {store.requests_today()}/{config.DAILY_RUNAWAY_GUARD}")
        elif args.sweep:
            client = client_mod.AisClient(store)
            avail = scheduler.sweep(client, store)
            lo, hi = config.window()
            print(f"\nEarliest available appointment per consulate "
                  f"(window {lo} .. {hi}):\n")
            for post in config.ROTATION:
                d = avail.get(post)
                mark = "  <-- IN WINDOW" if d and config.in_window(d) else ""
                print(f"  {post:<12} {d.isoformat() if d else 'none available'}{mark}")
            print(f"\nbudget used today: {store.requests_today()}/{config.DAILY_RUNAWAY_GUARD}")
        elif args.digest:
            digest.run(store)
            print("digest sent")
        elif args.poll:
            scheduler.run_loop(store)
    except KeyboardInterrupt:
        return 130
    except client_mod.LoginFailed as exc:
        print(f"\nAIS rejected the sign-in: {exc}\n"
              "Not retrying — repeated failures can lock the account.\n"
              "Verify AIS_EMAIL / AIS_PASSWORD in bot/.env by signing in manually at\n"
              f"  {config.BASE}/users/sign_in", file=sys.stderr)
        store.event("error", "login_rejected", str(exc))
        return 4
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
