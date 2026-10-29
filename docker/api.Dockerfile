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
COPY scripts ./scripts
COPY alembic.ini .
COPY docker/entrypoint.sh /app/entrypoint.sh

RUN chmod +x /app/entrypoint.sh && chown -R app:app /app

USER app

EXPOSE 8000
# Healthcheck sem `curl`: o pacote existia somente para esta linha e
# respondia por 4 dos achados HIGH do scan da imagem (curl + libcurl4t64).
# Python ja esta na imagem e cobre o mesmo caso.
HEALTHCHECK --interval=10s --timeout=5s --start-period=60s --retries=3 \
    CMD ["python", "-c", "import os,urllib.request,sys;\
sys.exit(0) if urllib.request.urlopen('http://127.0.0.1:'+os.environ.get('PORT','8000')+'/healthz', timeout=4).status==200 else sys.exit(1)"]

ENTRYPOINT ["/app/entrypoint.sh"]
