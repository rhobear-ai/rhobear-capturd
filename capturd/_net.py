"""SSRF guard — the single canonical helper shared by the hosted service and the
MCP server.

A submitted URL drives a server-side Playwright render (film.py). Without this
guard a user can point a generation at an internal address — ``169.254.169.254``
(cloud metadata), ``127.0.0.1`` (the service itself), or an RFC 1918 host on the
box's private network — and read whatever the render returns. We resolve the
host at submit time and reject private/internal ranges.

This is the ONE copy of the guard. Both ``service/app/main.py`` (the hosted app)
and ``capturd/mcp/server.py`` (the MCP ``demo.record`` tool) import
:func:`is_private_ip` from here so the two surfaces cannot drift apart — which
is exactly how the MCP copy fell behind the service copy and started letting
IPv6-mapped IPv4 metadata addresses through.
"""
from __future__ import annotations

import ipaddress


def is_private_ip(ip_str: str) -> bool:
    """True when *ip_str* is private/internal in any address family.

    Uses the stdlib ``ipaddress`` classification (the earlier hand-rolled guard
    in the MCP server missed IPv6-mapped IPv4 like ``::ffff:169.254.169.254``,
    which slipped past cloud-metadata protection, and silently treated malformed
    IPv4 strings as public). An IPv6-mapped IPv4 address is unwrapped and
    re-checked as its IPv4 self. A string that doesn't parse as an IP at all is
    treated as PRIVATE — fail closed, since ``getaddrinfo`` should only ever
    hand us real IPs.
    """
    try:
        addr = ipaddress.ip_address(ip_str)
    except ValueError:
        return True   # unparseable ⇒ fail closed
    mapped = getattr(addr, "ipv4_mapped", None)
    if mapped is not None:
        addr = mapped
    return (addr.is_private or addr.is_loopback or addr.is_link_local
            or addr.is_reserved or addr.is_multicast or addr.is_unspecified)
