FROM python:3.12-slim

# No git: here the data is a database and it carries its own history inside
# (rule_versions, written by triggers). sqlite3 is in for the one legitimate
# way out — root on the host opening the file by hand when a key is lost.
RUN apt-get update \
 && apt-get install -y --no-install-recommends sqlite3 \
 && rm -rf /var/lib/apt/lists/*

WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

# Explicit, never COPY *.py: with a wildcard the three test files end up inside
# the image, and an image should carry what it runs and nothing else.
# `codifier-icon.png` is here for the POST, not for the MCP surface: the
# surface serves it from a raw GitHub URL and never needed the file. mail.py
# embeds it in every message, because a linked image is blocked by default in
# most clients and a broken placeholder is worse than no logo.
COPY rules.py server.py web.py mail.py preflight.py entrypoint.sh \
     reference-guide.md reference-guide-admin.md codifier-icon.png ./
RUN chmod +x entrypoint.sh

# OAuth store (tokens, registrations) on a persistent volume: it survives
# container recreation. Encryption is derived from JWT_SIGNING_KEY.
ENV FASTMCP_HOME=/data/fastmcp

# Unbuffered stdout. Without it Python buffers when it is not writing to a
# terminal, and the log lines arrive in bursts — or, at a hard stop, not at
# all. Half an hour once went into looking for lines that had not been lost,
# only delayed.
ENV PYTHONUNBUFFERED=1

# Quiet FastMCP down. These are read when fastmcp is IMPORTED, so they must be
# in the environment before the process starts — setting them inside server.py
# would arrive too late. Verified against fastmcp 3.4.5.
#   banner        the ASCII art and the commercial pointer
#   rich logging  the boxed, source-annotated lines
#   update check  an OUTBOUND network call at every boot, asking what the
#                 latest version is, on a service that pins its version on
#                 purpose. This one is not noise, it is traffic
#   log level     fastmcp's own logger; ours follows LOG_LEVEL
ENV FASTMCP_SHOW_SERVER_BANNER=false
ENV FASTMCP_ENABLE_RICH_LOGGING=false
ENV FASTMCP_CHECK_FOR_UPDATES=off
ENV FASTMCP_LOG_LEVEL=WARNING

# The database mounts on /db, state (tokens) on /data.
# The process stays ROOT and the database files are 0644 — see entrypoint.sh.
# No EXPOSE, and it says nothing about reachability either way: the MCP port
# is served by the Funnel inside the container, and the administration UI's
# port is published by the template's port mapping. EXPOSE would only be a
# second place for a number that already lives in web.py.
CMD ["/bin/sh", "/app/entrypoint.sh"]
