"""Does advertising pay for the servers?

Written because the answer is not obvious and the failure mode is expensive:
an ad-funded VPN can look healthy at a thousand users and lose money at ten
thousand, because the two sides of the ledger scale on different things.

    Cost scales with traffic.       Revenue scales with attention.

Those are uncorrelated. Somebody streaming 200 GB a month watches about as
many ads as somebody reading news on 2 GB, and pays for a hundred times more
bandwidth. With a paid tier that is fine — heavy users convert. Without one
there is nothing for them to convert *to*, so the only thing standing
between the service and a loss is a cap.

This model exists to find where that cap has to be. Every number in
`Assumptions` is a guess until it is measured; the point is not the default
values but the sensitivity around them, which `breakeven_*` reports.

Run it:  python3 economics/ad_model.py
"""

from __future__ import annotations

import argparse
from dataclasses import dataclass, replace


@dataclass(frozen=True)
class Assumptions:
    """Everything the answer depends on. All money in roubles per month."""

    # --- audience ---
    users: int = 10_000
    # Share of registered users who actually connect in a given month. The
    # rest cost nothing and earn nothing, so they only dilute per-user
    # averages — which is why costs below are computed on active users.
    active_share: float = 0.45

    # --- traffic ---
    # Gigabytes an active user pulls per month, *through the tunnel*.
    gb_per_active_user: float = 12.0
    # Share of a Russian user's traffic that the split tunnel sends direct
    # and therefore never touches a node. This is the single biggest lever
    # the product design already pulled: VK, Yandex, marketplaces, banks and
    # every .ru site bypass the VPN entirely.
    direct_share: float = 0.45

    # --- nodes ---
    node_monthly_cost: float = 700.0
    # Traffic included with a node, in GB. 0 means unmetered.
    node_traffic_gb: float = 0.0
    node_port_mbit: float = 1000.0
    # Share of a node's users online at the same time, at peak.
    peak_concurrency: float = 0.10
    # Mbit/s a connected user consumes at peak, averaged across everyone
    # online. Far below any speed cap — most of a session is idle.
    #
    # This is the user's *total* rate. The share of it that reaches a node
    # is reduced by `direct_share` below, because traffic the split tunnel
    # sends direct does not occupy the node's port either: the bypass saves
    # bandwidth at the moment of contention, not only gigabytes at the end
    # of the month.
    mbit_per_concurrent_user: float = 3.0
    # Never plan to fill a link completely — queueing delay climbs long
    # before saturation, and a node at 100% is a node that feels broken.
    node_utilisation_ceiling: float = 0.70

    # --- fixed ---
    head_monthly_cost: float = 2_500.0
    misc_monthly_cost: float = 1_000.0

    # --- advertising ---
    # Roubles per 1000 completed rewarded views. The number to verify first:
    # everything here moves linearly with it.
    ecpm: float = 120.0
    # Share of ad requests that get filled. Rarely 100%, and in a market
    # several networks have left, materially less.
    fill_rate: float = 0.70
    # Completed rewarded views per active user per month. The product lever:
    # it is set by how much a boost is worth and how long it lasts.
    ads_per_active_user: float = 12.0


@dataclass(frozen=True)
class Result:
    active_users: int
    tunnelled_gb_total: float
    users_per_node: int
    nodes_needed: int
    node_cost: float
    fixed_cost: float
    total_cost: float
    revenue: float
    margin: float
    cost_per_active_user: float
    revenue_per_active_user: float
    binding_constraint: str


def users_per_node(a: Assumptions) -> tuple[int, str]:
    """How many *active* users one node holds, and what stops it holding more.

    Two independent ceilings; the lower one wins. Reporting which it was
    matters more than the number: a traffic-bound fleet is fixed by buying
    allowance, a bandwidth-bound one by buying ports, and confusing the two
    means spending money on the wrong thing.
    """
    usable_mbit = a.node_port_mbit * a.node_utilisation_ceiling
    tunnelled_mbit = a.mbit_per_concurrent_user * (1 - a.direct_share)
    by_bandwidth = usable_mbit / max(a.peak_concurrency * tunnelled_mbit, 1e-9)

    tunnelled_gb = a.gb_per_active_user * (1 - a.direct_share)
    by_traffic = float("inf") if a.node_traffic_gb <= 0 else a.node_traffic_gb / max(tunnelled_gb, 1e-9)

    if by_traffic < by_bandwidth:
        return max(int(by_traffic), 1), "traffic allowance"
    return max(int(by_bandwidth), 1), "port bandwidth"


