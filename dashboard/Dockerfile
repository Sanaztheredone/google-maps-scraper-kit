# syntax=docker/dockerfile:1
FROM python:3.12-slim
ENV PYTHONDONTWRITEBYTECODE=1 PYTHONUNBUFFERED=1
WORKDIR /app
COPY dashboard/requirements.txt /app/dashboard/requirements.txt
RUN --mount=type=secret,id=build_ca \
    if [ -f /run/secrets/build_ca ]; then cp /run/secrets/build_ca /usr/local/share/ca-certificates/build-proxy.crt && update-ca-certificates; fi; \
    PIP_CERT=/etc/ssl/certs/ca-certificates.crt pip install --no-cache-dir -r dashboard/requirements.txt && useradd --uid 10001 --create-home dashboard && mkdir /data && chown dashboard:dashboard /data
COPY --chown=dashboard:dashboard dashboard /app/dashboard
COPY --chown=dashboard:dashboard scripts /app/scripts
USER dashboard
EXPOSE 8090
HEALTHCHECK --interval=20s --timeout=5s --start-period=15s --retries=3 CMD python -c "import urllib.request; urllib.request.urlopen('http://localhost:8090/health',timeout=3)"
CMD ["gunicorn", "--bind", "0.0.0.0:8090", "--workers", "1", "--threads", "4", "--access-logfile", "-", "--timeout", "60", "dashboard.app:production()"]
