"""Service class as a property of an inbound, not of a node.

Every node serves everyone. What separates users is which inbound they sit
on, because an inbound's port is the one thing about a user's traffic that
Linux `tc` on the node can see and act on — Xray offers no per-user
bandwidth control of its own (measured, not assumed: the `speedLimit`
policy field is silently ignored, and `sendThrough` has no effect either).

There are two classes and they mean:

    full    the user watched a rewarded ad and has paid for this hour with
            their attention. Served first when the link is contended.
    grace   the ad network could not deliver one, and we let them online
            anyway rather than showing a working service that refuses to
            work (app/services/access.py). Deliberately the lower class:
            the fallback must not be as good as the thing it stands in for,
            or it becomes the way to skip the ad.

These were `paid` and `free` when the plan was a subscription. The port
sets and the `tc` filters are unchanged — only what decides who lands in
which class — so renaming them costs no node re-provisioning.

`tc` is configured once at provisioning with two priority classes keyed on
exactly these ports. Nothing has to run on the node when inbounds rotate,
because the port sets never change — only which port inside a set is in
use.

The port lists below and the `tc` filters on the node must agree, or the
priority silently does nothing. They are not duplicated: provisioning
passes these exact values to the bootstrap script, so this module is the
single source of truth.
"""

from __future__ import annotations

import enum


class Tier(str, enum.Enum):
    """Which `tc` priority class an inbound's port lands in."""

    grace = "grace"
    full = "full"


# All of these are ordinary HTTPS ports, so a Reality listener on any of
# them is unremarkable; the split between classes carries no meaning to an
# outside observer.
TIER_PORTS: dict[Tier, tuple[int, ...]] = {
    Tier.full: (443, 2053, 2087),
    Tier.grace: (8443, 2083, 2096),
}

# Used once a tier's preferred ports are all taken on a node. Disjoint so a
# fallback port still lands in the right `tc` class.
TIER_FALLBACK_RANGE: dict[Tier, tuple[int, int]] = {
    Tier.full: (20000, 39999),
    Tier.grace: (40000, 59999),
}


def ports_for(tier: Tier) -> tuple[int, ...]:
    return TIER_PORTS[tier]


def fallback_range_for(tier: Tier) -> tuple[int, int]:
    return TIER_FALLBACK_RANGE[tier]


def tier_of_port(port: int) -> Tier | None:
    """Which class a port belongs to, for checking a node's shaping matches."""
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
    # The bootstrap script's argument names still say paid/free. They are
    # positional on the wire and describe the high- and low-priority sets,
    # which is exactly what full and grace are — renaming them on the node
    # would mean re-provisioning the fleet to change two words.
    return {
        "paid_ports": ",".join(str(p) for p in TIER_PORTS[Tier.full]),
        "free_ports": ",".join(str(p) for p in TIER_PORTS[Tier.grace]),
        "paid_range": "{}-{}".format(*TIER_FALLBACK_RANGE[Tier.full]),
        "free_range": "{}-{}".format(*TIER_FALLBACK_RANGE[Tier.grace]),
    }


def all_ports() -> tuple[int, ...]:
    """Every preferred port, both classes together.

    Used to report which of them a node already has taken. The fallback
    ranges are deliberately not included: they are twenty thousand ports
    wide, so naming the handful that happen to be busy would say nothing.
    """
    return tuple(port for ports in TIER_PORTS.values() for port in ports)
