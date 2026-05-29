import os
from dataclasses import dataclass
from pathlib import Path


BASE_DIR = Path(__file__).resolve().parent.parent
RUNTIME_ROOT = Path(os.getenv("APP_RUNTIME_ROOT", BASE_DIR / "runtime_data"))
PROFILE_ROOT = Path(os.getenv("PROFILE_ROOT_DIR", RUNTIME_ROOT / "profiles"))
DAEMON_ROOT = Path(os.getenv("GENERIC_DAEMON_ROOT", RUNTIME_ROOT / "daemon"))


def _bool_env(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return str(raw).strip().lower() in {"1", "true", "yes", "on"}


@dataclass(frozen=True)
class ProviderSettings:
    name: str
    profile_dir: Path
    start_url: str
    headless: bool
    user_agent: str
    viewport_width: int
    viewport_height: int
    cdp_host: str = "127.0.0.1"
    cdp_port: int = 0
    prefer_cdp_attach: bool = False


def ensure_runtime_dirs() -> None:
    for path in (RUNTIME_ROOT, PROFILE_ROOT, DAEMON_ROOT):
        path.mkdir(parents=True, exist_ok=True)


def provider_runtime_dir(provider: str) -> Path:
    path = DAEMON_ROOT / provider
    path.mkdir(parents=True, exist_ok=True)
    return path
