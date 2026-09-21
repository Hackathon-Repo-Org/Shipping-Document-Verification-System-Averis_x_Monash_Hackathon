# shipdoc — batch verification of shipping instructions against draft bills of lading.
#
# ONE IMAGE, TWO ROLES
# --------------------
# Phase 14 added the HTTP API, so this image now serves both:
#
#   docker run shipdoc run           -> the BATCH processor (Container Apps JOB)
#   docker run shipdoc serve         -> the HTTP API        (Container APP, ingress)
#
# Same code, same cache, same config; only the entrypoint differs. That is why the
# Phase 13 deployment guide did not have to be thrown away when the API arrived.
#
# WHAT IS DELIBERATELY NOT IN HERE
# --------------------------------
#   * No secrets. Not the API key, not DATABASE_URL, not a .env. Those arrive as
#     environment variables from Azure App Settings / Container Apps secrets at run
#     time. `.dockerignore` excludes `.env` so a developer's copy cannot be baked in
#     by accident, and `infra/dotenv.py` lets a real environment variable win even if
#     one somehow were.
#   * No Ollama and no GPU. The committed cache answers every prompt this corpus
#     asks, so the image reproduces the published score with no model and no network.
#     Set `llm.provider: deepseek` and supply a key to use a hosted model instead.
#   * No answer key. It lives outside the repository and outside the image.

FROM python:3.12-slim AS base

# PYTHONDONTWRITEBYTECODE: a read-only filesystem is a reasonable hardening step and
# .pyc writes would fail noisily. PYTHONUNBUFFERED: without it, logs from a batch job
# appear only when the process exits, which is precisely when you stop needing them.
ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1 \
    PIP_NO_CACHE_DIR=1 \
    PIP_DISABLE_PIP_VERSION_CHECK=1

WORKDIR /app

# Tesseract for the three scanned PDFs. pip cannot install it, so it has to come
# from the OS layer — without it those records are NEEDS_REVIEW, which is the
# correct answer but not the complete one.
RUN apt-get update  && apt-get install -y --no-install-recommends tesseract-ocr poppler-utils  && rm -rf /var/lib/apt/lists/*

# Dependency layer first, so a source edit does not reinstall the world.
COPY pyproject.toml README.md ./
COPY src/ ./src/
RUN pip install --no-cache-dir ".[hosted,db,ocr,api]"

# Everything the run actually reads. `dataset/` carries the organisers' loader.py and
# the corpus; `cache/llm/` is what makes a run reproducible with no model.
COPY config/ ./config/
COPY cache/ ./cache/
COPY dataset/ ./dataset/
COPY alembic/ ./alembic/
COPY alembic.ini ./
# The Evaluation page reads the measured provider comparison from this one file.
# The rest of docs/ stays out of the image (see .dockerignore).
COPY docs/provider-comparison.json ./docs/provider-comparison.json

# Non-root. A container that processes third-party documents should not be root, and
# Azure Container Apps does not require it.
RUN useradd --create-home --uid 10001 shipdoc \
    && mkdir -p /app/output \
    && chown -R shipdoc:shipdoc /app
USER shipdoc

# `doctor --fast` exits non-zero when the install, corpus or config is broken, so it
# is a real check rather than a liveness placebo.
HEALTHCHECK --interval=60s --timeout=30s --start-period=10s --retries=2 \
    CMD ["shipdoc", "doctor", "--fast"]

# `serve` is not a shipdoc subcommand — it is the API. The shim keeps one entrypoint
# so the Container Apps job and the Container App differ by ONE argument.
COPY docker-entrypoint.sh /usr/local/bin/

# The shim is authored on Windows, so it can arrive with CRLF line endings and
# without the executable bit. Either one makes the container exit immediately with
# an exec-format or permission error and no application log. Normalising it here
# means the image starts correctly no matter how the file was checked out.
USER root
RUN sed -i 's/\r$//' /usr/local/bin/docker-entrypoint.sh \
    && chmod 0755 /usr/local/bin/docker-entrypoint.sh
USER shipdoc

ENTRYPOINT ["/usr/local/bin/docker-entrypoint.sh"]

# Default to the HTTP API. The batch job overrides this with `run`.
CMD ["serve"]
