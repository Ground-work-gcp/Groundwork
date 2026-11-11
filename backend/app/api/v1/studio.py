"""``/studio`` — the Engineering Studio.

Stateless tools (``POST /studio/<tool>``) for one-off use, and **projects**: an org-scoped,
audited pipeline (requirements → stack → architecture → stories → scaffold) whose every run is
a new immutable artifact version. Downstream steps record the upstream versions they were built
from, so a regenerated upstream marks them stale; approval is refused on stale or self-made
artifacts (when separate approvers are required) and is written to the audit chain.
"""

from __future__ import annotations

import json
import time
from collections import defaultdict, deque
from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException, Response
from pydantic import BaseModel, Field
from sqlalchemy import func, select
from sqlalchemy.orm import Session

from app.core.config import get_settings
from app.domain import audit
from app.infrastructure.database.models import StudioArtifact, StudioProject, User, utcnow
from app.infrastructure.database.session import get_db
from app.services import ai, studio
from app.services.studio import pipeline

from .deps import current_user, require

router = APIRouter(prefix="/studio", tags=["studio"])
Lang = Literal["python", "node", "java", "go", "typescript", "javascript"]
Step = Literal["requirements", "stack", "architecture", "stories", "scaffold"]
_hits: dict[str, deque[float]] = defaultdict(deque)
MAX_CONTENT_BYTES = 512_000


def _limit(user: User) -> None:
    """Per-user sliding window; protects the Gemini quota and the CPU-bound analysers."""
    per_minute = get_settings().studio_rate_per_minute
    now = time.monotonic()
    window = _hits[user.id]
    while window and now - window[0] > 60:
        window.popleft()
    if len(window) >= per_minute:
        raise HTTPException(status_code=429, detail=f"Studio limit is {per_minute} requests per minute; try again shortly")
    window.append(now)


def limited(user: User = Depends(current_user)) -> User:
    _limit(user)
    return user


def limited_writer(user: User = Depends(require("studio:write"))) -> User:
    _limit(user)
    return user


# ── stateless tools ─────────────────────────────────────────────────────────


class IdeaIn(BaseModel):
    idea: str = Field(min_length=3, max_length=8000)
    notes: str = Field(default="", max_length=8000)


class StackIn(BaseModel):
    requirements: str = Field(min_length=3, max_length=20000)
    language: Lang = "python"
    scale: Literal["small", "medium", "large"] = "small"


class ArchIn(BaseModel):
    requirements: str = Field(min_length=3, max_length=20000)
    stack: list[dict[str, Any]] | None = None


class ScaffoldIn(BaseModel):
    name: str = Field(min_length=1, max_length=80)
    language: Literal["python", "node"] = "python"


class StoriesIn(BaseModel):
    requirements: list[str] = Field(min_length=1, max_length=60)
    persona: str = Field(default="user", max_length=60)


class CodeIn(BaseModel):
    code: str = Field(min_length=1, max_length=100_000)
    language: Lang = "python"
    module: str = Field(default="module", pattern=r"^[A-Za-z_][\w.]*$", max_length=80)


def _run(fn, *args):
    try:
        return fn(*args)
    except ValueError as exc:
        raise HTTPException(status_code=422, detail=str(exc)) from exc


@router.get("/status")
def status(_: User = Depends(current_user)) -> dict[str, Any]:
    return {"ai": ai.status(), "tools": list(studio.TOOLS), "steps": list(pipeline.STEPS), "cache": studio.CACHE.stats(),
            "rate_per_minute": get_settings().studio_rate_per_minute}


@router.post("/requirements")
def requirements(body: IdeaIn, _: User = Depends(limited)):
    return _run(studio.requirements, body.idea, body.notes)


@router.post("/stack")
def stack(body: StackIn, _: User = Depends(limited)):
    return _run(studio.stack, body.requirements, body.language, body.scale)


@router.post("/architecture")
def architecture(body: ArchIn, _: User = Depends(limited)):
    return _run(studio.architecture, body.requirements, body.stack)


@router.post("/scaffold")
def scaffold(body: ScaffoldIn, _: User = Depends(limited)):
    return _run(studio.scaffold, body.name, body.language)


@router.post("/stories")
def stories(body: StoriesIn, _: User = Depends(limited)):
    return _run(studio.stories, body.requirements, body.persona)


@router.post("/code-health")
def code_health(body: CodeIn, _: User = Depends(limited)):
    return _run(studio.code_health, body.code, body.language)


@router.post("/visualize")
def visualize(body: CodeIn, _: User = Depends(limited)):
    return _run(studio.visualize, body.code, body.language)


