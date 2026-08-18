"""Admin panel routes.

Server-rendered rather than an SPA: the panel runs on the same box as the
head, so avoiding a JS build step means deploying is `pip install` and
nothing else. Every mutation is a POST that redirects, which keeps the
back button and a page refresh from re-firing an action.
"""

from __future__ import annotations

import logging
import uuid
from datetime import UTC, datetime, timedelta
from typing import Annotated

from fastapi import APIRouter, Form, Request
from fastapi.responses import RedirectResponse
from sqlalchemy import func, select

from app.admin.deps import CurrentAdmin, redirect_with_flash, render
from app.api.deps import DbSession
from app.config import get_settings
from app.db.models.admin import AdminAudit
from app.db.models.logs import FailReport, NodeChannelEvent
from app.db.models.node import (
    Assignment,
    Inbound,
    InboundState,
    Node,
    NodeChannelState,
    NodeStatus,
    SniCandidate,
    SniProbe,
)
from app.db.models.outbox import ConfigPush
from app.db.models.plan import Plan, Subscription, SubscriptionType
from app.db.models.update import NodeUpdate, NodeUpdateStatus
from app.db.models.user import AuthIdentity, User, UserStatus
from app.services import provisioning, xray_updates
from app.services.admin_auth import (
    SESSION_COOKIE,
    audit,
    authenticate,
    issue_session,
)
from app.services.config_selector import NoCapacityError
from app.services.sni_discovery import (
    default_sources,
    probe_candidates_for_node,
    refresh_candidates,
)
from app.services.ssh_manager import SshError
from app.services.subscriptions import current_subscription
from app.services.tiering import reconcile_placement
from app.services.timeutil import as_aware

logger = logging.getLogger(__name__)
router = APIRouter(prefix="/admin", tags=["admin"], include_in_schema=False)

FormStr = Annotated[str, Form()]
FormInt = Annotated[int, Form()]


# --- auth ----------------------------------------------------------------


@router.get("/login")
def login_form(request: Request):
    return render(request, "login.html", admin=None)


@router.post("/login")
def login(request: Request, db: DbSession, username: FormStr, password: FormStr):
    admin = authenticate(db, username, password)
    if admin is None:
        db.commit()
        return redirect_with_flash("/admin/login", "err", "Неверный логин или пароль")

    audit(db, admin.username, "login")
    db.commit()

    response = RedirectResponse("/admin", status_code=303)
    response.set_cookie(
        SESSION_COOKIE,
        issue_session(admin.username),
        httponly=True,
        samesite="lax",
        secure=get_settings().admin_cookie_secure,
        max_age=get_settings().admin_session_hours * 3600,
    )
    return response


@router.get("/logout")
def logout():
    response = RedirectResponse("/admin/login", status_code=303)
    response.delete_cookie(SESSION_COOKIE)
    return response


# --- dashboard -----------------------------------------------------------


def _node_loads(db: DbSession) -> dict:
    rows = db.execute(
        select(Inbound.node_id, func.count(Assignment.id))
        .join(Assignment, Assignment.inbound_id == Inbound.id)
        .where(Assignment.released_at.is_(None))
        .group_by(Inbound.node_id)
    ).all()
    return {node_id: count for node_id, count in rows}


def _inbound_counts(db: DbSession) -> dict:
    rows = db.execute(
        select(Inbound.node_id, func.count())
        .where(Inbound.is_control_channel.is_(False))
        .group_by(Inbound.node_id)
    ).all()
    return {node_id: count for node_id, count in rows}


