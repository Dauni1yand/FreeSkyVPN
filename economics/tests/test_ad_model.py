"""Tests for the advertising model.

A model nobody checks is a spreadsheet with extra steps. What is asserted
here is not the output numbers — those move with every assumption — but the
*directions*: that each lever pushes the answer the way it physically
should, and that the model says "this lever does nothing" when that is the
truth rather than quietly returning a plausible figure.
"""

from __future__ import annotations

from dataclasses import replace

from ad_model import (
    Assumptions,
    affordable_gb_per_user,
    affordable_speed_per_user,
    breakeven_ads_per_user,
    breakeven_ecpm,
    evaluate,
    users_per_node,
)

BASE = Assumptions()


# --- directions ----------------------------------------------------------


def test_more_users_needs_more_nodes():
    small = evaluate(replace(BASE, users=10_000))
    large = evaluate(replace(BASE, users=100_000))
    assert large.nodes_needed > small.nodes_needed


def test_fixed_costs_are_what_makes_small_scale_unprofitable():
    """The head server costs the same at 500 users as at 50 000, so an
    ad-funded service is loss-making below some size and profitable above
    it. Finding that size is the whole point of the model."""
    tiny = evaluate(replace(BASE, users=1_000))
    large = evaluate(replace(BASE, users=100_000))
    assert tiny.margin < 0 < large.margin


def test_a_higher_ecpm_earns_more():
    poor = evaluate(replace(BASE, ecpm=50))
    rich = evaluate(replace(BASE, ecpm=250))
    assert rich.revenue > poor.revenue
    assert rich.margin > poor.margin


def test_revenue_does_not_depend_on_traffic():
    """The structural problem with ad funding, asserted so it cannot be
    forgotten: someone streaming 200 GB watches as many ads as someone
    reading news on 2 GB, and costs a hundred times more to serve."""
    light = evaluate(replace(BASE, gb_per_active_user=2))
    heavy = evaluate(replace(BASE, gb_per_active_user=200))
    assert light.revenue == heavy.revenue


def test_split_tunnelling_reduces_the_fleet():
    """Traffic sent direct does not occupy a node's port at peak either, so
    the bypass buys bandwidth and not only gigabytes."""
    none = evaluate(replace(BASE, direct_share=0.0, users=50_000))
    half = evaluate(replace(BASE, direct_share=0.5, users=50_000))
    assert half.nodes_needed < none.nodes_needed
    assert half.margin > none.margin


def test_a_slower_cap_fits_more_users_on_a_node():
    fast = users_per_node(replace(BASE, mbit_per_concurrent_user=10))[0]
    slow = users_per_node(replace(BASE, mbit_per_concurrent_user=1))[0]
    assert slow > fast


# --- which constraint binds ----------------------------------------------


def test_unmetered_nodes_are_bound_by_bandwidth():
    assert users_per_node(replace(BASE, node_traffic_gb=0))[1] == "port bandwidth"


def test_a_small_traffic_allowance_binds_instead():
    metered = replace(BASE, node_traffic_gb=2_000, gb_per_active_user=20)
    assert users_per_node(metered)[1] == "traffic allowance"


def test_a_gigabyte_cap_is_reported_as_useless_when_it_is():
    """The important refusal. On unmetered nodes the fleet is sized by port
    bandwidth, so halving everyone's monthly volume changes the bill by
    nothing — and returning a number anyway would send someone off to build
    a quota system that cannot move the figure it was built to move."""
    assert affordable_gb_per_user(replace(BASE, node_traffic_gb=0)) is None


def test_a_gigabyte_cap_is_computed_when_traffic_is_the_constraint():
    metered = replace(BASE, node_traffic_gb=2_000, gb_per_active_user=20, users=50_000)
    cap = affordable_gb_per_user(metered)
    assert cap is not None and cap > 0


def test_a_speed_cap_is_the_lever_when_bandwidth_binds():
    cap = affordable_speed_per_user(replace(BASE, users=10_000))
    assert cap is not None
    assert cap < BASE.mbit_per_concurrent_user, "the base case is loss-making, so the cap must bite"


def test_no_cap_helps_when_the_shortfall_is_in_fixed_costs():
    """At 500 users the servers are almost free and the head still is not.
    Saying "cap the speed" there would be advice that cannot work."""
    hopeless = replace(BASE, users=500)
    assert affordable_speed_per_user(hopeless) is None


# --- breakeven -----------------------------------------------------------


def test_breakeven_ecpm_actually_breaks_even():
    a = replace(BASE, users=10_000)
    at_breakeven = replace(a, ecpm=breakeven_ecpm(a))
    assert abs(evaluate(at_breakeven).margin) < 1.0


def test_breakeven_ad_count_actually_breaks_even():
    a = replace(BASE, users=10_000)
    at_breakeven = replace(a, ads_per_active_user=breakeven_ads_per_user(a))
    assert abs(evaluate(at_breakeven).margin) < 1.0


def test_inactive_users_cost_nothing_and_earn_nothing():
    engaged = evaluate(replace(BASE, users=10_000, active_share=1.0))
    idle = evaluate(replace(BASE, users=10_000, active_share=0.1))
    assert engaged.nodes_needed > idle.nodes_needed
    assert engaged.revenue > idle.revenue


# --- packages ------------------------------------------------------------


from ad_model import Package, matching_ecpm, revenue_per_hour_served  # noqa: E402

SHORT = Package("15 мин, пропускаемый", minutes=15, views=1, ecpm=40)
HOUR = Package("1 час, полный", minutes=60, views=1, ecpm=120)
DOUBLE = Package("2 часа, два полных", minutes=120, views=2, ecpm=120)


def test_a_cheaper_ad_can_still_earn_more_per_hour_served():
    """The counterintuitive result the packages rest on: a quarter of the
    time against a third of the price is a better trade, and comparing ad
    prices alone gets it backwards."""
    assert revenue_per_hour_served(SHORT) > revenue_per_hour_served(HOUR)


def test_doubling_the_package_does_not_change_the_rate():
    """Two rewarded views for two hours is the hour package twice over —
    which is exactly why granting per view rather than per package is safe."""
    assert revenue_per_hour_served(DOUBLE) == revenue_per_hour_served(HOUR)


def test_the_short_package_has_a_floor_price():
    """Below it the short option subsidises the long one, and offering it
    costs money rather than making it."""
    floor = matching_ecpm(SHORT, HOUR)
    assert 25 < floor < 35  # ~30 ₽

    cheap = Package("too cheap", minutes=15, views=1, ecpm=floor - 10)
    assert revenue_per_hour_served(cheap) < revenue_per_hour_served(HOUR)