@router.post("/tests")
def tests(body: CodeIn, _: User = Depends(limited)):
    return _run(studio.unit_tests, body.code, body.language, body.module)


# ── projects ────────────────────────────────────────────────────────────────


class ProjectIn(BaseModel):
    name: str = Field(min_length=1, max_length=120)
    idea: str = Field(min_length=10, max_length=8000)
    language: Literal["python", "node", "java", "go"] = "python"
    scale: Literal["small", "medium", "large"] = "small"
    persona: str = Field(default="user", min_length=1, max_length=60)


class ProjectPatch(BaseModel):
    name: str | None = Field(default=None, min_length=1, max_length=120)
    idea: str | None = Field(default=None, min_length=10, max_length=8000)
    language: Literal["python", "node", "java", "go"] | None = None
    scale: Literal["small", "medium", "large"] | None = None
    persona: str | None = Field(default=None, min_length=1, max_length=60)
    status: Literal["active", "archived"] | None = None


class RunIn(BaseModel):
    notes: str = Field(default="", max_length=8000)


class EditIn(BaseModel):
    content: dict[str, Any]


def _project(db: Session, user: User, project_id: str) -> StudioProject:
    project = db.get(StudioProject, project_id)
    if project is None or project.organization_id != user.organization_id:
        raise HTTPException(status_code=404, detail="Project not found")
    return project


def _latest(db: Session, project_id: str) -> dict[str, StudioArtifact]:
    newest = (select(StudioArtifact.kind, func.max(StudioArtifact.version).label("v"))
              .where(StudioArtifact.project_id == project_id).group_by(StudioArtifact.kind).subquery())
    rows = db.scalars(select(StudioArtifact).join(newest, (StudioArtifact.kind == newest.c.kind) & (StudioArtifact.version == newest.c.v))
                      .where(StudioArtifact.project_id == project_id)).all()
    return {a.kind: a for a in rows}


def _project_out(p: StudioProject) -> dict[str, Any]:
    return {"id": p.id, "name": p.name, "idea": p.idea, "language": p.language, "scale": p.scale, "persona": p.persona,
            "status": p.status, "created_at": p.created_at.isoformat(), "updated_at": p.updated_at.isoformat()}


def _artifact_out(a: StudioArtifact, latest_versions: dict[str, int], with_content: bool = True) -> dict[str, Any]:
    out = {"id": a.id, "kind": a.kind, "version": a.version, "source": a.source, "model": a.model, "based_on": a.based_on,
           "stale": pipeline.stale(a.kind, a.based_on, latest_versions), "approved": a.approved_at is not None,
           "approved_at": a.approved_at.isoformat() if a.approved_at else None, "approved_by_id": a.approved_by_id,
           "created_by_id": a.created_by_id, "created_at": a.created_at.isoformat()}
    if with_content:
        out["content"] = a.content
    return out


def _progress(latest: dict[str, StudioArtifact]) -> dict[str, Any]:
    versions = {k: a.version for k, a in latest.items()}
    done = [k for k in pipeline.STEPS if k in latest and not pipeline.stale(k, latest[k].based_on, versions)]
    nxt = next((k for k in pipeline.STEPS if k not in done), None)
    return {"done": done, "next": nxt, "percent": round(100 * len(done) / len(pipeline.STEPS)),
            "approved": [k for k in pipeline.STEPS if k in latest and latest[k].approved_at]}


