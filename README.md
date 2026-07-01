# Gamendar

A self-hosted team availability scheduler. Users log in and mark which days they're available for each weekly event. Admins manage users, create events, and configure notifications.

**Stack:** Python/Flask · SQLite · Docker · Single-page HTML frontend

---

## Features

- **Weekly events** — admins create events with a date range; the newest event is shown front and centre, older ones are collapsible under "Previous events" (view-only)
- **Per-day availability** — each user selects Available / Unavailable / Maybe for each day via a dropdown
- **Optional notes** — users can attach a short note to any day (e.g. "After 8pm only")
- **Live summary row** — shows how many active users are free each day, updates instantly
- **Roles** — Admin (full access) and User (mark own availability only)
- **JWT authentication** — sessions persist across browser refreshes; tokens are revoked on logout
- **Discord integration** — webhook-based notifications for new events, daily summaries, and all-available alerts
- **Signal integration** — same notifications via signal-cli-rest-api; message templates fully editable from the admin panel
- **Security** — bcrypt passwords, rate-limited login (10 attempts / 15 min), input validation, security headers, startup check for default SECRET_KEY
- Fully self-hosted, no external services required beyond optional Discord/Signal

---

## Quick Start (any Docker host)

### 1. Clone the repo

```bash
git clone https://github.com/ryancallister/gamendar.git
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

The app will be available at **http://localhost:3005** (or whatever `APP_PORT` you set).

### 4. Default admin credentials

```
Username: admin
Password: admin123
```

**Change this immediately** after first login via Admin → Edit user, then deactivate or delete the default admin account.

---

## Unraid Setup

### Option A — Docker Compose (recommended)

1. Install the **Community Applications** plugin if not already installed
2. Install the **Compose Manager Plus** plugin from Community Applications
3. SSH into your Unraid server:

```bash
cd /mnt/user/appdata
git clone https://github.com/ryancallister/gamendar.git
cd gamendar
cp .env.example .env
nano .env   # set SECRET_KEY and APP_PORT
```

4. In the Unraid UI → **Docker Compose Manager** → point it at `/mnt/user/appdata/gamendar/docker-compose.yml` and start it.

### Option B — Manual Docker run

```bash
mkdir -p /mnt/user/appdata/gamendar/data

docker run -d \
  --name gamendar \
  --restart unless-stopped \
  -e SECRET_KEY=your-secret-key-here \
  -e DATABASE_PATH=/data/calendar.db \
  -p 3005:5000 \
  -v /mnt/user/appdata/gamendar/data:/data \
  ghcr.io/ryancallister/gamendar:latest
```

### Unraid tips

| Setting | Recommended value |
|---|---|
| `APP_PORT` | `3005` (or any unused port) |
| Data volume | `/mnt/user/appdata/gamendar/data` |
| Config path | `/mnt/user/appdata/gamendar` |

---

## Updating

```bash
cd /mnt/user/appdata/gamendar
git pull
docker compose up -d --build
```

The SQLite database in `./data/` is preserved across rebuilds.

---

## Environment Variables

| Variable | Default | Description |
|---|---|---|
| `SECRET_KEY` | *(none — required)* | JWT signing secret. App refuses to start with the default value. |
| `APP_PORT` | `3005` | Host port the app listens on |
| `FLASK_DEBUG` | `false` | Enable Flask debug mode (dev only) |
| `DATABASE_PATH` | `/data/calendar.db` | Path to SQLite database inside container |

---

## Discord Integration

Set up in **Admin → Discord Integration**:

1. Create a webhook in your Discord channel: Channel Settings → Integrations → Webhooks → New Webhook → Copy URL
2. Paste the webhook URL, set a daily summary time, enable, save, and click **Send test**

**Automatic messages:**
- 📅 **New event** — posted when an admin creates an event
- 📋 **Daily summary** — posted at the configured time for any event active that day
- 🎉 **Everyone available** — posted once when all active users mark a day as available

**Manual re-sends** available from the event detail page (admin only) via the 🔔 Notify menu.

---

## Signal Integration

Requires a running [signal-cli-rest-api](https://github.com/bbernhard/signal-cli-rest-api) container (default port `8739`).

Set up in **Admin → Signal Integration**:

| Field | Value |
|---|---|
| Signal API URL | `http://YOUR-UNRAID-IP:8739` |
| Sender number | Phone number registered with signal-cli (e.g. `+12345678900`) |
| Recipient | Group ID from `GET /v1/groups/+12345678900`, starts with `group.` |

