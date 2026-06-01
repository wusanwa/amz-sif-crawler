from __future__ import annotations

import json
import os
from dataclasses import dataclass
from pathlib import Path


def _env_flag(name: str, default: bool) -> bool:
    raw = os.getenv(name)
    if raw is None:
        return default
    return raw.strip().lower() in {"1", "true", "yes", "on"}


def _detect_default_headless() -> bool:
    if os.getenv("DOCKER_ENV") == "1":
        return True
    if os.getenv("DISPLAY") or os.getenv("WAYLAND_DISPLAY"):
        return False
    return True


@dataclass(slots=True)
class AppConfig:
    base_dir: Path
    runtime_root: Path
    cache_dir: Path
    amazon_profile_dir: Path
    sif_profile_dir: Path
    amazon_daemon_url: str
    sif_daemon_url: str
    cache_enabled: bool
    cache_expiry_sec: int
    debug_mode: bool
    in_docker: bool
    output_file: str
    amazon_headless: bool
    sif_headless: bool


def load_settings(base_dir: Path) -> dict:
    settings_path = base_dir / "config" / "settings.json"
    if not settings_path.exists():
        return {}
    with settings_path.open("r", encoding="utf-8") as handle:
        return json.load(handle)


def load_app_config(base_dir: str | Path | None = None) -> AppConfig:
    root = Path(base_dir or Path(__file__).resolve().parents[3]).resolve()
    settings = load_settings(root)

    runtime_root = Path(os.getenv("APP_RUNTIME_ROOT", root / "runtime_data")).resolve()
    profile_root = Path(os.getenv("PROFILE_ROOT_DIR", runtime_root / "profiles")).resolve()
    cache_dir = Path(os.getenv("CACHE_DIR", runtime_root / "cache_db")).resolve()
    default_headless = _detect_default_headless()

    return AppConfig(
        base_dir=root,
        runtime_root=runtime_root,
        cache_dir=cache_dir,
        amazon_profile_dir=Path(os.getenv("AMAZON_PROFILE_DIR", profile_root / "amazon")).resolve(),
        sif_profile_dir=Path(os.getenv("SIF_PROFILE_DIR", profile_root / "sif")).resolve(),
        amazon_daemon_url=str(os.getenv("AMAZON_DAEMON_URL", "")).strip(),
        sif_daemon_url=str(os.getenv("SIF_DAEMON_URL", "")).strip(),
        cache_enabled=_env_flag("CACHE_ENABLED", bool(settings.get("CACHE_ENABLED", False))),
        cache_expiry_sec=int(os.getenv("CACHE_EXPIRY_SEC", settings.get("CACHE_EXPIRY_SEC", 80000))),
        debug_mode=_env_flag("DEBUG_MODE", bool(settings.get("DEBUG_MODE", False))),
        in_docker=os.getenv("DOCKER_ENV") == "1",
        output_file=str(settings.get("BATCH", {}).get("outfile", "batch_results.jsonl")),
        amazon_headless=_env_flag("AMAZON_HEADLESS", default_headless),
        sif_headless=_env_flag("SIF_HEADLESS", default_headless),
    )


def ensure_runtime_dirs(config: AppConfig) -> None:
    for path in [
        config.runtime_root,
        config.cache_dir,
        config.amazon_profile_dir,
        config.sif_profile_dir,
    ]:
        path.mkdir(parents=True, exist_ok=True)
