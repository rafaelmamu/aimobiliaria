"""DNS resolution + aiohttp session for BR hosts whose DNS fails through
Docker's internal resolver (e.g. www.upsideimoveis.com.br).

Uses dnspython (pure-Python, no pycares C-ABI landmines) against explicit
external nameservers — Quad9 first because Cloudflare/Google return
SERVFAIL for the Upside domain's authoritative setup.
"""
import logging
import socket

import aiohttp
from aiohttp.abc import AbstractResolver
import dns.asyncresolver
import dns.resolver

logger = logging.getLogger(__name__)

# Order matters: Quad9 first (Cloudflare/Google SERVFAIL on upsideimoveis.com.br).
BR_NAMESERVERS: list[str] = ["9.9.9.9", "149.112.112.112", "1.1.1.1", "8.8.8.8"]


class BRDNSResolver(AbstractResolver):
    """aiohttp-compatible resolver using dnspython with explicit nameservers.

    Walks the nameserver list and falls through on SERVFAIL/timeout so at
    least one answers. Forces IPv4 (AF_INET) because many BR hosts have
    no AAAA record and the dual-stack lookup hangs otherwise.
    """

    def __init__(self, nameservers: list[str] | None = None):
        self._nameservers = list(nameservers or BR_NAMESERVERS)

    async def resolve(
        self, host: str, port: int = 0, family: int = socket.AF_INET
    ) -> list[dict]:
        last_exc: Exception | None = None
        for ns in self._nameservers:
            r = dns.asyncresolver.Resolver(configure=False)
            r.nameservers = [ns]
            r.timeout = 3.0
            r.lifetime = 4.0
            try:
                answer = await r.resolve(host, "A")
                addrs = [str(record.address) for record in answer]
                logger.info(f"DNS {ns} resolved {host} -> {addrs}")
                return [
                    {
                        "hostname": host,
                        "host": addr,
                        "port": port,
                        "family": socket.AF_INET,
                        "proto": 0,
                        "flags": 0,
                    }
                    for addr in addrs
                ]
            except Exception as e:
                logger.warning(
                    f"DNS {ns} failed for {host}: {type(e).__name__}: {e}"
                )
                last_exc = e
        raise last_exc or RuntimeError(f"All nameservers failed for {host}")

    async def close(self) -> None:
        return None


def make_br_session(
    timeout_seconds: float = 30.0,
    headers: dict[str, str] | None = None,
) -> aiohttp.ClientSession:
    """aiohttp session that resolves via external DNS (see BRDNSResolver).

    Use for outbound requests to Brazilian hosts that Docker's internal
    resolver cannot reach reliably.
    """
    connector = aiohttp.TCPConnector(
        resolver=BRDNSResolver(),
        family=socket.AF_INET,
        ttl_dns_cache=300,
    )
    return aiohttp.ClientSession(
        connector=connector,
        headers=headers or {},
        timeout=aiohttp.ClientTimeout(total=timeout_seconds),
    )
