"""HTTP layer. Deliberately plain.

  * No browser User-Agent, ever. Default is whatever `requests` sends
    (python-requests/x.y). A custom UA is allowed only if it doesn't look like
    a browser, and the check runs on the prepared request right before send.
  * No retries, no proxies, no challenge solving. If the response looks like
    a Cloudflare challenge or a block (403/429/503, cf-mitigated header,
    "Just a moment" page), we raise BlockedError and the caller stops the run
    and writes a HALT file.
"""
from __future__ import annotations

from datetime import datetime, timezone

import requests

from .parse import extract_next_data

BROWSER_UA_MARKERS = (
    "mozilla", "chrome", "chromium", "safari", "applewebkit", "gecko",
    "firefox", "edg/", "opr/", "msie", "trident",
)
CHALLENGE_MARKERS = (
    "just a moment", "cf-chl", "challenge-platform", "cf_chl_opt",
    "attention required", "cf-turnstile",
)
BLOCK_STATUSES = {403, 429, 503}


class BlockedError(RuntimeError):
    """FanGraphs/Cloudflare appears to be refusing us. Stop everything."""


class FetchError(RuntimeError):
    """A single page failed in an ordinary way (404, missing data block...)."""


def assert_honest_user_agent(ua: str | None) -> None:
    low = (ua or "").lower()
    hits = [m for m in BROWSER_UA_MARKERS if m in low]
    if hits:
        raise ValueError(
            f"Refusing to send browser-like User-Agent {ua!r} (matched {hits}). "
            "Use no override, or an honest descriptive string."
        )


def build_session(user_agent: str | None = None) -> requests.Session:
    s = requests.Session()
    if user_agent is not None:
        assert_honest_user_agent(user_agent)
        s.headers["User-Agent"] = user_agent
    assert_honest_user_agent(s.headers.get("User-Agent"))
    return s


def looks_blocked(status: int, headers, text: str) -> bool:
    if (headers.get("cf-mitigated") or "").lower() == "challenge":
        return True
    if status in BLOCK_STATUSES:
        return True
    head = (text or "")[:20000].lower()
    return any(m in head for m in CHALLENGE_MARKERS)


def fetch_page(session: requests.Session, url: str, timeout: float) -> dict:
    prepared = session.prepare_request(requests.Request("GET", url))
    assert_honest_user_agent(prepared.headers.get("User-Agent"))
    resp = session.send(prepared, timeout=timeout, allow_redirects=True)

    status = resp.status_code
    text = resp.text
    if status != 200 or resp.headers.get("cf-mitigated"):
        if looks_blocked(status, resp.headers, text):
            raise BlockedError(f"{url} -> HTTP {status}, looks like a Cloudflare challenge/block")
        raise FetchError(f"{url} -> HTTP {status}")

    content_type = resp.headers.get("Content-Type", "").lower()
    if "json" in content_type:
        payload, source = resp.json(), "json"
    else:
        payload, source = extract_next_data(text), "next_data"
        if payload is None:
            if looks_blocked(status, resp.headers, text):
                raise BlockedError(f"{url} -> HTTP 200 but body is a challenge page")
            raise FetchError(f"{url} -> no __NEXT_DATA__ block (page structure may have changed)")

    return {
        "url": url,
        "final_url": resp.url,
        "status": status,
        "fetched_at": datetime.now(timezone.utc).isoformat(timespec="seconds"),
        "user_agent_sent": prepared.headers.get("User-Agent"),
        "source": source,
        "payload": payload,
    }
