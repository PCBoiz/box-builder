"""Safe picture links for catalog rows.

Pictures belong to Scenario Manager (SM). The server never fetches them and never talks to SM.
It only stores a URL string that the userscript sends and passes it to the page, so every value
from a row goes through clean_image_ref() first.
"""
import re
import time
from datetime import datetime, timezone
from urllib.parse import parse_qs, urlsplit

IMAGE_BASE = "https://sm.config.inc"
# SM serves object pictures from this S3 bucket as signed links (read from SM's /api/objects, field image_url).
DEFAULT_HOSTS = ("sm.config.inc", "configint-main.s3.amazonaws.com")

MAX_LEN = 2000  # a signed S3 link carries a long query string
MAX_DEPTH = 4  # how deep pick_image looks into dicts and lists
# SM's real field name is not known yet, so try these in order.
IMAGE_KEYS = (
    "image_url", "imageUrl", "image", "thumbnail_url", "thumbnailUrl", "thumbnail", "thumb",
    "photo_url", "photo", "picture", "img", "img_url", "images", "files", "attachments",
)
# Keys that can hold the link when a value is a dict.
URL_KEYS = ("url", "src", "path", "href", "image_url")
# Backslash is a path trick. Quotes and angle brackets could break out of an HTML attribute.
BAD_CHARS = frozenset("\\\"'<>`")


def _host_allowed(host: str, hosts) -> bool:
    """True if host is an allowed host or a subdomain of one (case-insensitive)."""
    host = host.lower()
    allowed = [h.strip().lower() for h in hosts if h and h.strip()]
    return any(host == h or host.endswith("." + h) for h in allowed)


def clean_image_ref(value, base: str = IMAGE_BASE, hosts=DEFAULT_HOSTS) -> str | None:
    """Return a safe absolute https URL for value, or None.

    Accepts an https URL on an allowed host, or a path starting with one "/" (joined to base).
    """
    if not isinstance(value, str):
        return None
    url = value.strip()
    if not url or len(url) > MAX_LEN:
        return None
    # No spaces, control or invisible characters, and none of the BAD_CHARS.
    if any(c.isspace() or not c.isprintable() or c in BAD_CHARS for c in url):
        return None
    if url.startswith("/"):
        # "//host/x" is protocol-relative and would leave our site.
        return None if url.startswith("//") else base.rstrip("/") + url
    try:
        parts = urlsplit(url)
        port = parts.port
    except ValueError:  # bad port or broken [ipv6] host
        return None
    host = parts.hostname  # parsed and lowercased, so "sm.config.inc@evil.com" gives evil.com
    if parts.scheme != "https" or not host or "@" in parts.netloc or port not in (None, 443):
        return None
    return url if _host_allowed(host, hosts) else None


def _first_clean(value, base: str, hosts, depth: int = 0) -> str | None:
    """Find the first usable link in a string, a dict with a URL key, or a list of those."""
    if depth > MAX_DEPTH:
        return None
    if isinstance(value, str):
        return clean_image_ref(value, base, hosts)
    if isinstance(value, dict):
        items = [value.get(k) for k in URL_KEYS]
    elif isinstance(value, list):
        items = value
    else:
        return None
    for item in items:
        found = _first_clean(item, base, hosts, depth + 1)
        if found:
            return found
    return None


def pick_image(row: dict, base: str = IMAGE_BASE, hosts=DEFAULT_HOSTS) -> str | None:
    """Return the first safe picture link in a catalog row, looking at IMAGE_KEYS in order."""
    if not isinstance(row, dict):
        return None
    for key in IMAGE_KEYS:
        found = _first_clean(row.get(key), base, hosts)
        if found:
            return found
    return None


def hosts_from_env(value: str | None) -> tuple[str, ...]:
    """Parse a comma-separated IMAGE_HOSTS value. Empty or missing gives DEFAULT_HOSTS."""
    names = [h.strip().lower() for h in (value or "").split(",")]
    return tuple(dict.fromkeys(h for h in names if h)) or DEFAULT_HOSTS


def detail_url(object_id: str, base: str = IMAGE_BASE) -> str:
    """Link to an object's SM page. The id keeps only its digits, padded to 6."""
    digits = "".join(c for c in str(object_id) if c in "0123456789")
    return f"{base.rstrip('/')}/object_detail/{digits.zfill(6)}"


# ----------------------------------------------------------------------------- when a signed link stops working
# SM's pictures are signed links that last about an hour. The link says when: "Expires=<epoch seconds>" (older signing) or
# "X-Amz-Date=<yyyymmddThhmmssZ>" plus "X-Amz-Expires=<seconds>" (newer signing). A picture asked for in the last minute may fail
# half way, so the time given is a little early.
EXPIRY_MARGIN_S = 60
_SIGNED_KEYS = ("signature", "x-amz-signature", "awsaccesskeyid", "x-amz-credential")


def _query(url: str) -> dict[str, str]:
    try:
        raw = parse_qs(urlsplit(url).query, keep_blank_values=False)
    except ValueError:
        return {}
    return {k.lower(): v[0] for k, v in raw.items() if v}


def is_signed(url) -> bool:
    """True when the link carries a signature, so it will stop working at some time."""
    return isinstance(url, str) and any(k in _query(url) for k in _SIGNED_KEYS)


_DIGITS = re.compile(r"[0-9]+")           # ASCII only: str.isdigit() also accepts characters such as the superscript two, which int() refuses


def link_expiry(url) -> float | None:
    """When the link stops working (epoch seconds), read from the link itself; None when it says nothing readable.
    A strange link means "no expiry known", never an error: one odd picture must not break the object list."""
    try:
        return _link_expiry(url)
    except (ValueError, OverflowError, TypeError):
        return None


def _link_expiry(url) -> float | None:
    if not isinstance(url, str) or not url:
        return None
    q = _query(url)
    found: list[float] = []
    exp = q.get("expires", "")
    if _DIGITS.fullmatch(exp) and 10**9 <= int(exp) < 4 * 10**9:             # an epoch in 2001..2096, not a count of seconds
        found.append(float(exp))
    date, span = q.get("x-amz-date", ""), q.get("x-amz-expires", "")
    if _DIGITS.fullmatch(span) and 0 < int(span) <= 7 * 86400:
        try:
            found.append(datetime.strptime(date, "%Y%m%dT%H%M%SZ").replace(tzinfo=timezone.utc).timestamp() + int(span))
        except ValueError:
            pass
    return min(found) if found else None


def expiry_for(url, synced_at: float | None, ttl_s: float) -> float | None:
    """The moment to stop asking for this picture (epoch seconds), or None for a link that never expires.
    The link's own expiry wins. A signed link that does not say falls back to the time the catalog was read plus the usual life
    (IMAGE_TTL_MINUTES): without a read time nothing can be said, so the link is kept."""
    exp = link_expiry(url)
    if exp is None and is_signed(url) and synced_at:
        exp = synced_at + ttl_s
    return None if exp is None else exp - EXPIRY_MARGIN_S


def is_expired(exp, now: float | None = None) -> bool:
    """exp is what expiry_for gave (or anything else a caller passes along): only a real number in the past counts."""
    return isinstance(exp, (int, float)) and not isinstance(exp, bool) and (time.time() if now is None else now) >= exp
