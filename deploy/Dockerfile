FROM node:26-slim AS frontend
WORKDIR /build
RUN npm install -g pnpm@12.6.0
COPY frontend/package.json frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml ./
RUN pnpm install --frozen-lockfile
COPY frontend/ ./
RUN pnpm run build

FROM python:3.11-slim-bookworm AS api
ENV PYTHONUNBUFFERED=1 PYTHONDONTWRITEBYTECODE=1 INTICLASS_PROFILE=product INTICLASS_EXTERNAL_WORKER=1 INTICLASS_MODEL_DEVICE=cpu INTICLASS_ANALYSIS_DEVICE=cpu
WORKDIR /app
RUN apt-get update && apt-get install -y --no-install-recommends ffmpeg libglib2.0-0 libgomp1 && rm -rf /var/lib/apt/lists/*
COPY requirements/ /tmp/requirements/
RUN pip install --no-cache-dir -r /tmp/requirements/api.txt
COPY src/ ./src/
COPY deploy_app.py ./deploy_app.py
COPY scripts/start_context.py scripts/run_automatic_analysis.py scripts/run_analysis_tracking.py scripts/product_worker.py scripts/initialize_product.py scripts/migrate_runtime_data.py scripts/prepare_identity_playback.py scripts/container_smoke.py ./scripts/
COPY --from=frontend /build/dist ./frontend/dist/
RUN useradd --uid 10001 --create-home lessontrace && mkdir -p /app/lesson_trace_data && ln -s lesson_trace_data /app/data && chown -R lessontrace:lessontrace /app
USER lessontrace
EXPOSE 8787
CMD ["sh","-c","python scripts/initialize_product.py && python -m uvicorn --factory deploy_app:create_app --host 0.0.0.0 --port 8787 --no-access-log"]

FROM api AS worker
USER root
RUN pip install --no-cache-dir -r /tmp/requirements/cpu.txt
USER lessontrace
CMD ["python","scripts/product_worker.py"]