@router.get("/projects")
def list_projects(include_archived: bool = False, user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")):
    q = select(StudioProject).where(StudioProject.organization_id == user.organization_id)
    if not include_archived:
        q = q.where(StudioProject.status == "active")
    projects = db.scalars(q.order_by(StudioProject.updated_at.desc()).limit(200)).all()
    return {"projects": [{**_project_out(p), "progress": _progress(_latest(db, p.id))} for p in projects]}


@router.post("/projects", status_code=201)
def create_project(body: ProjectIn, user: User = Depends(require("studio:write")), db: Session = Depends(get_db, scope="function")):
    p = StudioProject(organization_id=user.organization_id, created_by_id=user.id, **body.model_dump())
    db.add(p)
    db.flush()
    audit.record(db, organization_id=user.organization_id, event="studio.project_created", actor=user,
                 entity_type="studio_project", entity_id=p.id, detail={"language": p.language, "scale": p.scale})
    return get_project(p.id, user, db)


@router.get("/projects/{project_id}")
def get_project(project_id: str, user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")):
    p = _project(db, user, project_id)
    latest = _latest(db, p.id)
    versions = {k: a.version for k, a in latest.items()}
    contents = {k: a.content for k, a in latest.items()}
    return {**_project_out(p), "progress": _progress(latest),
            "steps": {k: _artifact_out(latest[k], versions) if k in latest else None for k in pipeline.STEPS},
            "dependencies": pipeline.DEPENDS,
            "traceability": pipeline.traceability(contents.get("requirements"), contents.get("stories"))}


@router.patch("/projects/{project_id}")
def update_project(project_id: str, body: ProjectPatch, user: User = Depends(require("studio:write")), db: Session = Depends(get_db, scope="function")):
    p = _project(db, user, project_id)
    changes = body.model_dump(exclude_none=True)
    for k, v in changes.items():
        setattr(p, k, v)
    p.updated_at = utcnow()
    audit.record(db, organization_id=user.organization_id, event="studio.project_updated", actor=user,
                 entity_type="studio_project", entity_id=p.id, detail={"fields": sorted(changes)})
    return get_project(p.id, user, db)


def _generate(p: StudioProject, kind: str, contents: dict[str, dict[str, Any]], notes: str) -> dict[str, Any]:
    req = contents.get("requirements") or {}
    text = pipeline.requirements_text(req)
    if kind == "requirements":
        prior = "\n".join(req.get("functional", []))
        return studio.requirements(p.idea, "\n".join(x for x in (notes, prior) if x))
    if kind == "stack":
        return studio.stack(f"{text}\n{notes}", p.language, p.scale)
    if kind == "architecture":
        return studio.architecture(f"{text}\n{notes}", (contents.get("stack") or {}).get("stack"))
    if kind == "stories":
        return studio.stories(req.get("functional", []), p.persona)
    return pipeline.bundle(p.name, p.language, contents)


def _store(db: Session, user: User, p: StudioProject, kind: str, content: dict[str, Any], latest: dict[str, StudioArtifact], source: str) -> StudioArtifact:
    if len(json.dumps(content)) > MAX_CONTENT_BYTES:
        raise HTTPException(status_code=413, detail="Artifact is too large")
    version = (latest[kind].version + 1) if kind in latest else 1
    based_on = {up: latest[up].version for up in pipeline.DEPENDS[kind] if up in latest}
    a = StudioArtifact(project_id=p.id, kind=kind, version=version, content=content, source=source,
                       model=str(content.get("model") or "")[:60], based_on=based_on, created_by_id=user.id)
    db.add(a)
    p.updated_at = utcnow()
    db.flush()
    latest[kind] = a
    audit.record(db, organization_id=user.organization_id, event=f"studio.{kind}_{'edited' if source == 'edited' else 'generated'}",
                 actor=user, entity_type="studio_artifact", entity_id=a.id, detail={"project": p.id, "version": version, "source": source})
    return a


def _run_step(db: Session, user: User, p: StudioProject, kind: str, latest: dict[str, StudioArtifact], notes: str = "") -> StudioArtifact:
    if p.status != "active":
        raise HTTPException(status_code=409, detail="Project is archived")
    missing = [up for up in pipeline.DEPENDS[kind] if up not in latest]
    if missing:
        raise HTTPException(status_code=409, detail=f"Generate {', '.join(missing)} first")
    content = _run(_generate, p, kind, {k: a.content for k, a in latest.items()}, notes)
    return _store(db, user, p, kind, content, latest, content.get("source", "rules"))


@router.post("/projects/{project_id}/steps/{kind}/run")
def run_step(project_id: str, kind: Step, body: RunIn | None = None, user: User = Depends(limited_writer), db: Session = Depends(get_db, scope="function")):
    p = _project(db, user, project_id)
    latest = _latest(db, p.id)
    _run_step(db, user, p, kind, latest, (body or RunIn()).notes)
    return get_project(p.id, user, db)


@router.post("/projects/{project_id}/run-all")
def run_all(project_id: str, user: User = Depends(limited_writer), db: Session = Depends(get_db, scope="function")):
    """Run every missing or stale step in dependency order (fresh ones are left untouched)."""
    p = _project(db, user, project_id)
    latest = _latest(db, p.id)
    ran = []
    for kind in pipeline.STEPS:
        versions = {k: a.version for k, a in latest.items()}
        if kind not in latest or pipeline.stale(kind, latest[kind].based_on, versions):
            _run_step(db, user, p, kind, latest)
            ran.append(kind)
    return {**get_project(p.id, user, db), "ran": ran}


@router.put("/projects/{project_id}/steps/{kind}")
def edit_step(project_id: str, kind: Step, body: EditIn, user: User = Depends(require("studio:write")), db: Session = Depends(get_db, scope="function")):
    """A human edit is stored as a new version (source=edited); history is never overwritten."""
    p = _project(db, user, project_id)
    latest = _latest(db, p.id)
    if kind not in latest:
        raise HTTPException(status_code=409, detail=f"Generate {kind} before editing it")
    required = {"requirements": "functional", "stack": "stack", "architecture": "components", "stories": "stories", "scaffold": "files"}[kind]
    if not isinstance(body.content.get(required), list):
        raise HTTPException(status_code=422, detail=f"{kind} content must contain a '{required}' list")
    content = {**body.content, "source": "edited"}
    if kind == "stories":
        for s in content["stories"]:
            if isinstance(s, dict):
                s["invest_warnings"] = studio.invest(s)
    _store(db, user, p, kind, content, latest, "edited")
    return get_project(p.id, user, db)


@router.post("/projects/{project_id}/steps/{kind}/approve")
def approve_step(project_id: str, kind: Step, user: User = Depends(require("studio:approve")), db: Session = Depends(get_db, scope="function")):
    p = _project(db, user, project_id)
    latest = _latest(db, p.id)
    a = latest.get(kind)
    if a is None:
        raise HTTPException(status_code=409, detail=f"Nothing to approve: {kind} has not been generated")
    if a.approved_at:
        raise HTTPException(status_code=409, detail=f"{kind} v{a.version} is already approved")
    stale = pipeline.stale(kind, a.based_on, {k: x.version for k, x in latest.items()})
    if stale:
        raise HTTPException(status_code=409, detail=f"{kind} is stale (upstream changed: {', '.join(stale)}); regenerate before approving")
    if get_settings().require_separate_approver and a.created_by_id == user.id:
        raise HTTPException(status_code=403, detail="A different person must approve an artifact you generated")
    a.approved_by_id, a.approved_at = user.id, utcnow()
    audit.record(db, organization_id=user.organization_id, event="studio.artifact_approved", actor=user,
                 entity_type="studio_artifact", entity_id=a.id, detail={"project": p.id, "kind": kind, "version": a.version})
    return get_project(p.id, user, db)


@router.get("/projects/{project_id}/steps/{kind}/versions")
def versions(project_id: str, kind: Step, user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")):
    p = _project(db, user, project_id)
    latest = {k: a.version for k, a in _latest(db, p.id).items()}
    rows = db.scalars(select(StudioArtifact).where(StudioArtifact.project_id == p.id, StudioArtifact.kind == kind)
                      .order_by(StudioArtifact.version.desc())).all()
    return {"versions": [_artifact_out(a, latest, with_content=False) for a in rows]}


@router.get("/projects/{project_id}/steps/{kind}/versions/{version}")
def version(project_id: str, kind: Step, version: int, user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")):
    p = _project(db, user, project_id)
    a = db.scalar(select(StudioArtifact).where(StudioArtifact.project_id == p.id, StudioArtifact.kind == kind, StudioArtifact.version == version))
    if a is None:
        raise HTTPException(status_code=404, detail="Version not found")
    return _artifact_out(a, {k: x.version for k, x in _latest(db, p.id).items()})


@router.get("/projects/{project_id}/export.md")
def export_markdown(project_id: str, user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")) -> Response:
    p = _project(db, user, project_id)
    contents = {k: a.content for k, a in _latest(db, p.id).items()}
    md = pipeline.to_markdown(_project_out(p), contents, pipeline.traceability(contents.get("requirements"), contents.get("stories")))
    return Response(md, media_type="text/markdown; charset=utf-8",
                    headers={"content-disposition": f'attachment; filename="{studio._slug(p.name)}.md"'})


@router.get("/projects/{project_id}/stories.csv")
def export_stories(project_id: str, user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")) -> Response:
    p = _project(db, user, project_id)
    a = _latest(db, p.id).get("stories")
    if a is None:
        raise HTTPException(status_code=409, detail="Generate stories first")
    return Response(pipeline.jira_csv(a.content), media_type="text/csv; charset=utf-8",
                    headers={"content-disposition": f'attachment; filename="{studio._slug(p.name)}-stories.csv"'})


@router.get("/projects/{project_id}/scaffold.zip")
def export_zip(project_id: str, user: User = Depends(current_user), db: Session = Depends(get_db, scope="function")) -> Response:
    p = _project(db, user, project_id)
    a = _latest(db, p.id).get("scaffold")
    if a is None:
        raise HTTPException(status_code=409, detail="Generate the scaffold first")
    slug = studio._slug(p.name)
    return Response(pipeline.to_zip(a.content.get("files", []), slug), media_type="application/zip",
                    headers={"content-disposition": f'attachment; filename="{slug}.zip"'})
