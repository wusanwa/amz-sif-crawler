import inspect
import os

from crawl4ai import BrowserConfig


DEFAULT_SIF_BROWSER_CANDIDATES = [
    os.getenv("SIF_BROWSER_PATH", "").strip(),
    "/usr/bin/chromium",
    "/usr/bin/chromium-browser",
    "/usr/bin/google-chrome",
    "/usr/bin/google-chrome-stable",
]


def resolve_sif_browser_path() -> str:
    for candidate in DEFAULT_SIF_BROWSER_CANDIDATES:
        if candidate and os.path.exists(candidate):
            return candidate
    return ""


def build_sif_browser_config(
    *,
    profile_dir: str,
    headless: bool,
    extra_args=None,
    user_agent=None,
    viewport=None,
) -> BrowserConfig:
    browser_path = resolve_sif_browser_path()
    kwargs = {
        "browser_type": "chromium",
        "headless": headless,
        "use_persistent_context": True,
        "user_data_dir": profile_dir,
        "extra_args": extra_args,
    }
    if user_agent is not None:
        kwargs["user_agent"] = user_agent
    if viewport is not None:
        kwargs["viewport"] = viewport

    try:
        sig = inspect.signature(BrowserConfig)
        if browser_path and "executable_path" in sig.parameters:
            kwargs["executable_path"] = browser_path
        elif browser_path and "browser_executable_path" in sig.parameters:
            kwargs["browser_executable_path"] = browser_path
    except Exception:
        pass

    return BrowserConfig(**kwargs)