@router.get("")
def dashboard(request: Request, db: DbSession, admin: CurrentAdmin):
    nodes = list(db.scalars(select(Node).order_by(Node.country)).all())
    loads = _node_loads(db)
    for node in nodes:
        node.load = loads.get(node.id, 0)

    now = datetime.now(UTC)
    live_subs = [
        s for s in db.scalars(select(Subscription)).all() if (as_aware(s.expires_at) or now) > now
    ]

    stats = {
        "nodes_total": len(nodes),
        "nodes_active": sum(1 for n in nodes if n.status == NodeStatus.active),
        "capacity_total": sum(n.capacity for n in nodes if n.status == NodeStatus.active),
        "capacity_used": sum(n.load for n in nodes if n.status == NodeStatus.active),
        "nodes_degraded": sum(1 for n in nodes if n.channel_state == NodeChannelState.degraded),
        "nodes_isolated": sum(1 for n in nodes if n.channel_state == NodeChannelState.isolated),
        "users_total": db.scalar(select(func.count()).select_from(User)) or 0,
        "users_connected": db.scalar(
            select(func.count()).select_from(Assignment).where(Assignment.released_at.is_(None))
        ) or 0,
        "subs_paid": sum(1 for s in live_subs if s.type == SubscriptionType.paid),
        "subs_trial": sum(1 for s in live_subs if s.type == SubscriptionType.trial),
        "inbounds_active": db.scalar(
            select(func.count()).select_from(Inbound).where(Inbound.state == InboundState.active)
        ) or 0,
        "inbounds_dead": db.scalar(
            select(func.count()).select_from(Inbound).where(Inbound.state == InboundState.dead)
        ) or 0,
        "pushes_pending": db.scalar(
            select(func.count()).select_from(ConfigPush).where(ConfigPush.delivered_at.is_(None))
        ) or 0,
    }

    return render(
        request,
        "dashboard.html",
        admin,
        page="dashboard",
        s=stats,
        nodes=nodes,
        events=_recent_events(db, limit=8),
        now=now.strftime("%d.%m.%Y %H:%M UTC"),
    )


def _recent_events(db: DbSession, limit: int = 30):
    hosts = {n.id: n.host for n in db.scalars(select(Node)).all()}
    events = db.scalars(
        select(NodeChannelEvent).order_by(NodeChannelEvent.occurred_at.desc()).limit(limit)
    ).all()
    for event in events:
        event.host = hosts.get(event.node_id)
    return events


# --- nodes ---------------------------------------------------------------


@router.get("/nodes")
def nodes_page(request: Request, db: DbSession, admin: CurrentAdmin):
    return render(request, "nodes.html", admin, page="nodes", nodes=_nodes_with_counts(db))


def _nodes_with_counts(db: DbSession) -> list[Node]:
    nodes = list(db.scalars(select(Node).order_by(Node.country, Node.host)).all())
    loads = _node_loads(db)
    inbounds = _inbound_counts(db)
    ratio = get_settings().free_admission_ratio
    for node in nodes:
        node.load = loads.get(node.id, 0)
        node.inbound_count = inbounds.get(node.id, 0)
        # Where free users stop being admitted; the gap above it is the
        # headroom held for paying users.
        node.free_cutoff = int(node.capacity * ratio)
    return nodes


@router.post("/nodes/add")
def add_node(
    db: DbSession,
    admin: CurrentAdmin,
    host: FormStr,
    country: FormStr,
    ssh_user: FormStr,
    ssh_password: FormStr,
    ssh_port: FormInt = 22,
    uplink_mbit: FormInt = 100,
    capacity: FormInt = 200,
    control_sni: FormStr = "www.microsoft.com",
):
    try:
        result = provisioning.provision_node(
            db,
            host=host.strip(),
            country=country.strip(),
            ssh_user=ssh_user.strip(),
            ssh_password=ssh_password,
            ssh_port=ssh_port,
            uplink_mbit=uplink_mbit,
            capacity=capacity,
            control_sni=control_sni.strip(),
        )
    except (provisioning.ProvisioningError, SshError) as exc:
        db.commit()  # keep the half-provisioned node row and its audit trail
        audit(db, admin, "node.add.failed", host, str(exc)[:2000])
        db.commit()
        return redirect_with_flash("/admin/nodes", "err", f"Не удалось подключить {host}: {exc}")

    audit(db, admin, "node.add", host, f"uplink={uplink_mbit}mbit capacity={capacity}")
    db.commit()
    return redirect_with_flash(
        "/admin/nodes", "ok", f"Нода {host} подключена. " + " · ".join(result.log)
    )


@router.post("/nodes/{node_id}/rotate-password")
def rotate_password(db: DbSession, admin: CurrentAdmin, node_id: uuid.UUID):
    node = db.get(Node, node_id)
    if node is None:
        return redirect_with_flash("/admin/nodes", "err", "Нода не найдена")

    try:
        provisioning.rotate_node_password(db, node)
    except SshError as exc:
        db.rollback()
        return redirect_with_flash("/admin/nodes", "err", f"Не удалось сменить пароль: {exc}")

    audit(db, admin, "node.rotate_password", node.host)
    db.commit()
    return redirect_with_flash(
        "/admin/nodes", "ok", f"Пароль на {node.host} изменён. Старый больше не действует."
    )


