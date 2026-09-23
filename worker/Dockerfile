FROM python:3.12-slim
WORKDIR /app

RUN apt-get update && apt-get install -y --no-install-recommends \
    ca-certificates curl \
  && curl -fsSL https://deb.nodesource.com/setup_22.x | bash - \
  && apt-get install -y --no-install-recommends nodejs \
  && npm install -g @anthropic-ai/claude-code \
  && rm -rf /var/lib/apt/lists/*

COPY requirements.txt requirements.lock ./
RUN pip install --no-cache-dir -r requirements.lock

COPY adapters ./adapters
COPY refresh.py main.py technicals.py advisory.py schedule.py doctrine.py ./
COPY test_doctrine.py test_persist_e2e.py ./

# Claude CLI refuses --dangerously-skip-permissions as root; run it as this user.
# Worker process stays root so shared /data SQLite stays writable with the API.
RUN useradd --create-home --uid 1000 --shell /bin/bash tradai \
  && printf '%s\n' '{"hasCompletedOnboarding":true,"hasTrustDialogAccepted":true}' \
       > /home/tradai/.claude.json \
  && chown -R tradai:tradai /home/tradai

ENV TRADAI_DATA_DIR=/data
ENV WORKER_HEARTBEAT_SECONDS=30
ENV WORKER_QUOTE_INTERVAL_SECONDS=300
ENV WORKER_HTTP_PORT=8090
ENV CLAUDE_RUN_AS=tradai
ENV HOME=/root
EXPOSE 8090
CMD ["python", "-u", "main.py"]