Same three automatic message types as Discord. Message templates are fully editable from the admin panel — each template supports placeholders (e.g. `{title}`, `{available_names}`, `{summary_date}`) and includes a live preview with sample data. Templates can be reset to defaults at any time.

---

## Project Structure

```
gamendar/
├── backend/
│   ├── routes/
│   │   ├── auth.py           # Login, logout, /me, change-password
│   │   ├── events.py         # CRUD for weekly events
│   │   ├── availability.py   # Per-day availability set/get
│   │   ├── admin.py          # User management
│   │   ├── discord.py        # Discord webhook settings & triggers
│   │   └── signal.py         # Signal settings, templates & triggers
│   ├── static/
│   │   └── index.html        # Full single-page frontend (served by Flask)
│   ├── app.py                # Flask entry point, blueprints, security headers
│   ├── auth_utils.py         # JWT decorators + token blocklist check
│   ├── database.py           # SQLite schema & init
│   ├── discord_service.py    # Discord message builders & send logic
│   ├── signal_service.py     # Signal message builders, templates & send logic
│   ├── Dockerfile
│   └── requirements.txt
├── frontend/
│   └── index.html            # Frontend source (copy of backend/static/index.html)
├── data/                     # SQLite DB — gitignored, created at runtime
├── .github/workflows/
│   └── docker-build.yml      # Builds & pushes single image to ghcr.io on push to main
├── .env.example
├── docker-compose.yml
└── README.md
```

---

## API Reference

All endpoints are under `/api/`. Protected routes require `Authorization: Bearer <token>`.

### Auth

| Method | Path | Auth | Description |
|---|---|---|---|
| `POST` | `/api/auth/login` | — | Login, returns JWT token |
| `POST` | `/api/auth/logout` | User | Revokes current token |
| `GET` | `/api/auth/me` | User | Current user info |
| `POST` | `/api/auth/change-password` | User | Change own password |

### Events

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/api/events/` | User | List all events (newest first) |
| `POST` | `/api/events/` | Admin | Create event |
| `GET` | `/api/events/:id` | User | Event detail + all availability |
| `PUT` | `/api/events/:id` | Admin | Update event |
| `DELETE` | `/api/events/:id` | Admin | Delete event |

### Availability

| Method | Path | Auth | Description |
|---|---|---|---|
| `POST` | `/api/availability/event/:id/set` | User | Set status + note for one day |
| `POST` | `/api/availability/event/:id/bulk` | User | Set status for multiple days |
| `GET` | `/api/availability/event/:id` | User | Get all availability for event |
| `GET` | `/api/availability/my` | User | Current user's availability across all events |

### Admin

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/api/admin/users` | Admin | List all users |
| `POST` | `/api/admin/users` | Admin | Create user |
| `PUT` | `/api/admin/users/:id` | Admin | Update role / status / password |
| `DELETE` | `/api/admin/users/:id` | Admin | Delete user |

### Discord

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/api/discord/settings` | Admin | Get Discord settings |
| `POST` | `/api/discord/settings` | Admin | Save Discord settings |
| `POST` | `/api/discord/test` | Admin | Send test message |
| `POST` | `/api/discord/send/announcement/:id` | Admin | Re-send event announcement |
| `POST` | `/api/discord/send/summary/:id` | Admin | Send today's summary |
| `GET` | `/api/discord/log` | Admin | Last 50 sent messages |

### Signal

| Method | Path | Auth | Description |
|---|---|---|---|
| `GET` | `/api/signal/settings` | Admin | Get Signal settings |
| `POST` | `/api/signal/settings` | Admin | Save Signal settings |
| `POST` | `/api/signal/test` | Admin | Send test message |
| `GET` | `/api/signal/templates` | Admin | Get all message templates |
| `POST` | `/api/signal/templates` | Admin | Save one or more templates |
| `POST` | `/api/signal/templates/:key/reset` | Admin | Reset template to default |
| `POST` | `/api/signal/templates/preview` | Admin | Preview a template with sample data |
| `POST` | `/api/signal/send/announcement/:id` | Admin | Re-send event announcement |
| `POST` | `/api/signal/send/summary/:id` | Admin | Send today's summary |
| `GET` | `/api/signal/log` | Admin | Last 50 sent messages |

---

## GitHub Actions (CI/CD)

Pushing to `main` automatically builds and pushes a single Docker image to GitHub Container Registry. Images are tagged `latest` and by commit SHA.

To use the pre-built image instead of building locally, update `docker-compose.yml`:

```yaml
services:
  gamendar:
    image: ghcr.io/ryancallister/gamendar:latest
```

---

## License

MIT