@router.post("/nodes/{node_id}/capacity")
def set_node_capacity(db: DbSession, admin: CurrentAdmin, node_id: uuid.UUID, capacity: FormInt):
    node = db.get(Node, node_id)
    if node is None:
        return redirect_with_flash("/admin/nodes", "err", "Нода не найдена")

    previous = node.capacity
    node.capacity = max(1, capacity)
    audit(db, admin, "node.capacity", node.host, f"{previous} -> {node.capacity}")
    db.commit()

    ratio = get_settings().free_admission_ratio
    return redirect_with_flash(
        "/admin/nodes",
        "ok",
        f"Ёмкость {node.host}: {node.capacity}. Бесплатные перестают приниматься "
        f"на {int(node.capacity * ratio)}, остальное держится для платных.",
    )


@router.post("/nodes/{node_id}/status")
def set_node_status(db: DbSession, admin: CurrentAdmin, node_id: uuid.UUID, status: FormStr):
    node = db.get(Node, node_id)
    if node is None:
        return redirect_with_flash("/admin/nodes", "err", "Нода не найдена")

    node.status = NodeStatus(status)
    audit(db, admin, "node.status", node.host, status)
    db.commit()

    message = (
        f"Нода {node.host} выведена из ротации — новых пользователей она не получит, "
        "существующие продолжают работать."
        if node.status == NodeStatus.draining
        else f"Нода {node.host} снова в ротации."
    )
    return redirect_with_flash("/admin/nodes", "ok", message)


@router.post("/nodes/{node_id}/delete")
def delete_node(db: DbSession, admin: CurrentAdmin, node_id: uuid.UUID):
    node = db.get(Node, node_id)
    if node is None:
        return redirect_with_flash("/admin/nodes", "err", "Нода не найдена")

    stranded = db.scalar(
        select(func.count())
        .select_from(Assignment)
        .join(Inbound, Inbound.id == Assignment.inbound_id)
        .where(Inbound.node_id == node.id, Assignment.released_at.is_(None))
    ) or 0

    host = node.host
    db.delete(node)
    audit(db, admin, "node.delete", host, f"stranded_users={stranded}")
    db.commit()

    note = (
        f" {stranded} пользовател(ей) остались без конфига — они получат новый, "
        "нажав «Подключиться» или «Не работает»."
        if stranded
        else ""
    )
    return redirect_with_flash("/admin/nodes", "ok", f"Нода {host} удалена.{note}")


# --- users ---------------------------------------------------------------


@router.get("/users")
def users_page(request: Request, db: DbSession, admin: CurrentAdmin, q: str | None = None):
    statement = select(User).order_by(User.created_at.desc()).limit(200)
    if q:
        needle = q.strip()
        matching = db.scalars(
            select(AuthIdentity.user_id).where(AuthIdentity.provider_uid.ilike(f"%{needle}%"))
        ).all()
        ids = set(matching)
        try:
            ids.add(uuid.UUID(needle))
        except ValueError:
            pass
        statement = select(User).where(User.id.in_(ids or {uuid.uuid4()})).limit(200)

    users = list(db.scalars(statement).all())
    now = datetime.now(UTC)

    for user in users:
        user.identities = db.scalars(
            select(AuthIdentity).where(AuthIdentity.user_id == user.id)
        ).all()
        sub = current_subscription(db, user)
        user.sub_type = sub.type.value if sub else None
        user.sub_expires = as_aware(sub.expires_at) if sub else None

        assignment = db.scalar(
            select(Assignment)
            .where(Assignment.user_id == user.id, Assignment.released_at.is_(None))
            .order_by(Assignment.assigned_at.desc())
        )
        if assignment is not None:
            inbound = db.get(Inbound, assignment.inbound_id)
            node = db.get(Node, inbound.node_id)
            user.node_host = node.host
            # The tier is the inbound's: nodes serve both audiences.
            user.node_tier = inbound.tier.value
        else:
            user.node_host = None
            user.node_tier = None

    total = db.scalar(select(func.count()).select_from(User)) or 0
    return render(
        request, "users.html", admin, page="users", users=users, total=total, query=q, now=now
    )


