from typing import Any

from fastapi import FastAPI, HTTPException
from pydantic import BaseModel, Field

from .browser_daemon import GenericBrowserDaemonManager
from .providers import list_supported_providers


class CaptureRequest(BaseModel):
    provider: str = Field(..., description="amazon or sif")
    url: str = Field(..., description="Target page URL")
    wait_until: str = Field(default="load")
    capture_network: bool = Field(default=True)
    idle_ms: int = Field(default=5000, ge=0, le=60000)


class ProviderRequest(BaseModel):
    provider: str = Field(..., description="amazon or sif")


class SifQueryRequest(BaseModel):
    asin: str = Field(..., description="Amazon ASIN used for SIF reverse lookup")
    capture_network: bool = Field(default=False)
    idle_ms: int = Field(default=2500, ge=0, le=60000)


class AmazonExtractRequest(BaseModel):
    url: str = Field(..., description="Amazon product URL")
    wait_until: str = Field(default="domcontentloaded")
    idle_ms: int = Field(default=1500, ge=0, le=60000)


def build_app() -> FastAPI:
    app = FastAPI(title="Generic Amazon/SIF Browser Daemon")
    manager = GenericBrowserDaemonManager()

    @app.get("/health")
    async def health() -> dict[str, Any]:
        return {
            "status": "ok",
            "providers": list_supported_providers(),
            "services": manager.list_status(),
        }

    @app.get("/daemon/status")
    async def daemon_status() -> dict[str, Any]:
        return manager.list_status()

    @app.post("/daemon/warmup")
    async def warmup(payload: ProviderRequest) -> dict[str, Any]:
        try:
            service = manager.get_service(payload.provider)
            return await service.warmup()
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/daemon/capture")
    async def capture(payload: CaptureRequest) -> dict[str, Any]:
        try:
            service = manager.get_service(payload.provider)
            return await service.open_and_capture(
                url=payload.url,
                wait_until=payload.wait_until,
                capture_network=payload.capture_network,
                idle_ms=payload.idle_ms,
            )
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/daemon/sif/query")
    async def sif_query(payload: SifQueryRequest) -> dict[str, Any]:
        try:
            service = manager.get_service("sif")
            return await service.fetch_sif_keywords(
                asin=payload.asin,
                capture_network=payload.capture_network,
                idle_ms=payload.idle_ms,
            )
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/daemon/amazon/extract")
    async def amazon_extract(payload: AmazonExtractRequest) -> dict[str, Any]:
        try:
            service = manager.get_service("amazon")
            return await service.fetch_amazon_product(
                url=payload.url,
                wait_until=payload.wait_until,
                idle_ms=payload.idle_ms,
            )
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    @app.post("/daemon/stop")
    async def stop(payload: ProviderRequest) -> dict[str, Any]:
        try:
            service = manager.get_service(payload.provider)
            return await service.stop()
        except Exception as exc:
            raise HTTPException(status_code=400, detail=str(exc)) from exc

    return app
