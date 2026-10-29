# syntax=docker/dockerfile:1
# Multi-stage build, non-root user.
#
# Base fixada por digest de **lista de manifestos** (cobre linux/amd64 e
# linux/arm64, as duas plataformas publicadas). A tag `python:3.12-alpine`
# flutua: com ela, duas execucoes do mesmo commit podiam produzir imagens
# diferentes. Fixar o digest torna o artefato reproduzivel.
#
# Atualizacao da base: `docker buildx imagetools inspect python:3.12-alpine`
# devolve o digest novo; troque-o aqui e reconstrua. Ver docs/operations.md.
#
# Base escolhida por medicao, com trivy em 2026-10-29, da imagem final:
#   python:3.12-alpine        (Alpine 3.24.3)  ->  0 HIGH,  0 CRITICAL
#   python:3.12-slim          (Debian 13.7)    -> 44 HIGH,  0 CRITICAL, nenhum com correcao
#   python:3.12-slim-bookworm (Debian 12.15)   -> 59 HIGH,  2 CRITICAL, nenhum com correcao
#   python:3.13-slim          (Debian 13.7)    -> 49 HIGH,  0 CRITICAL
#
# Os 44 HIGH do Debian vinham de util-linux, ncurses, systemd e perl-base, que
# esta aplicacao nao usa, e nenhum tinha correcao publicada. O Alpine entrega o
# mesmo runtime sem esses pacotes, o que permite ao scan da imagem ser
# bloqueante em vez de informativo.
#
# Compatibilidade verificada antes da troca: todas as dependencias nativas
# (psycopg[binary], cryptography, Pillow, bcrypt, uvloop, httptools, grpcio,
# pydantic-core) publicam wheel musllinux para amd64 e arm64; nada e compilado.
FROM python:3.12-alpine@sha256:5789aa194f757888f41fa3d48e14b6feaa1b57c0af0b56fcb1372d6474d3bff0 AS builder

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

WORKDIR /build

COPY requirements.txt .
# Somente wheel: nada e compilado no build, e a falta de um wheel para a
# plataforma falha aqui em vez de arrastar um compilador para a imagem.
RUN pip install --no-cache-dir --only-binary=:all: -r requirements.txt

# ---
FROM python:3.12-alpine@sha256:5789aa194f757888f41fa3d48e14b6feaa1b57c0af0b56fcb1372d6474d3bff0 AS runtime

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1

RUN addgroup -S app && adduser -S -G app -H -D app

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