@router.post("/users/{user_id}/grant")
def grant_subscription(db: DbSession, admin: CurrentAdmin, user_id: uuid.UUID, days: FormInt = 30):
    user = db.get(User, user_id)
    if user is None:
        return redirect_with_flash("/admin/users", "err", "Пользователь не найден")

    now = datetime.now(UTC)
    existing = current_subscription(db, user)
    if existing is not None and existing.type == SubscriptionType.paid:
        # Extend rather than replace, matching how a real renewal behaves.
        existing.expires_at = (as_aware(existing.expires_at) or now) + timedelta(days=days)
    else:
        db.add(
            Subscription(
                user_id=user.id,
                plan_id=None,
                type=SubscriptionType.paid,
                expires_at=now + timedelta(days=days),
            )
        )
    db.flush()

    try:
        reconcile_placement(db, user)
    except NoCapacityError:
        logger.warning("granted user %s has no paid node available yet", user.id)

    audit(db, admin, "user.grant", str(user.id), f"+{days}d")
    db.commit()
    return redirect_with_flash("/admin/users", "ok", f"Выдано {days} дн. подписки.")


@router.post("/users/{user_id}/revoke")
def revoke_subscription(db: DbSession, admin: CurrentAdmin, user_id: uuid.UUID):
    user = db.get(User, user_id)
    if user is None:
        return redirect_with_flash("/admin/users", "err", "Пользователь не найден")

    now = datetime.now(UTC)
    for sub in db.scalars(select(Subscription).where(Subscription.user_id == user.id)).all():
        if (as_aware(sub.expires_at) or now) > now:
            sub.expires_at = now
    db.flush()

    try:
        reconcile_placement(db, user)
    except NoCapacityError:
        logger.warning("revoked user %s has no free node available yet", user.id)

    audit(db, admin, "user.revoke", str(user.id))
    db.commit()
    return redirect_with_flash("/admin/users", "ok", "Подписка отозвана.")


@router.post("/users/{user_id}/ban")
def ban_user(db: DbSession, admin: CurrentAdmin, user_id: uuid.UUID, banned: FormStr = "1"):
    user = db.get(User, user_id)
    if user is None:
        return redirect_with_flash("/admin/users", "err", "Пользователь не найден")

    user.status = UserStatus.banned if banned == "1" else UserStatus.active
    audit(db, admin, "user.ban" if banned == "1" else "user.unban", str(user.id))
    db.commit()
    return redirect_with_flash(
        "/admin/users", "ok", "Пользователь заблокирован." if banned == "1" else "Блокировка снята."
    )


# --- plans ---------------------------------------------------------------


@router.get("/plans")
def plans_page(request: Request, db: DbSession, admin: CurrentAdmin):
    plans = list(db.scalars(select(Plan).order_by(Plan.duration_days)).all())
    counts = dict(
        db.execute(
            select(Subscription.plan_id, func.count()).group_by(Subscription.plan_id)
        ).all()
    )
    for plan in plans:
        plan.sub_count = counts.get(plan.id, 0)
        plan.price = float(plan.price)
    return render(request, "plans.html", admin, page="plans", plans=plans)


@router.post("/plans/add")
def add_plan(
    db: DbSession,
    admin: CurrentAdmin,
    code: FormStr,
    name: FormStr,
    duration_days: FormInt,
    max_devices: FormInt,
    price: Annotated[float, Form()],
):
    if db.scalar(select(Plan).where(Plan.code == code.strip())):
        return redirect_with_flash("/admin/plans", "err", f"Тариф с кодом {code} уже есть")

    db.add(
        Plan(
            code=code.strip(),
            name=name.strip(),
            duration_days=duration_days,
            max_devices=max_devices,
            price=price,
        )
    )
    audit(db, admin, "plan.add", code)
    db.commit()
    return redirect_with_flash("/admin/plans", "ok", f"Тариф {code} добавлен.")


@router.post("/plans/{plan_id}/toggle")
def toggle_plan(db: DbSession, admin: CurrentAdmin, plan_id: uuid.UUID):
    plan = db.get(Plan, plan_id)
    if plan is None:
        return redirect_with_flash("/admin/plans", "err", "Тариф не найден")

    plan.active = not plan.active
    audit(db, admin, "plan.toggle", plan.code, str(plan.active))
    db.commit()
    return redirect_with_flash(
        "/admin/plans", "ok", f"Тариф {plan.code} {'показан' if plan.active else 'скрыт'}."
    )


# --- SNI -----------------------------------------------------------------


