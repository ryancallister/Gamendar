# Gamendar

A self-hosted team availability scheduler. Users log in and mark which days they're available for each weekly event. Admins manage users and create events.

**Stack:** Python/Flask · SQLite · Nginx · Docker · Single-page HTML frontend

---

## Features

- **Weekly events** created by admins with a date range
- **Per-day availability** — each user marks days as Available / Unavailable / Maybe
- **Live summary row** showing how many people are free each day
- **Role system** — Admin (full access) and User (mark own availability)
- **JWT authentication** — sessions persist across browser refreshes
- Fully self-hosted, no external services required

---

## Quick Start (any Docker host)

### 1. Clone the repo

```bash
git clone https://github.com/YOUR_USERNAME/gamendar.git
cd gamendar
```

### 2. Configure environment

```bash
cp .env.example .env
```

Edit `.env` and set a strong `SECRET_KEY`:

```bash
# Generate a secure key:
python3 -c "import secrets; print(secrets.token_hex(32))"
```

### 3. Start the app

```bash
docker compose up -d --build
```

The app will be available at **http://localhost:8080** (or whatever `APP_PORT` you set).

### 4. Default admin credentials

```
Username: admin
Password: admin123
```

**Change this immediately** after first login via Admin → Edit user.

---

## Unraid Setup

### Option A — Docker Compose (recommended)

1. Install the **Community Applications** plugin if not already installed
2. Install the **Docker Compose Manager** plugin from Community Applications
3. SSH into your Unraid server:

```bash
cd /mnt/user/appdata
git clone https://github.com/YOUR_USERNAME/gamendar.git
cd gamendar
cp .env.example .env
nano .env   # set SECRET_KEY and APP_PORT
```

4. In the Unraid UI → **Docker Compose Manager** → point it at `/mnt/user/appdata/gamendar/docker-compose.yml` and start it.

### Option B — Manual Docker run (no Compose plugin needed)

Single container — no separate frontend image needed.

```bash
mkdir -p /mnt/user/appdata/gamendar/data

docker run -d \
  --name gamendar \
  --restart unless-stopped \
  -e SECRET_KEY=your-secret-key-here \
  -e DATABASE_PATH=/data/calendar.db \
  -p 3005:5000 \
  -v /mnt/user/appdata/gamendar/data:/data \
  ghcr.io/YOUR_USERNAME/gamendar:latest
```

### Unraid port & data path tips

| Setting | Recommended value |
|---|---|
| `APP_PORT` | `8080` (or any unused port) |
| Data volume | `/mnt/user/appdata/gamendar/data` |
| Config path | `/mnt/user/appdata/gamendar` |

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `SECRET_KEY` | `change-me-in-production` | JWT signing secret — **must be changed** |
| `APP_PORT` | `8080` | Host port the app listens on |
| `FLASK_DEBUG` | `false` | Enable Flask debug mode (dev only) |
| `DATABASE_PATH` | `/data/calendar.db` | Path to SQLite database inside container |

---

## Updating

```bash
git pull
docker compose up -d --build
```

The SQLite database in `./data/` is preserved across rebuilds.

---

## GitHub Actions (CI/CD)

Pushing to `main` automatically builds and pushes images to GitHub Container Registry (`ghcr.io`). Images are tagged with both `latest` and the commit SHA.

To use pre-built images instead of building locally, replace the `build:` keys in `docker-compose.yml` with:

```yaml
services:
  backend:
    image: ghcr.io/YOUR_USERNAME/gamendar-backend:latest
  frontend:
    image: ghcr.io/YOUR_USERNAME/gamendar-frontend:latest
```

---

## Project Structure

```
gamendar/
├── backend/
│   ├── routes/         # auth, events, availability, admin
│   ├── app.py          # Flask entry point
│   ├── auth_utils.py   # JWT decorators
│   ├── database.py     # SQLite schema & helpers
│   ├── Dockerfile
│   └── requirements.txt
├── frontend/
│   ├── index.html      # Full single-page app
│   └── Dockerfile
├── nginx/
│   └── nginx.conf      # Reverse proxy config
├── data/               # SQLite DB (gitignored, created at runtime)
├── .github/workflows/  # Docker build CI
├── .env.example
├── docker-compose.yml
└── README.md
```

---

## API Reference

All endpoints are under `/api/`. Auth endpoints return a JWT Bearer token.

| Method | Path | Auth | Description |
|---|---|---|---|
| `POST` | `/api/auth/login` | — | Login, returns token |
| `POST` | `/api/auth/register` | — | Self-register |
| `GET` | `/api/auth/me` | User | Current user info |
| `POST` | `/api/auth/change-password` | User | Change own password |
| `GET` | `/api/events/` | User | List all events |
| `POST` | `/api/events/` | Admin | Create event |
| `GET` | `/api/events/:id` | User | Event detail + all availability |
| `PUT` | `/api/events/:id` | Admin | Update event |
| `DELETE` | `/api/events/:id` | Admin | Delete event |
| `POST` | `/api/availability/event/:id/set` | User | Set availability for one day |
| `POST` | `/api/availability/event/:id/bulk` | User | Set availability for multiple days |
| `GET` | `/api/admin/users` | Admin | List all users |
| `POST` | `/api/admin/users` | Admin | Create user |
| `PUT` | `/api/admin/users/:id` | Admin | Update role/status/password |
| `DELETE` | `/api/admin/users/:id` | Admin | Delete user |

---

## License

MIT
