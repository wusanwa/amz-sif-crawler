import os

import uvicorn

from daemon.http_api import build_app


if __name__ == "__main__":
    host = os.getenv("DAEMON_HOST", "0.0.0.0").strip() or "0.0.0.0"
    port = int(os.getenv("DAEMON_PORT", "8890"))
    uvicorn.run(build_app(), host=host, port=port)
