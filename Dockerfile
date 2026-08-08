FROM python:3.12-slim
# niente git: qui i dati sono un database, la storia ce l'ha dentro (rule_versions).
RUN apt-get update && apt-get install -y --no-install-recommends sqlite3 && rm -rf /var/lib/apt/lists/*
WORKDIR /app
COPY requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt
COPY *.py entrypoint.sh ./
RUN chmod +x entrypoint.sh
# Il database si monta su /db, lo stato (log + token OAuth) su /data.
# Il processo resta root: vedi entrypoint.sh.
ENV FASTMCP_HOME=/data/fastmcp
CMD ["/bin/sh", "/app/entrypoint.sh"]