@router.get("/sni")
def sni_page(request: Request, db: DbSession, admin: CurrentAdmin):
    candidates = list(
        db.scalars(
            select(SniCandidate).order_by(SniCandidate.burn_count, SniCandidate.domain).limit(300)
        ).all()
    )
    hosts = {n.id: n.host for n in db.scalars(select(Node)).all()}
    for candidate in candidates:
        probes = db.scalars(
            select(SniProbe).where(SniProbe.candidate_id == candidate.id)
        ).all()
        for probe in probes:
            probe.host = hosts.get(probe.node_id, "?")
        candidate.probes = probes

    nodes = list(
        db.scalars(
            select(Node).where(Node.channel_state != NodeChannelState.isolated)
        ).all()
    )
    return render(request, "sni.html", admin, page="sni", candidates=candidates, nodes=nodes)


@router.post("/sni/refresh")
def sni_refresh(db: DbSession, admin: CurrentAdmin):
    try:
        added = refresh_candidates(db, default_sources())
    except Exception as exc:  # noqa: BLE001 - an external source failing is a message, not a 500
        db.rollback()
        return redirect_with_flash("/admin/sni", "err", f"Источник недоступен: {exc}")

    audit(db, admin, "sni.refresh", detail=f"added={added}")
    db.commit()
    message = (
        f"Добавлено доменов: {added}."
        if added
        else "Новых доменов не появилось — источник вернул только уже известные."
    )
    return redirect_with_flash("/admin/sni", "ok", message)


@router.post("/sni/probe/{node_id}")
def sni_probe(db: DbSession, admin: CurrentAdmin, node_id: uuid.UUID):
    node = db.get(Node, node_id)
    if node is None:
        return redirect_with_flash("/admin/sni", "err", "Нода не найдена")

    try:
        results = probe_candidates_for_node(db, node)
    except Exception as exc:  # noqa: BLE001 - a failed probe is a message, not a 500
        db.rollback()
        return redirect_with_flash("/admin/sni", "err", f"Проверка не удалась: {exc}")

    usable = sum(1 for r in results if r.ok)
    audit(db, admin, "sni.probe", node.host, f"{usable}/{len(results)}")
    db.commit()
    return redirect_with_flash(
        "/admin/sni", "ok", f"С {node.host}: пригодных {usable} из {len(results)}."
    )


@router.post("/sni/add")
def sni_add(db: DbSession, admin: CurrentAdmin, domain: FormStr):
    domain = domain.strip().lower()
    if db.scalar(select(SniCandidate).where(SniCandidate.domain == domain)):
        return redirect_with_flash("/admin/sni", "err", f"{domain} уже в пуле")

    db.add(SniCandidate(domain=domain, source="static"))
    audit(db, admin, "sni.add", domain)
    db.commit()
    return redirect_with_flash("/admin/sni", "ok", f"{domain} добавлен. Проверьте его с нужной ноды.")


@router.post("/sni/{candidate_id}/toggle")
def sni_toggle(db: DbSession, admin: CurrentAdmin, candidate_id: uuid.UUID):
    candidate = db.get(SniCandidate, candidate_id)
    if candidate is None:
        return redirect_with_flash("/admin/sni", "err", "Домен не найден")

    candidate.active = not candidate.active
    audit(db, admin, "sni.toggle", candidate.domain, str(candidate.active))
    db.commit()
    return redirect_with_flash("/admin/sni", "ok", f"{candidate.domain}: {'включён' if candidate.active else 'выключен'}.")


# --- Xray updates --------------------------------------------------------


@router.get("/updates")
def updates_page(request: Request, db: DbSession, admin: CurrentAdmin):
    """Everything about Xray versions in one place: what each node runs, what
    is on offer, and what has been done about it.

    The per-node current version is read from the last proposal rather than
    queried live: this page is opened by a human waiting for it to render,
    and one control-channel round trip per node would make that wait scale
    with the fleet. The scheduled check is what keeps the numbers fresh.
    """
    nodes = list(db.scalars(select(Node).order_by(Node.country, Node.host)).all())
    rows = list(
        db.scalars(select(NodeUpdate).order_by(NodeUpdate.created_at.desc()).limit(100)).all()
    )
    hosts = {node.id: node for node in nodes}
    for row in rows:
        node = hosts.get(row.node_id)
        row.host = node.host if node else "удалена"
        row.country = node.country if node else "?"

    pending = [row for row in rows if row.status == NodeUpdateStatus.pending]
    history = [row for row in rows if row.status != NodeUpdateStatus.pending][:40]

    # Latest *known* release: deliberately the cached answer rather than a
    # fresh lookup, so opening this page cannot spend the GitHub rate limit
    # or hang for the full timeout on a head that cannot reach GitHub.
    latest = xray_updates.latest_release_version(cached_only=True)

    return render(
        request,
        "updates.html",
        admin,
        page="updates",
        nodes=nodes,
        pending=pending,
        history=history,
        latest=latest,
        node_versions=_node_versions(rows),
    )


