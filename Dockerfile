# JUST Assistant — production image for Render.com (Web Service + Docker)
#
# Render: New → Web Service → connect repo → Environment "Docker"
# Set PORT is automatic. Configure env vars in the dashboard (do not bake secrets into the image):
#   OPENAI_API_KEY, REDIS_HOST, REDIS_PORT, REDIS_PASSWORD (if needed),
#   JWT_SECRET (required for real auth), DATABASE_PATH (optional; default ./instance/just_app.db)
#
# Add a Render Redis instance and point REDIS_HOST / REDIS_PORT at it (or use an external Redis URL).

FROM python:3.12-slim-bookworm

WORKDIR /app

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

RUN apt-get update \
    && apt-get install -y --no-install-recommends ca-certificates \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt .
RUN pip install --no-cache-dir --upgrade pip \
    && pip install --no-cache-dir -r requirements.txt

COPY . .

RUN mkdir -p instance logs

# Render sets PORT at runtime (often 10000). Local default 5000.
EXPOSE 10000

# Long timeout helps /query/stream and slow LLM calls. Tune WEB_CONCURRENCY in Render env if needed.
CMD ["/bin/sh", "-c", "exec gunicorn --bind 0.0.0.0:${PORT:-5000} --workers ${WEB_CONCURRENCY:-2} --threads 4 --timeout 180 --access-logfile - --error-logfile - server:app"]
