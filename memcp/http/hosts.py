"""Host and Origin checks: the MCP SDK's DNS-rebinding protection, set from MEMCP_ALLOWED_HOSTS."""

from __future__ import annotations

from mcp.server.transport_security import TransportSecuritySettings

from memcp.config import Config


def transport_security(config: Config) -> TransportSecuritySettings | None:
    """Explicit Host and Origin allow-lists, or None to keep the SDK's defaults.

    With MEMCP_ALLOWED_HOSTS set we hand the SDK explicit settings, because its own
    rule turns protection on only for a loopback `host` and leaves it off for every
    other value — including `0.0.0.0`, which is memcp's default. Origins are derived
    from the same list over http and https; a browser Origin memcp does not
    recognise is refused rather than served.
    """
    allowed = config.allowed_hosts_list
    if not allowed:
        return None
    return TransportSecuritySettings(
        enable_dns_rebinding_protection=True,
        allowed_hosts=allowed,
        allowed_origins=[f"{scheme}://{h}" for h in allowed for scheme in ("http", "https")],
    )
