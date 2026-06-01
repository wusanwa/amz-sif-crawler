from __future__ import annotations

import os
import shutil
import tempfile
from contextlib import asynccontextmanager
from pathlib import Path

from playwright.async_api import BrowserContext, async_playwright


COMMON_BROWSER_ARGS = [
    "--disable-blink-features=AutomationControlled",
    "--no-sandbox",
    "--disable-dev-shm-usage",
    "--disable-gpu",
    "--disable-setuid-sandbox",
]


@asynccontextmanager
async def open_persistent_context(
    *,
    profile_dir: str | Path,
    headless: bool,
    user_agent: str,
    viewport: dict[str, int],
    extra_args: list[str] | None = None,
):
    playwright = await async_playwright().start()
    context: BrowserContext | None = None
    try:
        context = await playwright.chromium.launch_persistent_context(
            user_data_dir=str(profile_dir),
            headless=headless,
            user_agent=user_agent,
            viewport=viewport,
            args=(extra_args or COMMON_BROWSER_ARGS),
        )
        yield context
    finally:
        if context is not None:
            await context.close()
        await playwright.stop()


def summarize_browser_error(exc: Exception) -> str:
    message = str(exc or "").strip()
    if not message:
        return "Browser launch failed"
    first_line = message.splitlines()[0].strip()
    if "Target page, context or browser has been closed" in message:
        return "Browser launch failed: Chromium exited immediately"
    return first_line[:300]


def clone_profile_dir(profile_dir: str | Path, prefix: str) -> str:
    source = Path(profile_dir)
    target = Path(tempfile.mkdtemp(prefix=prefix, dir="/tmp"))
    if not source.exists():
        return str(target)

    ignored_names = {
        "SingletonLock",
        "SingletonSocket",
        "SingletonCookie",
        "LOCK",
        "lockfile",
        "Cache",
        "Cache_Data",
        "Code Cache",
        "DawnCache",
        "GPUCache",
        "GrShaderCache",
        "ShaderCache",
        "Session Storage",
        "blob_storage",
        "shared_proto_db",
        "Safe Browsing",
    }
    ignored_suffixes = (".lock", ".log")
    ignored_exact_files = {"LOG", "LOG.old", "LOCK", "Cookies-journal"}

    def _ignore(_src: str, names: list[str]) -> set[str]:
        skipped: set[str] = set()
        for name in names:
            if name in ignored_names or name in ignored_exact_files or name.endswith(ignored_suffixes):
                skipped.add(name)
        return skipped

    for child in source.iterdir():
        if child.name in ignored_names or child.name in ignored_exact_files or child.name.endswith(ignored_suffixes):
            continue
        destination = target / child.name
        try:
            if child.is_dir():
                shutil.copytree(child, destination, symlinks=True, dirs_exist_ok=True, ignore=_ignore)
            else:
                shutil.copy2(child, destination)
        except (PermissionError, OSError, shutil.Error):
            # Browser profile caches often contain locked or root-owned files.
            # Best-effort cloning is enough for reusing login state.
            continue
    return str(target)
