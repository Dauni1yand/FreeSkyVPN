"""Free/paid as a property of an inbound, not of a node.

Every node serves both audiences. What separates them is which inbound a
user sits on, because an inbound's port is the one thing about a user's
traffic that Linux `tc` on the node can see and act on — Xray offers no
per-user bandwidth control of its own (measured, not assumed: the
`speedLimit` policy field is silently ignored, and `sendThrough` has no
effect either).

So each tier owns a fixed set of ports. `tc` is configured once at
provisioning with two priority classes keyed on exactly these ports: paid
traffic is served first when the link is contended, free traffic uses
whatever is left and can still burst to the full link when nothing else
wants it. Nothing has to run on the node when inbounds rotate, because the
port sets never change — only which port inside a set is currently in use.

The port lists below and the `tc` filters on the node must agree, or the
priority silently does nothing. They are not duplicated: provisioning
passes these exact values to the bootstrap script, so this module is the
single source of truth.
"""

from __future__ import annotations

import enum


class Tier(str, enum.Enum):
    free = "free"
    paid = "paid"


# All of these are ordinary HTTPS ports, so a Reality listener on any of
# them is unremarkable; the split between tiers carries no meaning to an
# outside observer.
TIER_PORTS: dict[Tier, tuple[int, ...]] = {
    Tier.paid: (443, 2053, 2087),
    Tier.free: (8443, 2083, 2096),
}

# Used once a tier's preferred ports are all taken on a node. Disjoint so a
# fallback port still lands in the right `tc` class.
TIER_FALLBACK_RANGE: dict[Tier, tuple[int, int]] = {
    Tier.paid: (20000, 39999),
    Tier.free: (40000, 59999),
}


def ports_for(tier: Tier) -> tuple[int, ...]:
    return TIER_PORTS[tier]


def fallback_range_for(tier: Tier) -> tuple[int, int]:
    return TIER_FALLBACK_RANGE[tier]


def tier_of_port(port: int) -> Tier | None:
    """Which tier a port belongs to, for checking a node's shaping matches."""
    for tier, ports in TIER_PORTS.items():
        if port in ports:
            return tier
    for tier, (low, high) in TIER_FALLBACK_RANGE.items():
        if low <= port <= high:
            return tier
    return None


def bootstrap_arguments() -> dict[str, str]:
    """The port sets, formatted for bootstrap_node.sh.

    Passed rather than hardcoded on the node so the shaping filters and the
    ports the head hands out cannot drift apart.
    """
    return {
        "paid_ports": ",".join(str(p) for p in TIER_PORTS[Tier.paid]),
        "free_ports": ",".join(str(p) for p in TIER_PORTS[Tier.free]),
        "paid_range": "{}-{}".format(*TIER_FALLBACK_RANGE[Tier.paid]),
        "free_range": "{}-{}".format(*TIER_FALLBACK_RANGE[Tier.free]),
    }
