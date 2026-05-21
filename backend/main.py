from fastapi import FastAPI
from fastapi.middleware.cors import CORSMiddleware

from backend.routers import (
    appointments,
    campaigns,
    conversations,
    doctors,
    health,
    memory,
    metrics,
    patients,
    slots,
    traces,
    voice,
)
from config import get_settings


def create_app() -> FastAPI:
    app = FastAPI(title="2careAi Backend", version="0.1.0")
    # CORS — the static voice page at docs/voice/ runs on a different port
    # (5173/8766/etc.) and needs to POST to /voice/chat. We keep this loose
    # in dev (allow_origins=["*"]) but lock it down to your deployed domain
    # before shipping to prod.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=["*"],
        allow_credentials=False,
        allow_methods=["GET", "POST", "PATCH", "DELETE", "OPTIONS"],
        allow_headers=["*"],
    )
    app.include_router(doctors.router)
    app.include_router(patients.router)
    app.include_router(slots.router)
    app.include_router(appointments.router)
    app.include_router(conversations.router)
    app.include_router(memory.router)
    app.include_router(campaigns.router)
    app.include_router(metrics.router)
    app.include_router(traces.router)
    app.include_router(health.router)
    app.include_router(voice.router)

    # Legacy alias for k8s probes that hit /health by convention. The
    # detailed compound check lives at /healthz.
    @app.get("/health")
    async def health_legacy() -> dict[str, str]:
        return {"status": "ok"}

    return app


app = create_app()


def main() -> None:
    import uvicorn

    settings = get_settings()
    uvicorn.run(
        "backend.main:app",
        host=settings.backend_host,
        port=settings.backend_port,
        reload=True,
    )


if __name__ == "__main__":
    main()
