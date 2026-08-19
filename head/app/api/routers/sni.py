"""Operating the SNI pool — refreshing candidates and probing them per node."""

from __future__ import annotations

import uuid

from fastapi import APIRouter, HTTPException, status
from pydantic import BaseModel
from sqlalchemy import func, select

from app.api.auth import AdminAuth
from app.api.deps import DbSession
from app.db.models.node import Node, SniCandidate, SniProbe
from app.services.sni_discovery import (
    default_sources,
    probe_candidates_for_node,
    refresh_candidates,
)

router = APIRouter(prefix="/api/v1/sni", tags=["sni"], dependencies=[AdminAuth])


class RefreshResponse(BaseModel):
    added: int
    pool_size: int


class ProbeSummary(BaseModel):
    node_id: uuid.UUID
    probed: int
    usable: int
    from_node: bool


class CandidateResponse(BaseModel):
    domain: str
    source: str
    active: bool
    burn_count: int
    usable_on_nodes: int


@router.post("/refresh", response_model=RefreshResponse)
def refresh(db: DbSession) -> RefreshResponse:
    """Pull fresh domains from the configured popularity sources into the pool."""
    added = refresh_candidates(db, default_sources())
    db.commit()
    pool_size = db.scalar(select(func.count()).select_from(SniCandidate)) or 0
    return RefreshResponse(added=added, pool_size=pool_size)


@router.post("/probe/{node_id}", response_model=ProbeSummary)
def probe(node_id: uuid.UUID, db: DbSession) -> ProbeSummary:
    """Verify pool candidates from one node's vantage point."""
    node = db.get(Node, node_id)
    if node is None:
        raise HTTPException(status_code=status.HTTP_404_NOT_FOUND, detail="unknown node")

    results = probe_candidates_for_node(db, node)
    db.commit()

    any_probe = db.scalar(select(SniProbe).where(SniProbe.node_id == node.id))
    return ProbeSummary(
        node_id=node.id,
        probed=len(results),
        usable=sum(1 for r in results if r.ok),
        from_node=bool(any_probe.from_node) if any_probe else False,
    )


@router.get("/candidates", response_model=list[CandidateResponse])
def candidates(db: DbSession, limit: int = 100) -> list[CandidateResponse]:
    rows = db.scalars(
        select(SniCandidate).order_by(SniCandidate.burn_count.asc()).limit(limit)
    ).all()
    return [
        CandidateResponse(
            domain=c.domain,
            source=c.source,
            active=c.active,
            burn_count=c.burn_count,
            usable_on_nodes=sum(1 for p in c.probes if p.ok),
        )
        for c in rows
    ]
