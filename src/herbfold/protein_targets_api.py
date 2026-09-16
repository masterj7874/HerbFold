"""Explicit registration endpoints; reading a target never triggers a download."""

from fastapi import APIRouter, HTTPException
from pydantic import BaseModel, ConfigDict, Field

from .protein_targets import TargetRegistry, TargetRegistryError


class RegisterTargetRequest(BaseModel):
    model_config = ConfigDict(extra="forbid")
    accession: str = Field(min_length=1, max_length=20)


def make_router(store):
    registry = TargetRegistry(store)
    router = APIRouter(prefix="/api/molecular/targets", tags=["protein-targets"])

    @router.get("")
    def list_targets():
        try:
            return {"items": registry.list()}
        except TargetRegistryError as exc:
            raise HTTPException(exc.status_code, str(exc)) from exc

    @router.post("/register")
    def register_target(body: RegisterTargetRequest):
        try:
            return registry.register(body.accession)
        except TargetRegistryError as exc:
            raise HTTPException(exc.status_code, str(exc)) from exc

    @router.get("/{accession}")
    def get_target(accession: str):
        try:
            return registry.get(accession)
        except KeyError as exc:
            raise HTTPException(404, "등록되지 않은 표적입니다. UniProt에서 먼저 등록하세요.") from exc
        except TargetRegistryError as exc:
            raise HTTPException(exc.status_code, str(exc)) from exc

    return router
