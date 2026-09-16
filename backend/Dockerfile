FROM python:3.12-slim

WORKDIR /app

COPY backend/requirements.txt .
RUN pip install --no-cache-dir -r requirements.txt

COPY backend/ .

# frontend/index.html is the single source for the UI; Flask serves it
# from /app/static. Build context is the repo root, not ./backend.
COPY frontend/index.html static/index.html

RUN mkdir -p /data

EXPOSE 5000

CMD ["gunicorn", "--bind", "0.0.0.0:5000", "--workers", "1", "--timeout", "60", "app:app"]
