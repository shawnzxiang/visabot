"""Read-only reconnaissance walk: login and snapshot every page the bot relies on.

Submits nothing except the sign-in form. Costs ~6 budget requests.
Run before going live; review the saved HTML in bot/recon/ and the printed summary.
"""
import re
import shutil

from bs4 import BeautifulSoup

from . import config, detector
from .client import AisClient
from .store import Store

FIXTURES = config.BOT_DIR / "tests" / "fixtures"


def run(store: Store) -> None:
    client = AisClient(store)
    print("1. login ...")
    client.login()
    print("   ok")

    print("2. dashboard / schedule_id ...")
    html = client.get_account_page()
    dash_path = client.save_debug("recon_dashboard", html)
    m = re.search(r"/schedule/(\d+)/", html)
    if not m:
        print(f"   !! no /schedule/<id>/ link found — inspect {dash_path}")
        return
    client.schedule_id = m.group(1)
    store.kv_set("schedule_id", client.schedule_id)
    print(f"   schedule_id = {client.schedule_id}  (saved {dash_path})")

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

    print("4. payment page (the poll target) ...")
    pay = client.get_payment_page()
    pay_path = client.save_debug("recon_payment", pay)
    FIXTURES.mkdir(parents=True, exist_ok=True)
    shutil.copy(pay_path, FIXTURES / "payment_page_real.html")
    avail = detector.parse_payment_page(pay)
    if avail:
        print("   parsed availability:")
        for post, d in sorted(avail.items()):
            print(f"     {post:<12} {d or 'no appointments'}")
        print(f"   (copied to {FIXTURES/'payment_page_real.html'} for the test suite)")
    else:
        print(f"   !! parser found NO posts — inspect {pay_path} and fix detector.py")

    print("5. days.json for Calgary (1 request) ...")
    days = client.get_days(config.FACILITIES["Calgary"])
    print(f"   {len(days)} available dates" + (f", earliest {days[0]}" if days else " (empty — expected while nothing is bookable)"))

    print(f"\nDone. Budget used today: {store.requests_today()}/{config.DAILY_REQUEST_BUDGET}")
    print("Review the recon/*.html snapshots, then proceed with --once and go-live.")
