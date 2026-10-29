# syntax=docker/dockerfile:1
# Multi-stage build, non-root user.
#
# Base fixada por digest de **lista de manifestos** (cobre linux/amd64 e
# linux/arm64, as duas plataformas publicadas). A tag `python:3.12-slim` flutua:
# com ela, duas execucoes do mesmo commit podiam produzir imagens diferentes.
# Fixar o digest torna o artefato reproduzivel — e nao corrige CVE por si so.
#
# Atualizacao da base: `docker buildx imagetools inspect python:3.12-slim`
# devolve o digest novo; troque-o aqui e reconstrua. Ver docs/operations.md.
#
# Base escolhida deliberadamente: Debian 13 (trixie). Medido em 2026-10-29 com
# trivy, sem correcao disponivel em nenhuma das duas:
#   python:3.12-slim          (Debian 13.7) -> 44 HIGH,  0 CRITICAL
#   python:3.12-slim-bookworm (Debian 12.15) -> 59 HIGH, 2 CRITICAL
FROM python:3.12-slim@sha256:aa943ce411423c7582288f18707ff66eddcbaeb510502d747a72aeb34f30d159 AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build

COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# ---
FROM python:3.12-slim@sha256:aa943ce411423c7582288f18707ff66eddcbaeb510502d747a72aeb34f30d159 AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN groupadd -r app && useradd -r -g app app

WORKDIR /app
ENV PYTHONPATH=/app

COPY --from=builder /usr/local/lib/python3.12/site-packages /usr/local/lib/python3.12/site-packages
COPY --from=builder /usr/local/bin /usr/local/bin

COPY src ./src
COPY migrations ./migrations
COPY alembic.ini .

RUN chown -R app:app /app /tmp

USER app

HEALTHCHECK --interval=30s --timeout=5s --start-period=10s --retries=3 \
    CMD find /tmp/worker-heartbeat -mmin -1 | grep -q .

CMD ["python", "-m", "src.worker.main"]
