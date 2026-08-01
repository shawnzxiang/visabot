"""Raw-HTTP client for ais.usvisa-info.com (no browser).

Contract mirrors jeangnc/us-visa-bot (JS, proven through 2026) and
Soroosh-N/us_visa_scheduler's requests-based booking POST:
- CSRF token lives in <meta name="csrf-token">; login sends it as X-CSRF-Token header.
- Session rides on the _yatri_session cookie, which Rails rotates on login/booking GET.
- Booking POST goes to the same /schedule/{id}/appointment URL as the GET.
"""
import logging
import re
import time
from datetime import datetime

import requests
from bs4 import BeautifulSoup

from . import config
from .store import Store

log = logging.getLogger(__name__)

CAPTCHA_MARKERS = ("data-sitekey", "h-captcha-response", "g-recaptcha-response")
# The sign-in form is data-remote, so a rejected login still returns HTTP 200 with a
# JS payload that re-renders the form carrying an error. Never trust the status code.
LOGIN_ERROR_MARKERS = (
    "invalid email or password",
    "your account is locked",
    "account has been locked",
    "you have to confirm your email",
)


class CaptchaDetected(Exception):
    """Login page rendered a captcha widget — needs a human, never bypass."""


class LoginFailed(Exception):
    """The site rejected the credentials. Do NOT retry in a loop — AIS locks accounts."""


class SessionExpired(Exception):
    """Request bounced to the sign-in page; re-login required."""


class MaintenancePage(Exception):
    """502/503 or the site's maintenance interstitial."""


class NeedsRecon(Exception):
    """Page structure didn't match expectations; HTML saved for inspection."""


