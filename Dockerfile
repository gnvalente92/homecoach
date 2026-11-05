# syntax=docker/dockerfile:1
FROM node:24-alpine AS web
WORKDIR /web
COPY web/package.json web/package-lock.json ./
RUN npm ci --no-audit --no-fund
COPY web/ ./
RUN npm run build

FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 HC_WEB_DIR=/app/web HC_DATA_DIR=/data
WORKDIR /app
COPY backend/pyproject.toml backend/constraints.txt ./
COPY backend/src ./src
RUN pip install --no-cache-dir -c constraints.txt .
COPY --from=web /web/dist ./web
RUN useradd -r -u 10001 homecoach && mkdir -p /data && chown homecoach /data
USER homecoach
EXPOSE 8080
HEALTHCHECK --interval=30s --timeout=5s CMD python -c "import urllib.request;urllib.request.urlopen('http://localhost:8080/api/health')" || exit 1
ENTRYPOINT ["homecoach"]
CMD ["api"]
