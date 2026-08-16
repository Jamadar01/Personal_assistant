# syntax=docker/dockerfile:1

# ---------------------------------------------------------------------------
# Stage 1 - build the React front end. Node is only needed here for the build;
# the runtime needs its own copy for a different reason (see below).
# ---------------------------------------------------------------------------
FROM node:22-bookworm-slim AS web

WORKDIR /web
# Dependencies first, so editing a component does not reinstall 69 packages.
COPY web/package.json web/package-lock.json* ./
RUN npm ci --no-audit --no-fund

COPY web/tsconfig.json web/vite.config.ts web/index.html ./
COPY web/src ./src
RUN npm run build


# ---------------------------------------------------------------------------
# Stage 2 - the image that actually runs.
# ---------------------------------------------------------------------------
FROM python:3.14-slim-bookworm

# Node is needed at *runtime*, not just for the build: the Gmail and Calendar MCP
# servers are npx subprocesses (main.py:161). Copying the binary out of the official
# node image avoids adding an apt repository, and both images are bookworm so the C
# library underneath matches.
COPY --from=node:22-bookworm-slim /usr/local/bin/node /usr/local/bin/node
COPY --from=node:22-bookworm-slim /usr/local/lib/node_modules/npm /usr/local/lib/node_modules/npm
RUN ln -s ../lib/node_modules/npm/bin/npm-cli.js /usr/local/bin/npm \
 && ln -s ../lib/node_modules/npm/bin/npx-cli.js /usr/local/bin/npx

# libstdc++6 is what the node binary links against and the python slim image does not
# necessarily carry it. ca-certificates is for the outbound HTTPS calls to OpenAI,
# Google and Open-Meteo.
RUN apt-get update \
 && apt-get install -y --no-install-recommends libstdc++6 ca-certificates \
 && rm -rf /var/lib/apt/lists/*

# server.py writes the Google credential files under Path.home() at startup, so whoever
# the container runs as needs a real home directory it owns. Running as root would work,
# but these are live OAuth tokens and there is no reason for the process holding them to
# be root as well.
RUN useradd -m -u 1000 user
USER user
ENV HOME=/home/user \
    PATH=/home/user/.local/bin:$PATH
WORKDIR $HOME/app

COPY --chown=user requirements.txt ./
RUN pip install --no-cache-dir --user -r requirements.txt

# Install the two MCP servers now rather than letting npx fetch them on first use. npx
# finds them in ./node_modules and starts them immediately, which turns a ~20s first
# request into a fast one. That matters more here than locally: a Space that has gone to
# sleep pays this cost again every time it wakes up.
RUN npm install --no-save --no-audit --no-fund \
      @gongrzhe/server-gmail-autoauth-mcp \
      @cocal/google-calendar-mcp

# Application code last - it changes most often, so everything above stays cached.
COPY --chown=user main.py server.py ./
COPY --chown=user --from=web /web/dist ./web/dist

# Render injects PORT at runtime and routes traffic to it, overriding this value; the
# default here is only what `docker run` locally gets. server.py reads $PORT and binds
# 0.0.0.0, which is what makes both cases work. Do not reference $PORT anywhere else in
# this file - it does not exist at build time.
ENV PORT=10000
EXPOSE 10000

CMD ["python", "server.py"]
