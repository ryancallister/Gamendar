FROM python:3.12-slim

WORKDIR /app

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ .

# frontend/ is the single source for the UI (index.html, manifest, icons);
# Flask serves it from /app/static. Build context is the repo root, not ./backend.
COPY frontend/ static/

RUN mkdir -p /data

EXPOSE 5000

CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "1", "--timeout", "60", "app:app"]
