"""Explicit preparation/submission and read-only reconnection of AF3 workflows."""

from fastapi import APIRouter, HTTPException, Query

from .af3_workflow import AF3WorkflowAttach, AF3WorkflowRequest, AF3WorkflowResume, AF3Workflows


def make_router(store, predictions):
    service = AF3Workflows(store, predictions)
    router = APIRouter(prefix="/api/af3-workflows", tags=["af3-workflows"])
    router.service = service

    @router.get("/options")
    def options():
        return service.options()

    @router.get("/jobs")
    def jobs(limit: int = Query(50, ge=1, le=100)):
        return {"items": service.jobs(limit)}

    @router.get("/by-request/{request_id}")
    def by_request(request_id: str):
        try:
            return service.by_request(request_id)
        except KeyError as exc:
            raise HTTPException(404, "AF3 workflow request not found") from exc
        except ValueError as exc:
            raise HTTPException(422, str(exc)) from exc

    @router.get("")
    def workflows(limit: int = Query(50, ge=1, le=100)):
        return {"items": service.list(limit)}

    @router.post("", status_code=202)
    def create(request: AF3WorkflowRequest):
        try:
            return service.create(request)
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.post("/attach", status_code=202)
    def attach(request: AF3WorkflowAttach):
        try:
            return service.attach(request)
        except KeyError as exc:
            raise HTTPException(404, "Registered studio prediction not found") from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    @router.get("/{workflow_id}")
    def get(workflow_id: str):
        try:
            return service.get(workflow_id)
        except KeyError as exc:
            raise HTTPException(404, "AF3 workflow not found") from exc

    @router.post("/{workflow_id}/resume", status_code=202)
    def resume(workflow_id: str, request: AF3WorkflowResume):
        try:
            return service.resume(workflow_id)
        except KeyError as exc:
            raise HTTPException(404, "AF3 workflow not found") from exc
        except ValueError as exc:
            raise HTTPException(409, str(exc)) from exc

    return router
