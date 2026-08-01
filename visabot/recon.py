"""Read-only reconnaissance walk: login and snapshot every page the bot relies on.

Submits nothing except the sign-in form. Costs ~6 budget requests.
Run before going live; review the saved HTML in bot/recon/ and the printed summary.
"""
import re

from bs4 import BeautifulSoup

from . import config
from .client import AisClient
from .store import Store


def run(store: Store) -> None:
    client = AisClient(store)
    print("1. login ...")
    client.login()
    print("   ok")

    print("2. dashboard / schedule_id ...")
    client.discover_schedule_id()
    print(f"   schedule_id = {client.schedule_id}")

    print("3. appointment page ...")
    appt = client.get_appointment_page()
    appt_path = client.save_debug("recon_appointment", appt)
    soup = BeautifulSoup(appt, "html.parser")
    form = client.parse_booking_form(appt)
    print(f"   hidden inputs resolved: { {k: (v[:12] + '…' if len(v) > 12 else v) for k, v in form.items()} }")
    sel = soup.find("select", id=re.compile("facility_id"))
    if sel:
        opts = {o.get("value"): o.get_text(strip=True) for o in sel.find_all("option") if o.get("value")}
        print(f"   facility select options: {opts}")
        known = set(str(v) for v in config.FACILITIES.values())
        missing = known - set(opts)
        if missing:
            print(f"   !! facility ids in config but NOT on page: {sorted(missing)}")
    else:
        print(f"   !! no facility <select> found — inspect {appt_path}")

    print("4. days.json for Calgary (1 request) ...")
    days = client.get_days(config.FACILITIES["Calgary"])
    print(f"   {len(days)} available dates" +
          (f", earliest {days[0]}" if days else " (empty — nothing bookable there)"))
    print("   (note: /payment 302s to /appointment for an unbooked account, so there is\n"
          "    no all-consulates table — the poller round-robins days.json per post.)")

    print(f"\nDone. Budget used today: {store.requests_today()}/{config.DAILY_REQUEST_BUDGET}")
    print("Review the recon/*.html snapshots, then proceed with --once and go-live.")
