"""Remote-endpoint consent (docs/ARCHITECTURE.md -> Privacy and remote endpoints).

Source code leaves the machine only after an explicit yes: `--yes`,
`llm.acknowledge_remote = true`, or an interactive y/N answer. With no TTY and no
acknowledgement the run stops before any provider call.
"""

from __future__ import annotations

import ipaddress
from collections.abc import Callable
from urllib.parse import urlsplit

from codewhy.llm.provider import Provider

FIX_HINT = "pass --yes or set llm.acknowledge_remote = true in codewhy.toml"


class ConsentError(Exception):
    """Sending source code to a remote endpoint was not allowed."""


class ConsentRequired(ConsentError):
    """Remote endpoint, no acknowledgement, and no terminal to ask on."""


class ConsentDeclined(ConsentError):
    """The user answered no at the prompt."""


def loopback(host: str) -> bool:
    """True for localhost and loopback IPs (127.0.0.0/8, ::1), with or without IPv6 brackets."""
    name = host.strip().lower()
    if name.startswith("[") and name.endswith("]"):
        name = name[1:-1]
    if name == "localhost":
        return True
    try:
        return ipaddress.ip_address(name).is_loopback
    except ValueError:
        return False


def url_host(url: str) -> str:
    """Hostname of `url` (brackets stripped); a bare `host:port` without a scheme is accepted."""
    parts = urlsplit(url if "://" in url else f"http://{url}")
    return parts.hostname or ""


def ensure_remote_ack(
    provider: Provider,
    *,
    assume_yes: bool,
    acknowledged_in_config: bool,
    is_tty: bool,
    ask: Callable[[str], bool],
    tell: Callable[[str], None],
) -> None:
    """Return when sending is allowed; raise ConsentRequired / ConsentDeclined otherwise.

    `tell` prints the endpoint notice and `ask` asks the y/N question; the CLI owns
    both, so this module never writes to the terminal itself.
    """
    if not provider.is_remote or assume_yes or acknowledged_in_config:
        return
    notice = f"Your source code will be sent to {provider.endpoint_label}."
    if not is_tty:
        raise ConsentRequired(f"{notice} No terminal is available to confirm this; {FIX_HINT} to allow it.")
    tell(notice)
    if not ask("Continue? [y/N]"):
        raise ConsentDeclined(f"Not sending source code to {provider.endpoint_label}; nothing was sent.")
