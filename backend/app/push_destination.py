"""Only reviewed Web Push provider origins may receive server-side requests."""

import re
from urllib.parse import urlsplit

APPROVED_PUSH_HOSTS = frozenset({
    "updates.push.services.mozilla.com",  # Firefox / Mozilla Autopush
    "fcm.googleapis.com",  # Google FCM / Chromium
})


def _is_apple_push_host(host: str | None) -> bool:
    """Apple documents Web Push endpoints on subdomains of push.apple.com."""
    return bool(
        host and len(host) <= 253 and host.endswith(".push.apple.com")
        and all(re.fullmatch(r"[a-z0-9](?:[a-z0-9-]{0,61}[a-z0-9])?", label)
                for label in host.split("."))
    )


def validate_push_destination(endpoint: str) -> str:
    """Reject ambiguous URLs; preserve opaque subscription paths and tokens."""
    error = "Push endpoint must be an HTTPS URL on an approved Web Push provider."
    if (not endpoint or any(ord(char) <= 32 or ord(char) >= 127 for char in endpoint)
            or "\\" in endpoint or "#" in endpoint
            or re.search(r"%(?![0-9a-fA-F]{2})", endpoint)):
        raise ValueError(error)
    try:
        parts = urlsplit(endpoint)
        host = parts.hostname
        if (parts.scheme != "https"
                or not (host in APPROVED_PUSH_HOSTS or _is_apple_push_host(host))
                or parts.username is not None or parts.password is not None
                or parts.port not in (None, 443)
                or parts.netloc.lower() not in (host, host + ":443")
                or not parts.path or parts.path == "/"):
            raise ValueError(error)
    except ValueError:
        raise ValueError(error) from None
    return endpoint
