import os
from pathlib import Path

from .config import PROFILE_ROOT, ProviderSettings
from sif_runtime import resolve_sif_browser_path


DEFAULT_CHROME_CANDIDATES = [
    os.getenv("AMAZON_BROWSER_PATH", "").strip(),
    os.getenv("CHROME_BROWSER_PATH", "").strip(),
    "/usr/bin/google-chrome",
    "/usr/bin/google-chrome-stable",
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
]


def resolve_browser_executable(provider: str) -> str:
    if provider == "sif":
        return resolve_sif_browser_path()
    for candidate in DEFAULT_CHROME_CANDIDATES:
        if candidate and Path(candidate).exists():
            return candidate
    return resolve_sif_browser_path()


def get_provider_settings(provider: str) -> ProviderSettings:
    normalized = str(provider or "").strip().lower()
    if normalized == "sif":
        return ProviderSettings(
            name="sif",
            profile_dir=Path(os.getenv("SIF_PROFILE_DIR", PROFILE_ROOT / "sif")),
            start_url=os.getenv(
                "SIF_START_URL",
                "https://www.sif.com/reverse?country=US&asin=B0CDX5XGLK&isListingSearch=false&trafficType=",
            ),
            headless=str(os.getenv("SIF_HEADLESS", "0")).strip().lower() in {"1", "true", "yes", "on"},
            user_agent=(
                "Mozilla/5.0 (X11; Linux x86_64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/147.0.0.0 Safari/537.36"
            ),
            viewport_width=1600,
            viewport_height=1200,
            cdp_host=os.getenv("SIF_CDP_HOST", "127.0.0.1").strip() or "127.0.0.1",
            cdp_port=int(os.getenv("SIF_CDP_PORT", "9224") or 9224),
            prefer_cdp_attach=True,
        )
    if normalized == "amazon":
        return ProviderSettings(
            name="amazon",
            profile_dir=Path(os.getenv("AMAZON_PROFILE_DIR", PROFILE_ROOT / "amazon")),
            start_url=os.getenv("AMAZON_START_URL", "https://www.amazon.com/"),
            headless=str(os.getenv("AMAZON_HEADLESS", "0")).strip().lower() in {"1", "true", "yes", "on"},
            user_agent=(
                "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                "(KHTML, like Gecko) Chrome/122.0.0.0 Safari/537.36"
            ),
            viewport_width=1440,
            viewport_height=900,
            cdp_host=os.getenv("AMAZON_CDP_HOST", "127.0.0.1").strip() or "127.0.0.1",
            cdp_port=int(os.getenv("AMAZON_CDP_PORT", "9225") or 9225),
            prefer_cdp_attach=True,
        )
    raise ValueError(f"Unsupported provider: {provider}")


def list_supported_providers() -> list[str]:
    return ["amazon", "sif"]
