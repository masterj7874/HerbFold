"""Public API for durable research analyses and exact Astra provider readiness."""

from __future__ import annotations

from fastapi import APIRouter, HTTPException, Query

from .orchestration import AnalysisRequest, Orchestrator


def make_router(store, pool, *, provider=None):
    engine = Orchestrator(store, pool, provider=provider)
    router = APIRouter(tags=["analyses"])
    router.orchestrator = engine

    @router.get("/api/llm/status")
    def llm_status(refresh: bool = False):
        return engine.provider.status(refresh=refresh)

    @router.get("/api/analyses")
    def list_analyses():
        return {"analyses": engine.list()}

    @router.post("/api/analyses", status_code=202)
    def create_analysis(request: AnalysisRequest):
        return engine.create(request)

    @router.get("/api/analyses/{run_id}")
    def get_analysis(run_id: str):
        try:
            return engine.get(run_id)
        except KeyError:
            raise HTTPException(404, "Analysis not found") from None

    @router.get("/api/analyses/{run_id}/events")
    def get_events(run_id: str, after: int = Query(default=0, ge=0)):
        state = get_analysis(run_id)
        return {
            "id": run_id,
            "status": state["status"],
            "events": [event for event in state["events"] if event["seq"] > after],
            "next_cursor": len(state["events"]),
        }

    @router.post("/api/analyses/{run_id}/cancel")
    def cancel_analysis(run_id: str):
        get_analysis(run_id)
        return engine.cancel(run_id)

    @router.post("/api/analyses/{run_id}/resume", status_code=202)
    def resume_analysis(run_id: str):
        get_analysis(run_id)
        try:
            return engine.resume(run_id)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None

    @router.post("/api/analyses/{run_id}/quantum/refresh")
    def refresh_quantum(run_id: str):
        get_analysis(run_id)
        try:
            return engine.refresh_quantum(run_id)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from None

    return router
