# Optional container image for self-hosting. Smithery distributes this server as an
# MCPB bundle and does not build this file.
FROM ghcr.io/astral-sh/uv:python3.12-bookworm-slim AS uv

WORKDIR /app

COPY pyproject.toml README.md /app/
COPY ./src/mcp_domain_availability /app/src/mcp_domain_availability

RUN --mount=type=cache,target=/root/.cache/uv \
    uv sync --no-dev --no-editable

FROM python:3.12-slim-bookworm

WORKDIR /app

COPY --from=uv /app/.venv /app/.venv
ENV PATH="/app/.venv/bin:$PATH"

# Serve over HTTP in a container; set MCP_TRANSPORT=stdio and run with -i for stdio.
ENV MCP_TRANSPORT=streamable-http
ENV HOST=0.0.0.0
ENV PORT=8080
EXPOSE 8080

ENTRYPOINT ["mcp-domain-availability"]
