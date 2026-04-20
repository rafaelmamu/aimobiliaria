"""Force IPv4 resolution for specific external APIs.

Some Docker networks (e.g. Coolify's default overlay) run a dual-stack
embedded resolver that hangs on AAAA queries for hosts with no IPv6
record — returning EAI_AGAIN (`[Errno -3] Temporary failure in name
resolution`) even when the A record is perfectly resolvable.

This module monkey-patches `socket.getaddrinfo` to force `AF_INET` for a
short allow-list of known-problematic hostnames. asyncio's default
resolver delegates to this function in a thread executor, so httpx,
aiohttp, anyio, and plain `socket` calls all pick up the override.
"""
import logging
import socket

logger = logging.getLogger(__name__)

# Hosts known to have no AAAA records that cause dual-stack resolver hangs
# inside Coolify's network. Substring match (case-insensitive).
IPV4_ONLY_HOSTS: tuple[str, ...] = ("upsideimoveis.com.br",)

_original_getaddrinfo = socket.getaddrinfo
_installed = False


def _patched_getaddrinfo(host, port, family=0, *args, **kwargs):
    if isinstance(host, str):
        lowered = host.lower()
        if any(h in lowered for h in IPV4_ONLY_HOSTS):
            family = socket.AF_INET
    return _original_getaddrinfo(host, port, family, *args, **kwargs)


def install_ipv4_only_override() -> None:
    """Install the socket.getaddrinfo monkey-patch. Safe to call multiple times."""
    global _installed
    if _installed:
        return
    socket.getaddrinfo = _patched_getaddrinfo
    _installed = True
    logger.info(
        f"IPv4-only DNS override installed for hosts: {IPV4_ONLY_HOSTS}"
    )
