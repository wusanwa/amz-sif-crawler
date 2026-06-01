FROM mcr.microsoft.com/playwright/python:v1.52.0-noble

WORKDIR /app

COPY pyproject.toml requirements.txt ./
COPY src ./src
COPY config ./config
COPY mcp_server.py mcp_gateway.py crawler_worker.py sif_login.py daemon_server.py crawl_once.py ./

RUN python -m pip install --upgrade pip setuptools wheel
RUN python -m pip install --no-cache-dir -r requirements.txt

RUN mkdir -p /app/runtime_data/profiles/amazon /app/runtime_data/profiles/sif /app/runtime_data/cache_db

EXPOSE 8000

CMD ["python", "mcp_server.py"]
