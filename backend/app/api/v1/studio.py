"""``/studio`` — the Engineering Studio (see ``app.services.studio``)."""

from __future__ import annotations

from typing import Any, Literal

from fastapi import APIRouter, Depends, HTTPException
from pydantic import BaseModel, Field

from app.infrastructure.database.models import User
from app.services import ai, studio

from .deps import current_user

router = APIRouter(prefix="/studio", tags=["studio"])
Lang = Literal["python", "node", "java", "go", "typescript", "javascript"]


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
    return {"ai": ai.status(), "tools": list(studio.TOOLS)}


@router.post("/requirements")
def requirements(body: IdeaIn, _: User = Depends(current_user)):
    return _run(studio.requirements, body.idea, body.notes)


@router.post("/stack")
def stack(body: StackIn, _: User = Depends(current_user)):
    return _run(studio.stack, body.requirements, body.language, body.scale)


@router.post("/architecture")
def architecture(body: ArchIn, _: User = Depends(current_user)):
    return _run(studio.architecture, body.requirements, body.stack)


@router.post("/scaffold")
def scaffold(body: ScaffoldIn, _: User = Depends(current_user)):
    return _run(studio.scaffold, body.name, body.language)


@router.post("/stories")
def stories(body: StoriesIn, _: User = Depends(current_user)):
    return _run(studio.stories, body.requirements, body.persona)


@router.post("/code-health")
def code_health(body: CodeIn, _: User = Depends(current_user)):
    return _run(studio.code_health, body.code, body.language)


@router.post("/visualize")
def visualize(body: CodeIn, _: User = Depends(current_user)):
    return _run(studio.visualize, body.code, body.language)


@router.post("/tests")
def tests(body: CodeIn, _: User = Depends(current_user)):
    return _run(studio.unit_tests, body.code, body.language, body.module)
