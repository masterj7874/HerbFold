"""API for the local deterministic drug-design specialist workflow."""

from fastapi import APIRouter, HTTPException, Query

from .design_pipeline import DesignPipeline, DesignRequest


def make_router(store, pool=None):
    router = APIRouter(prefix="/api/design-pipeline", tags=["design-pipeline"])
    engine = DesignPipeline(store, pool)
    router.engine = engine

    @router.get("/options")
    def options():
        return engine.options()

    @router.get("/runs")
    def runs(limit: int = Query(50, ge=1, le=200)):
        return {"items": engine.list(limit)}

    @router.post("/runs", status_code=202)
    def create_run(request: DesignRequest):
        try:
            return engine.create(request)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.get("/runs/{run_id}")
    def get_run(run_id: str):
        try:
            return engine.get(run_id)
        except KeyError as exc:
            raise HTTPException(404, "Design pipeline run not found") from exc

    @router.post("/runs/{run_id}/cancel")
    def cancel_run(run_id: str):
        try:
            return engine.cancel(run_id)
        except KeyError as exc:
            raise HTTPException(404, "Design pipeline run not found") from exc

    return router