def evaluate(a: Assumptions) -> Result:
    active = int(a.users * a.active_share)
    tunnelled_per_user = a.gb_per_active_user * (1 - a.direct_share)

    per_node, constraint = users_per_node(a)
    nodes = max(1, -(-active // per_node))  # ceiling division

    node_cost = nodes * a.node_monthly_cost
    fixed_cost = a.head_monthly_cost + a.misc_monthly_cost
    total_cost = node_cost + fixed_cost

    revenue = active * a.ads_per_active_user * a.fill_rate * (a.ecpm / 1000.0)

    return Result(
        active_users=active,
        tunnelled_gb_total=active * tunnelled_per_user,
        users_per_node=per_node,
        nodes_needed=nodes,
        node_cost=node_cost,
        fixed_cost=fixed_cost,
        total_cost=total_cost,
        revenue=revenue,
        margin=revenue - total_cost,
        cost_per_active_user=total_cost / max(active, 1),
        revenue_per_active_user=revenue / max(active, 1),
        binding_constraint=constraint,
    )


# --- the questions worth asking -----------------------------------------


def breakeven_ecpm(a: Assumptions) -> float:
    """The eCPM at which the service stops losing money."""
    r = evaluate(a)
    impressions = r.active_users * a.ads_per_active_user * a.fill_rate
    return float("inf") if impressions <= 0 else r.total_cost / impressions * 1000.0


def breakeven_ads_per_user(a: Assumptions) -> float:
    """How many rewarded views each active user must complete, monthly."""
    r = evaluate(a)
    per_ad = a.fill_rate * (a.ecpm / 1000.0)
    return float("inf") if per_ad <= 0 else r.total_cost / max(r.active_users, 1) / per_ad


def affordable_gb_per_user(a: Assumptions) -> float | None:
    """The traffic cap ad revenue can pay for, or None if capping GB is futile.

    None is the interesting answer and it comes up more often than expected.
    A gigabyte cap only saves money when nodes are *bought by the gigabyte*.
    On an unmetered node the fleet is sized by port bandwidth, and halving
    everyone's monthly volume changes the bill by nothing at all — the same
    number of people are still online at eight in the evening.

    Returning a number here regardless would be worse than useless: it would
    send someone off to build a quota system that cannot move the figure it
    was built to move.
    """
    if users_per_node(a)[1] != "traffic allowance":
        return None

    lo, hi = 0.0, 10_000.0
    for _ in range(60):
        mid = (lo + hi) / 2
        if evaluate(replace(a, gb_per_active_user=mid)).margin >= 0:
            lo = mid
        else:
            hi = mid
    return lo


def affordable_speed_per_user(a: Assumptions) -> float | None:
    """The peak per-user rate the revenue supports, in Mbit/s.

    This is the lever when the fleet is bandwidth-bound, and it is the one
    a speed cap actually pulls: slower users are smaller users *at the
    moment of contention*, so more of them fit on the same port.

    None when even a vanishing rate does not close the gap — meaning the
    shortfall is in fixed costs, not in what users consume, and no cap of
    any kind will fix it.
    """
    lo, hi = 0.0, a.mbit_per_concurrent_user * 40
    if evaluate(replace(a, mbit_per_concurrent_user=1e-9)).margin < 0:
        return None
    for _ in range(60):
        mid = (lo + hi) / 2
        if evaluate(replace(a, mbit_per_concurrent_user=mid)).margin >= 0:
            lo = mid
        else:
            hi = mid
    return lo


def _money(value: float) -> str:
    return f"{value:>12,.0f} ₽".replace(",", " ")


def report(a: Assumptions, title: str = "") -> str:
    r = evaluate(a)
    lines = []
    if title:
        lines += [title, "=" * len(title)]
    lines += [
        f"  пользователей          {a.users:>10,}".replace(",", " "),
        f"  из них активных        {r.active_users:>10,}".replace(",", " "),
        f"  трафик через туннель   {r.tunnelled_gb_total / 1024:>10.1f} ТБ/мес",
        f"  на ноду помещается     {r.users_per_node:>10,} (упирается в {r.binding_constraint})".replace(",", " "),
        f"  нод нужно              {r.nodes_needed:>10}",
        "",
        f"  расходы на ноды      {_money(r.node_cost)}",
        f"  голова и прочее      {_money(r.fixed_cost)}",
        f"  ИТОГО расходы        {_money(r.total_cost)}",
        f"  выручка с рекламы    {_money(r.revenue)}",
        f"  {'ПРИБЫЛЬ' if r.margin >= 0 else 'УБЫТОК ':<20}{_money(r.margin)}",
        "",
        f"  на активного: расход {r.cost_per_active_user:>6.2f} ₽   выручка {r.revenue_per_active_user:>6.2f} ₽",
        "",
        f"  чтобы выйти в ноль нужно:",
        f"    eCPM               {breakeven_ecpm(a):>10.0f} ₽  (сейчас в модели {a.ecpm:.0f} ₽)",
        f"    просмотров/юзера   {breakeven_ads_per_user(a):>10.1f}   (сейчас в модели {a.ads_per_active_user:.0f})",
    ]

    gb_cap = affordable_gb_per_user(a)
    if gb_cap is None:
        lines.append(
            "    лимит по ГБ            — не поможет: ноды безлимитные, упор в полосу"
        )
    else:
        lines.append(f"    либо лимит трафика {gb_cap:>10.1f} ГБ/мес (сейчас {a.gb_per_active_user:.0f})")

    speed_cap = affordable_speed_per_user(a)
    if speed_cap is None:
        lines.append(
            "    лимит скорости         — не поможет: не хватает даже при нулевом трафике"
        )
    else:
        lines.append(
            f"    либо потолок скорости {speed_cap:>9.1f} Мбит/с на онлайн-юзера "
            f"(сейчас {a.mbit_per_concurrent_user:.0f})"
        )

    return "\n".join(lines)


def sweep(base: Assumptions, sizes: tuple[int, ...]) -> str:
    """Margin against audience size.

    The shape matters more than any single row: fixed costs do not grow with
    users, so an ad-funded service is loss-making below some size and
    profitable above it. Finding that size is the point of the exercise.
    """
    rows = [
        f"{'юзеров':>10} {'активных':>9} {'нод':>5} {'расход':>11} {'выручка':>11} {'итог':>11}"
    ]
    rows.append("-" * 62)
    for n in sizes:
        r = evaluate(replace(base, users=n))
        rows.append(
            f"{n:>10,} {r.active_users:>9,} {r.nodes_needed:>5} "
            f"{r.total_cost:>10,.0f}₽ {r.revenue:>10,.0f}₽ {r.margin:>+10,.0f}₽".replace(",", " ")
        )
    return "\n".join(rows)


def main() -> None:
    parser = argparse.ArgumentParser(description="Сходится ли реклама с расходами на серверы")
    parser.add_argument("--users", type=int, default=10_000)
    parser.add_argument("--ecpm", type=float, default=120.0)
    parser.add_argument("--gb", type=float, default=12.0, help="ГБ на активного пользователя в месяц")
    parser.add_argument("--ads", type=float, default=12.0, help="просмотров рекламы на активного в месяц")
    parser.add_argument("--node-cost", type=float, default=700.0)
    args = parser.parse_args()

    base = Assumptions(
        users=args.users,
        ecpm=args.ecpm,
        gb_per_active_user=args.gb,
        ads_per_active_user=args.ads,
        node_monthly_cost=args.node_cost,
    )
    print(report(base, f"Базовый сценарий: {args.users:,} пользователей".replace(",", " ")))
    print()
    print("Как это масштабируется")
    print("======================")
    print(sweep(base, (1_000, 5_000, 10_000, 25_000, 50_000, 100_000)))


if __name__ == "__main__":
    main()
