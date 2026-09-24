# Periscope — daily collector (default) or web panel (periscope-web service)
FROM python:3.11-slim

WORKDIR /app

# Install uv for faster dependency management
COPY --from=ghcr.io/astral-sh/uv:latest /uv /usr/local/bin/uv

# Copy project files
COPY pyproject.toml uv.lock README.md ./
COPY src ./src
COPY data ./data
COPY profiles ./profiles
COPY .env.example .env.example

# Install optional extras from a comma-separated list, e.g. "trafilatura,openbb".
ARG EXTRAS=""
RUN set -eu; \
    set --; \
    if [ -n "$EXTRAS" ]; then \
        case "$EXTRAS" in ,*|*,|*,,*) echo "Invalid EXTRAS list: $EXTRAS" >&2; exit 2 ;; esac; \
        old_ifs=$IFS; IFS=,; \
        for extra in $EXTRAS; do \
            case "$extra" in [._-]*|*[._-]|*[!A-Za-z0-9._-]*) echo "Invalid extra: $extra" >&2; exit 2 ;; esac; \
            set -- "$@" --extra "$extra"; \
        done; \
        IFS=$old_ifs; \
    fi; \
    uv sync --frozen --no-dev "$@"

# Runtime data (corpus.db, llm_cache.db, config.json) is mounted here;
# keep the image and process unprivileged.
RUN useradd --create-home --uid 10001 periscope \
    && mkdir -p /app/data \
    && chown -R periscope:periscope /app

ENV PYTHONUNBUFFERED=1
USER periscope

VOLUME ["/app/data"]

# Default: one collection pass (cron-friendly). Override the command for the
# panel service (see docker-compose.yml).
ENTRYPOINT ["uv", "run", "periscope"]
CMD []
