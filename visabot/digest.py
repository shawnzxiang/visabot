"""Daily digest email; also the out-of-process watchdog for a silent poller."""
import json
from datetime import datetime, timedelta

from . import config, notifier
from .store import Store


def build(store: Store) -> tuple[str, str]:
    now = datetime.now()
    day_ago = (now - timedelta(hours=24)).isoformat(timespec="seconds")

    # health
    last_poll = store.kv_get("last_poll_ts")
    stale = True
    if last_poll:
        stale = (now - datetime.fromisoformat(last_poll)) > timedelta(hours=2)
    backoff = store.backoff_until()
    booked = store.booked()

    if booked:
        mode = "BOOKED — idle"
    elif not config.window_open():
        mode = "window closed — idle"
    elif backoff:
        mode = f"backoff until {backoff:%Y-%m-%d %H:%M}"
        stale = False
    else:
        mode = "polling"

    subject = "Daily digest — "
    if booked:
        subject += "BOOKED ✔"
    elif stale:
        subject += "⚠ POLLER STALE"
    else:
        subject += mode
    lo, hi = config.window()

    lines = [
        f"Mode: {mode}",
        f"Window: {lo} .. {hi} (rolling today+{config.EARLIEST_OFFSET_DAYS})",
        f"Last successful poll: {last_poll or 'never'}" + ("  ← STALE (>2h)" if stale else ""),
        f"Requests today: {store.requests_today()}/{config.DAILY_RUNAWAY_GUARD}",
        f"TEST_MODE: {'ON' if config.TEST_MODE else 'off'}",
        "",
    ]

    if booked:
        details = json.loads(store.kv_get("booked_details", "{}"))
        lines += [f"Booked: {details.get('post')} {details.get('date')} {details.get('time')} "
                  f"(at {details.get('at')})", ""]

    # Earliest appointment per consulate: where it stands now, and the best it got.
    rows = store.availability_since(day_ago)
    by_post: dict[str, list] = {}
    for ts, post, d in rows:
        by_post.setdefault(post, []).append((ts, d))

    lines.append("EARLIEST APPOINTMENT PER CONSULATE  (now / best in 24 h):")
    if by_post:
        for post in config.ROTATION:
            entries = by_post.get(post)
            if not entries:
                continue
            dates = [d for _, d in entries if d]
            latest = next((d for _, d in reversed(entries) if d), None)
            best = min(dates) if dates else None
            improved = " ** IMPROVED **" if best and latest and best < latest else ""
            n = len(entries)
            lines.append(
                f"  {post:<12} {latest or 'none':<12} / {best or 'none':<12}"
                f"  ({n} check{'' if n == 1 else 's'}){improved}")
    else:
        lines.append("  no checks recorded — see poller health above")
    lines.append("")

    all_best = [d for entries in by_post.values() for _, d in entries if d]
    if all_best:
        lines.append(f"Best date anywhere in Canada today: {min(all_best)}")
        lo, hi = config.window()
        lines.append(f"Needed for a booking: {lo} .. {hi}"
                     + ("  <-- IN RANGE" if min(all_best) <= hi.isoformat() else ""))
        lines.append("")

    best_ever = store.best_ever_by_post()
    if best_ever:
        lines.append("Earliest date ever recorded, per consulate (all time):")
        for post, d, ts in sorted(best_ever, key=lambda r: r[1]):
            lines.append(f"  {post:<12} {d} (seen {ts[:16].replace('T', ' ')})")
        lines.append("")

    attempts = store.attempts_since(day_ago)
    if attempts:
        lines.append("Booking attempts (24 h):")
        lines += [f"  {ts}  {post} {d} {t or ''} -> {outcome}"
                  for ts, post, d, t, outcome in attempts]
        lines.append("")

    problems = [e for e in store.events_since(day_ago) if e[1] in ("warn", "error")]
    if problems:
        lines.append(f"Warnings/errors (24 h, {len(problems)}):")
        lines += [f"  {ts}  [{code}] {msg[:150]}" for ts, level, code, msg in problems[-15:]]
    else:
        lines.append("No warnings or errors in the last 24 h.")

    return subject, "\n".join(lines)


def run(store: Store) -> None:
    subject, body = build(store)
    # The digest is the daily heartbeat: quiet priority normally, loud when stale.
    notifier.send_alert(store, subject, body, urgent="STALE" in subject)
