FROM node:22-slim AS frontend
WORKDIR /src
COPY frontend/package.json frontend/pnpm-lock.yaml frontend/pnpm-workspace.yaml frontend/tsconfig.json frontend/vite.config.ts frontend/index.html ./
COPY frontend/src ./src
RUN corepack enable && corepack prepare pnpm@11.19.0 --activate && pnpm install --frozen-lockfile && pnpm run build

FROM python:3.12-slim
RUN apt-get update && apt-get install -y --no-install-recommends libreoffice-impress poppler-utils fonts-dejavu-core && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY pyproject.toml ./
COPY backend ./backend
RUN pip install --no-cache-dir .
COPY config ./config
COPY prompts ./prompts
COPY skills ./skills
COPY agents ./agents
COPY resources ./resources
COPY --from=frontend /src/dist ./frontend/dist
CMD ["uvicorn", "vktech.api:app", "--host", "0.0.0.0", "--port", "8000"]
