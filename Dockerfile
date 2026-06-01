FROM mcr.microsoft.com/playwright/python:v1.53.0-jammy

WORKDIR /app

COPY pyproject.toml requirements.txt ./
COPY src ./src
COPY config ./config
COPY mcp_server.py mcp_gateway.py crawler_worker.py sif_login.py ./

RUN python -m pip install --upgrade pip setuptools wheel
RUN python -m pip install --no-cache-dir -r requirements.txt

RUN mkdir -p /app/runtime_data/profiles/amazon /app/runtime_data/profiles/sif /app/runtime_data/cache_db

EXPOSE 8000

CMD ["python", "mcp_server.py"]
