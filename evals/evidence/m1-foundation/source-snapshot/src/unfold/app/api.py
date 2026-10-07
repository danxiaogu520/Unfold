from __future__ import annotations

import asyncio
import json
from collections.abc import AsyncIterator
from contextlib import asynccontextmanager
from typing import Annotated, Any

from fastapi import FastAPI, Header, Request
from fastapi.exceptions import RequestValidationError
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse, StreamingResponse
from pydantic import BaseModel, ConfigDict, Field
from sqlalchemy.exc import SQLAlchemyError

from unfold.api_contract import ApiError, CreateRun, RunSnapshot
from unfold.app.config import AppSettings
from unfold.app.db import Database
from unfold.app.service import RunService, ServiceError


class CreateSpace(BaseModel):
    model_config = ConfigDict(extra="forbid", strict=True, str_strip_whitespace=True)
    domain: Annotated[str, Field(min_length=1, max_length=6000)]
    user_brief: Annotated[str, Field(min_length=1, max_length=6000)]


def create_app(settings: AppSettings, database: Database | None = None) -> FastAPI:
    database = database or Database(settings)
    service = RunService(database, settings)

    @asynccontextmanager
    async def lifespan(app: FastAPI) -> AsyncIterator[None]:
        yield
        database.close()

    app = FastAPI(title="Unfold / 知脉", version="0.1.0", lifespan=lifespan)
    app.state.service = service
    origins = {"http://127.0.0.1:5173", "http://localhost:5173", "http://127.0.0.1:8000"}
    app.add_middleware(
        CORSMiddleware,
        allow_origins=sorted(origins),
        allow_methods=["GET", "POST", "PATCH"],
        allow_headers=["Content-Type", "Idempotency-Key", "Last-Event-ID"],
    )

    @app.middleware("http")
    async def origin_guard(request: Request, call_next: Any) -> Any:
        origin = request.headers.get("origin")
        if origin and origin not in origins:
            error = ApiError(
                schema_version="error.v1",
                code="authentication",
                message="request origin is not allowed",
                retryable=False,
                outcome_unknown=False,
                run_id=None,
            )
            return JSONResponse(status_code=403, content=error.model_dump(mode="json"))
        return await call_next(request)

    @app.exception_handler(ServiceError)
    async def service_error(request: Request, exc: ServiceError) -> JSONResponse:
        error = ApiError(
            schema_version="error.v1",
            code=exc.code,
            message=str(exc),
            retryable=False,
            outcome_unknown=exc.outcome_unknown,
            run_id=None,
        )
        return JSONResponse(status_code=exc.http_status, content=error.model_dump(mode="json"))

    @app.exception_handler(RequestValidationError)
    async def invalid_request(request: Request, exc: RequestValidationError) -> JSONResponse:
        error = ApiError(
            schema_version="error.v1",
            code="invalid_input",
            message="request does not match the API contract",
            retryable=False,
            outcome_unknown=False,
            run_id=None,
        )
        return JSONResponse(status_code=422, content=error.model_dump(mode="json"))

    @app.exception_handler(SQLAlchemyError)
    async def database_error(request: Request, exc: SQLAlchemyError) -> JSONResponse:
        error = ApiError(
            schema_version="error.v1",
            code="provider_error",
            message="database operation unavailable",
            retryable=True,
            outcome_unknown=False,
            run_id=None,
        )
        return JSONResponse(status_code=503, content=error.model_dump(mode="json"))

    @app.get("/api/health")
    def health() -> dict[str, Any]:
        from sqlalchemy import text

        with database.transaction() as session:
            session.execute(text("SELECT 1"))
        return {"status": "ready", "model_mode": settings.model_mode, "access": "local_single_user"}

    @app.get("/api/v1/spaces")
    def list_spaces() -> list[dict[str, Any]]:
        return service.list_spaces()

    @app.post("/api/v1/spaces", status_code=201)
    def new_space(request: CreateSpace) -> dict[str, Any]:
        return service.create_space(request.domain, request.user_brief)

    @app.get("/api/v1/spaces/{space_id}")
    def get_space(space_id: str) -> dict[str, Any]:
        return service.get_space(space_id)

    @app.post("/api/v1/runs", response_model=RunSnapshot, status_code=202)
    def new_run(
        request: CreateRun, idempotency_key: Annotated[str, Header(alias="Idempotency-Key")]
    ) -> RunSnapshot:
        return service.create_run(request, idempotency_key)

    @app.get("/api/v1/runs/{run_id}", response_model=RunSnapshot)
    def get_run(run_id: str) -> RunSnapshot:
        return service.get_run(run_id)

    @app.post("/api/v1/runs/{run_id}/cancel", response_model=RunSnapshot)
    def cancel_run(run_id: str) -> RunSnapshot:
        return service.cancel(run_id)

    @app.get("/api/v1/runs/{run_id}/trace")
    def trace_run(run_id: str) -> dict[str, Any]:
        return service.trace(run_id)

    @app.get("/api/v1/runs/{run_id}/events")
    async def events(
        run_id: str,
        request: Request,
        last_event_id: Annotated[int, Header(alias="Last-Event-ID")] = 0,
    ) -> StreamingResponse:
        if last_event_id < 0:
            raise ServiceError("invalid_input", "event cursor cannot be negative")
        await asyncio.to_thread(service.get_run, run_id)

        async def stream() -> AsyncIterator[str]:
            cursor = last_event_id
            while not await request.is_disconnected():
                rows = await asyncio.to_thread(service.events, run_id, cursor)
                for row in rows:
                    cursor = row["sequence"]
                    yield f"id: {cursor}\ndata: {json.dumps(row, ensure_ascii=False)}\n\n"
                state = await asyncio.to_thread(service.get_run, run_id)
                if state.status not in ("queued", "running"):
                    return
                yield ": keepalive\n\n"
                await asyncio.sleep(0.5)

        return StreamingResponse(
            stream(),
            media_type="text/event-stream",
            headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"},
        )

    return app
