"""Safe picture links for catalog rows.

Pictures belong to Scenario Manager (SM). The server never fetches them and never talks to SM.
It only stores a URL string that the userscript sends and passes it to the page, so every value
from a row goes through clean_image_ref() first.
"""
from urllib.parse import urlsplit

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
