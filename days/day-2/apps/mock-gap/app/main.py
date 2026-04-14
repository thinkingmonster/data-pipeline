"""Mock GAP — FastAPI ingest gateway.

POST /api/gap/ingest     validate + produce to Kafka (Avro wire format)
GET  /healthz            liveness
GET  /readyz             readiness (checks producer started)
GET  /metrics            Prometheus scrape
"""
from __future__ import annotations

import logging
import os
from contextlib import asynccontextmanager

from fastapi import FastAPI, HTTPException, Request
from fastapi.responses import JSONResponse, Response
from prometheus_client import CONTENT_TYPE_LATEST, generate_latest

from .producer import GapProducer

logging.basicConfig(
    level=os.getenv("LOG_LEVEL", "INFO"),
    format="%(asctime)s %(levelname)s %(name)s - %(message)s",
)
log = logging.getLogger("mock-gap")


@asynccontextmanager
async def lifespan(app: FastAPI):
    bootstrap = os.environ["KAFKA_BOOTSTRAP_SERVERS"]
    registry = os.environ["SCHEMA_REGISTRY_URL"]
    producer = GapProducer(bootstrap, registry)
    await producer.start()
    app.state.producer = producer
    app.state.ready = True
    try:
        yield
    finally:
        app.state.ready = False
        await producer.stop()


app = FastAPI(title="Mock GAP", lifespan=lifespan)


@app.get("/healthz")
async def healthz() -> dict[str, str]:
    return {"status": "ok"}


@app.get("/readyz")
async def readyz(request: Request) -> JSONResponse:
    ready = getattr(request.app.state, "ready", False)
    status = 200 if ready else 503
    return JSONResponse({"ready": ready}, status_code=status)


@app.post("/api/gap/ingest")
async def ingest(request: Request) -> dict[str, object]:
    try:
        event = await request.json()
    except Exception as exc:
        raise HTTPException(status_code=400, detail=f"invalid JSON: {exc}") from exc

    if not isinstance(event, dict):
        raise HTTPException(status_code=400, detail="body must be a JSON object")

    try:
        meta = await request.app.state.producer.ingest(event)
    except ValueError as exc:
        # Schema / routing / payload-shape problem: client's fault.
        raise HTTPException(status_code=422, detail=str(exc)) from exc
    except RuntimeError as exc:
        # Downstream (Kafka / Registry) problem.
        log.exception("produce failed")
        raise HTTPException(status_code=502, detail=str(exc)) from exc

    return {"accepted": True, **meta}


@app.get("/metrics")
async def metrics() -> Response:
    return Response(generate_latest(), media_type=CONTENT_TYPE_LATEST)