def _node_versions(rows: list) -> dict:
    """Best-known current version per node, newest observation first."""
    seen: dict = {}
    for row in rows:  # already ordered newest first
        if row.node_id in seen:
            continue
        version = row.version_after or row.version_before
        if version:
            seen[row.node_id] = version
    return seen


@router.post("/updates/check")
def updates_check(db: DbSession, admin: CurrentAdmin):
    try:
        raised = xray_updates.check_for_updates(db)
    except Exception as exc:
        # A broken feed is a message on the page, not a 500 in an
        # operator's face.
        db.rollback()
        logger.exception("manual Xray update check failed")
        return redirect_with_flash("/admin/updates", "err", f"Проверка не удалась: {exc}")

    audit(db, admin, "updates.check", detail=f"raised={len(raised)}")
    db.commit()

    if raised:
        return redirect_with_flash(
            "/admin/updates", "ok", f"Найдено обновлений: {len(raised)}. Подтвердите нужные."
        )
    latest = xray_updates.latest_release_version()
    if latest is None:
        return redirect_with_flash(
            "/admin/updates",
            "warn",
            "Не удалось прочитать список релизов Xray — GitHub недоступен с этого сервера.",
        )
    return redirect_with_flash(
        "/admin/updates", "ok", f"Всё актуально: последняя версия Xray — {latest}."
    )


@router.post("/updates/{update_id}/decide")
def updates_decide(db: DbSession, admin: CurrentAdmin, update_id: uuid.UUID, approve: FormStr):
    changed = xray_updates.decide(db, [update_id], approve=approve == "1", by=admin)
    if not changed:
        # Already decided — most often the same update approved from Telegram
        # a moment earlier.
        db.rollback()
        return redirect_with_flash("/admin/updates", "warn", "Это обновление уже решено.")

    audit(db, admin, "updates.decide", str(update_id), approve)
    db.commit()
    if approve == "1":
        return redirect_with_flash(
            "/admin/updates",
            "ok",
            "Обновление поставлено в очередь. Нода перезапустится в течение минуты — "
            "активные подключения на ней оборвутся и клиенты переподключатся сами.",
        )
    return redirect_with_flash("/admin/updates", "ok", "Обновление отклонено.")


@router.post("/updates/approve-all")
def updates_approve_all(db: DbSession, admin: CurrentAdmin, target_version: FormStr):
    ids = [
        row.id
        for row in db.scalars(
            select(NodeUpdate).where(
                NodeUpdate.status == NodeUpdateStatus.pending,
                NodeUpdate.target_version == target_version,
            )
        ).all()
    ]
    changed = xray_updates.decide(db, ids, approve=True, by=admin)
    audit(db, admin, "updates.approve_all", target_version, str(changed))
    db.commit()
    return redirect_with_flash(
        "/admin/updates",
        "ok",
        f"Подтверждено нод: {changed}. Обновляются по одной, чтобы не уронить весь флот разом.",
    )


# --- events and audit ----------------------------------------------------


@router.get("/events")
def events_page(request: Request, db: DbSession, admin: CurrentAdmin):
    reports = db.scalars(select(FailReport).order_by(FailReport.reported_at.desc()).limit(40)).all()
    for report in reports:
        inbound = db.get(Inbound, report.inbound_id)
        node = db.get(Node, inbound.node_id) if inbound else None
        report.sni = inbound.sni if inbound else "?"
        report.port = inbound.port if inbound else "?"
        report.host = node.host if node else "?"

    pushes = db.scalars(select(ConfigPush).order_by(ConfigPush.created_at.desc()).limit(40)).all()
    return render(
        request,
        "events.html",
        admin,
        page="events",
        events=_recent_events(db, limit=40),
        reports=reports,
        pushes=pushes,
    )


@router.get("/audit")
def audit_page(request: Request, db: DbSession, admin: CurrentAdmin):
    entries = db.scalars(select(AdminAudit).order_by(AdminAudit.at.desc()).limit(200)).all()
    return render(request, "audit.html", admin, page="audit", entries=entries)
