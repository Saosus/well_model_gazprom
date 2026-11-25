FROM swaggerapi/swagger-ui:v5.17.14 AS swagger_assets
RUN set -eu; cd /usr/share/nginx/html; mkdir -p /tmp/wmb-swagger; \
    cp swagger-ui-bundle.js swagger-ui.css /tmp/wmb-swagger/; \
    for file in LICENSE* NOTICE* swagger-ui-bundle.js.LICENSE*; do \
        if [ -f "$file" ]; then cp "$file" /tmp/wmb-swagger/; fi; \
    done

FROM python:3.12.14-slim-bookworm AS base
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1 PIP_DISABLE_PIP_VERSION_CHECK=1
WORKDIR /app
RUN groupadd --system --gid 10001 app \
    && useradd --system --uid 10001 --gid app --create-home --shell /usr/sbin/nologin app
ENV HOME=/home/app
HEALTHCHECK --interval=5s --timeout=3s --start-period=5s --retries=10 CMD ["python", "-c", "import os, urllib.request; urllib.request.urlopen(os.environ['HEALTH_URL'], timeout=2).close()"]

FROM base AS api
COPY requirements.lock ./
RUN python -m pip install --no-cache-dir --no-deps -r requirements.lock && python -m pip check
COPY shared/ ./shared/
COPY services/ ./services/
COPY scripts/ ./scripts/
COPY examples/ ./examples/
COPY test_data/ ./test_data/
COPY --from=swagger_assets /tmp/wmb-swagger/ ./shared/swagger/
USER app

FROM api AS tests
HEALTHCHECK NONE
USER root
COPY requirements-dev.lock ./
RUN python -m pip install --no-cache-dir --no-deps -r requirements-dev.lock && python -m pip check
COPY README.md ./
COPY tests/ ./tests/
USER app
CMD ["python", "-m", "pytest", "-q", "-p", "no:cacheprovider"]

# Production edge: static dashboard + reverse proxy for the internal APIs.
FROM nginx:1.28-alpine AS nginx
COPY frontend/ /usr/share/nginx/html/
COPY deploy/nginx/default.conf /etc/nginx/conf.d/default.conf
