FROM node:22-bookworm-slim
RUN apt-get update && apt-get install -y --no-install-recommends python3 python3-venv \
    && rm -rf /var/lib/apt/lists/*
RUN python3 -m venv /opt/py && /opt/py/bin/pip install --no-cache-dir \
    fastapi==0.142.1 pydantic==2.13.5 httpx==0.28.1 pytest==9.1.1 \
    ruff==0.16.9 jsonschema==4.23.0
WORKDIR /opt/js
RUN npm init -y && npm install --ignore-scripts --save-exact \
    next@16.3.7 react@19.3.0 react-dom@19.3.0 typescript@5.9.3 \
    @types/react@19.1.17 @types/react-dom@19.1.9 @types/node@22.18.12
ENV PATH="/opt/py/bin:${PATH}" PYTHONDONTWRITEBYTECODE=1 NEXT_TELEMETRY_DISABLED=1
WORKDIR /tmp
USER 65534:65534