class AisClient:
    def __init__(self, store: Store):
        self.store = store
        self.session = requests.Session()
        self.session.headers.update({
            "User-Agent": config.USER_AGENT,
            "Accept-Language": "en-US,en;q=0.9",
            "Accept-Encoding": "gzip, deflate, br",
            "Connection": "close",          # mid-2026: server drops keep-alive sockets
            "Cache-Control": "no-store",
        })
        self.schedule_id: str | None = store.kv_get("schedule_id")
        self.logged_in = False

    # --- plumbing ---

    def _request(self, kind: str, method: str, url: str, **kw) -> requests.Response:
        """All server traffic funnels through here; budget is counted BEFORE sending.

        AIS's edge intermittently answers a valid request with 405/502; a single
        retry after a short pause has been enough in practice.
        """
        kw.setdefault("timeout", 40)
        for attempt in (1, 2):
            self.store.record_request(kind)
            resp = self.session.request(method, url, **kw)
            transient = resp.status_code in (405, 502, 503)
            if transient and attempt == 1:
                log.warning("%s: HTTP %s from edge, retrying once", kind, resp.status_code)
                time.sleep(4)
                continue
            if transient:
                raise MaintenancePage(f"{kind}: HTTP {resp.status_code} twice")
            if ("text/html" in resp.headers.get("content-type", "")
                    and "maintenance" in resp.text[:4000].lower()):
                raise MaintenancePage(f"{kind}: maintenance page")
            return resp
        raise MaintenancePage(f"{kind}: unreachable")

    def _check_signed_in(self, resp: requests.Response) -> None:
        if "/users/sign_in" in resp.url:
            self.logged_in = False
            raise SessionExpired(resp.url)

    @staticmethod
    def _csrf_from(html: str) -> str:
        soup = BeautifulSoup(html, "html.parser")
        meta = soup.find("meta", attrs={"name": "csrf-token"})
        if not meta or not meta.get("content"):
            raise NeedsRecon("csrf meta tag missing")
        return meta["content"]

    def save_debug(self, name: str, text: str) -> str:
        config.RECON_DIR.mkdir(parents=True, exist_ok=True)
        path = config.RECON_DIR / f"{datetime.now():%Y%m%d-%H%M%S}_{name}.html"
        path.write_text(text, errors="replace")
        return str(path)

    # --- auth ---

    def login(self) -> None:
        resp = self._request("login_get", "GET", f"{config.BASE}/users/sign_in")
        html = resp.text
        low = html.lower()
        if any(m in low for m in CAPTCHA_MARKERS):
            self.save_debug("login_captcha", html)
            raise CaptchaDetected("captcha widget rendered on sign-in page")
        csrf = self._csrf_from(html)

        resp = self._request(
            "login_post", "POST", f"{config.BASE}/users/sign_in",
            headers={
                "X-CSRF-Token": csrf,
                "Content-Type": "application/x-www-form-urlencoded; charset=UTF-8",
                "Referer": f"{config.BASE}/users/sign_in",
                "Origin": "https://ais.usvisa-info.com",
                "Accept": "*/*;q=0.5, text/javascript, application/javascript",
            },
            data={
                "utf8": "✓",
                "user[email]": config.AIS_EMAIL,
                "user[password]": config.AIS_PASSWORD,
                "policy_confirmed": "1",
                "commit": "Sign In",
            },
        )
        body_low = resp.text.lower()
        if any(m in body_low for m in CAPTCHA_MARKERS):
            self.save_debug("login_post_captcha", resp.text)
            raise CaptchaDetected("captcha challenge on sign-in POST")
        for marker in LOGIN_ERROR_MARKERS:
            if marker in body_low:
                self.save_debug("login_rejected", resp.text)
                raise LoginFailed(f"site said: {marker!r}")
        if "sign_in_form" in body_low and "error" in body_low:
            path = self.save_debug("login_rejected_unknown", resp.text)
            raise LoginFailed(f"sign-in form re-rendered with an error; saved {path}")
        self.logged_in = True
        log.info("login POST accepted (HTTP %s)", resp.status_code)

    # Post-login landing page differs by account state; try in order until one
    # yields a /schedule/<id>/ link.
    DASHBOARD_PATHS = ("/account", "/groups", "", "/schedule")

    def discover_schedule_id(self) -> str:
        """Parse /schedule/{id}/ out of the post-login dashboard. Also confirms login."""
        tried = []
        for path in self.DASHBOARD_PATHS:
            try:
                resp = self._request("account", "GET", f"{config.BASE}{path}",
                                     allow_redirects=True)
            except MaintenancePage as exc:
                tried.append(f"{path or '/'} -> {exc}")
                continue
            self._check_signed_in(resp)
            m = re.search(r"/schedule/(\d+)", resp.text)
            if m:
                self.schedule_id = m.group(1)
                self.store.kv_set("schedule_id", self.schedule_id)
                log.info("schedule_id = %s (found via %s)", self.schedule_id, path or "/")
                return self.schedule_id
            tried.append(f"{path or '/'} -> HTTP {resp.status_code}, no schedule link")
            self.save_debug(f"dashboard{path.replace('/', '_')}", resp.text)
        raise NeedsRecon("no /schedule/<id> link on any dashboard path: " + "; ".join(tried))

    def ensure_session(self) -> None:
        if not self.logged_in:
            self.login()
        if not self.schedule_id:
            self.discover_schedule_id()

    # --- reads ---

    def get_payment_page(self) -> str:
        resp = self._request(
            "payment", "GET",
            f"{config.BASE}/schedule/{self.schedule_id}/payment",
            allow_redirects=True,
        )
        self._check_signed_in(resp)
        return resp.text

    def _json_headers(self) -> dict:
        return {
            "Accept": "application/json, text/javascript, */*; q=0.01",
            "X-Requested-With": "XMLHttpRequest",
            "Referer": f"{config.BASE}/schedule/{self.schedule_id}/appointment",
            "Sec-Fetch-Site": "same-origin",
            "Sec-Fetch-Mode": "cors",
            "Sec-Fetch-Dest": "empty",
        }

    def _json(self, kind: str, url: str):
        resp = self._request(kind, "GET", url, headers=self._json_headers())
        if resp.status_code == 401:
            self.logged_in = False
            raise SessionExpired(f"{kind}: 401")
        self._check_signed_in(resp)
        try:
            return resp.json()
        except ValueError as exc:
            path = self.save_debug(f"{kind}_not_json", resp.text)
            raise NeedsRecon(f"{kind} returned non-JSON; saved {path}") from exc

    def get_days(self, facility_id: int) -> list[str]:
        data = self._json(
            "days",
            f"{config.BASE}/schedule/{self.schedule_id}/appointment/days/"
            f"{facility_id}.json?appointments[expedite]=false",
        )
        return [item["date"] for item in data]

    def get_times(self, facility_id: int, date_str: str) -> list[str]:
        data = self._json(
            "times",
            f"{config.BASE}/schedule/{self.schedule_id}/appointment/times/"
            f"{facility_id}.json?date={date_str}&appointments[expedite]=false",
        )
        return data.get("business_times") or data.get("available_times") or []

    def get_account_page(self) -> str:
        """Dashboard re-read used by the verifier (counted separately)."""
        resp = self._request("verify", "GET", f"{config.BASE}/account", allow_redirects=True)
        self._check_signed_in(resp)
        return resp.text

    def get_appointment_page(self) -> str:
        resp = self._request(
            "appt_get", "GET",
            f"{config.BASE}/schedule/{self.schedule_id}/appointment",
            allow_redirects=True,
        )
        self._check_signed_in(resp)
        return resp.text

    # --- booking ---

    @staticmethod
    def parse_booking_form(html: str) -> dict:
        """Hidden inputs from the appointment page; defaults match jeangnc's hardcodes."""
        soup = BeautifulSoup(html, "html.parser")

        def hidden(name: str, default: str) -> str:
            el = soup.find("input", attrs={"name": name})
            return el.get("value", default) if el else default

        csrf = AisClient._csrf_from(html)
        return {
            "utf8": hidden("utf8", "✓"),
            "authenticity_token": hidden("authenticity_token", csrf),
            "confirmed_limit_message": hidden("confirmed_limit_message", "1"),
            "use_consulate_appointment_capacity": hidden("use_consulate_appointment_capacity", "true"),
        }

    def post_booking(self, facility_id: int, date_str: str, time_str: str,
                     form: dict) -> requests.Response:
        url = f"{config.BASE}/schedule/{self.schedule_id}/appointment"
        data = {
            **form,
            "appointments[consulate_appointment][facility_id]": str(facility_id),
            "appointments[consulate_appointment][date]": date_str,
            "appointments[consulate_appointment][time]": time_str,
            "appointments[asc_appointment][facility_id]": "",
            "appointments[asc_appointment][date]": "",
            "appointments[asc_appointment][time]": "",
        }
        resp = self._request(
            "book_post", "POST", url,
            headers={
                "Content-Type": "application/x-www-form-urlencoded",
                "Referer": url,
                "Origin": "https://ais.usvisa-info.com",
                "Accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8",
            },
            data=data,
            allow_redirects=True,
        )
        return resp
