"""SSRF guard — the shared canonical predicate the MCP server now uses.

The hosted app and the ``demo.record`` MCP tool both drive a server-side
Playwright render from a user-supplied URL, so the predicate that classifies a
resolved IP as private/internal is a load-bearing security boundary. It must
BLOCK cloud-metadata, loopback, RFC 1918, link-local, and IPv6-mapped-IPv4
addresses, and it must fail CLOSED (treat as private) on anything it cannot
parse. These tests pin that behaviour against the single helper in
``capturd._net`` so the MCP copy cannot silently regress to a hand-rolled
version.
"""
import pytest

from capturd._net import is_private_ip


# Every address here MUST be treated as private/internal (BLOCKED). Includes
# the headline cloud-metadata bypass (IPv6-mapped IPv4) that the old
# hand-rolled guard let through.
@pytest.mark.parametrize(
    "ip",
    [
        # IPv6-mapped IPv4 — cloud metadata / loopback / RFC1918. The old guard
        # routed any ":"-containing string down a weak branch that returned
        # False (allow), so ::ffff:169.254.169.254 slipped straight through.
        "::ffff:169.254.169.254",
        "::ffff:127.0.0.1",
        "::ffff:10.0.0.1",
        # IPv4 cloud metadata / link-local.
        "169.254.169.254",
        # IPv4 loopback + RFC 1918 (incl. both ends of the 172.16/12 range).
        "127.0.0.1",
        "10.0.0.1",
        "172.16.0.1",
        "172.31.255.255",
        "192.168.1.1",
        # IPv6 loopback, unique-local, link-local.
        "::1",
        "fc00::1",
        "fd12::1",
        "fe80::1",
        # Malformed input must fail CLOSED (treated as private), never allow.
        "999.999.999.999",
        "not-an-ip",
        "",
    ],
)
def test_is_private_ip_blocks_internal_and_malformed(ip):
    assert is_private_ip(ip) is True


@pytest.mark.parametrize("ip", ["8.8.8.8", "1.1.1.1"])
def test_is_private_ip_allows_public(ip):
    assert is_private_ip(ip) is False


def test_mcp_server_delegates_to_shared_guard():
    """The MCP server must not keep a hand-rolled ``_is_private_ip`` copy.

    A local re-implementation is exactly how this bug class regressed before —
    the MCP copy drifted behind the service copy and started letting
    IPv6-mapped IPv4 metadata addresses through. Asserting the attribute is
    gone keeps future agents from silently reintroducing it.
    """
    import capturd.mcp.server

    assert not hasattr(capturd.mcp.server, "_is_private_ip")
    # And the shared predicate is what _reject_private_url now calls.
    assert capturd.mcp.server.is_private_ip is capturd._net.is_private_ip
