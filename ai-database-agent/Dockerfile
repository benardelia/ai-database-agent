FROM python:3.12-slim

ENV PYTHONDONTWRITEBYTECODE=1 \
    PYTHONUNBUFFERED=1

WORKDIR /app

# psycopg[binary] ships a prebuilt libpq, so no build toolchain is needed
# for it -- libpq-dev is still useful to have present for any C-extension
# fallback, but no compiler is required.
RUN apt-get update && apt-get install -y --no-install-recommends \
    libpq-dev \
    && rm -rf /var/lib/apt/lists/*

COPY requirements.txt /app/
RUN pip install --no-cache-dir --upgrade pip && \
    pip install --no-cache-dir -r requirements.txt

# Deliberately NOT copying databases.json or any per-database business
# content (context_*.md / glossary_*.json / metrics_*.json) into the
# image -- those hold real connection strings and real schema/business
# facts and must be mounted in at runtime instead (see docker-compose.yml),
# never baked into an image layer.
COPY src /app/src
COPY scripts /app/scripts
COPY databases.example.json /app/databases.example.json

EXPOSE 8005

CMD ["uvicorn", "dbagent.api.main:app", "--host", "0.0.0.0", "--port", "8005", "--app-dir", "src"]
