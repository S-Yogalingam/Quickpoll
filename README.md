# 🗳️ QuickPoll

**Create polls, share instantly, watch votes roll in.**

A polling web app where anyone can create a poll, share a unique link, and watch
results update **in real time**. No login required to vote. The poll creator gets
a private link to view detailed results and close the poll.

> Create a poll, open the share link in another tab/device, vote, and watch the
> bar chart update live.

---

## Features

### Core
- ✅ Create a poll with a **title**, **2–5 options**, and an optional **expiry time**
- ✅ Each poll gets a **unique shareable URL** (`/p/<id>`)
- ✅ **Anyone with the link can vote** — no login
- ✅ **Results page** with vote counts and a simple **bar chart**
- ✅ Creator gets a **private admin link** (`/p/<id>/admin?token=...`) to view detailed
  results and **close the poll**

### Bonus (all implemented)
- ✅ **Double-vote prevention** — per-poll voter token in an HTTP-only cookie, backed
  by a `UNIQUE(poll_id, voter_token)` DB constraint
- ✅ **Real-time vote updates** without page refresh — via **Server-Sent Events (SSE)**
- ✅ **Poll expiry** — polls auto-close after the set time (enforced server-side on every read/vote)
- ✅ **QR code** for the shareable link (generated server-side, no JS dependencies)

---

## Tech Stack

| Layer     | Tech                                                          |
|-----------|--------------------------------------------------------------|
| Language  | **Python 3**                                                 |
| Backend   | **Flask** (sync, threaded) · raw SQL · SSE                   |
| Database  | **MySQL 8** (`mysql-connector-python`)                       |
| Frontend  | **Server-rendered HTML** (Jinja2) · **Bootstrap 5** + custom CSS · **zero JavaScript** |
| Real-time | **`<meta http-equiv="refresh">`** auto-reload on results pages (no JS) · in-process SSE broker still backs the JSON API |

The single Flask process serves both the REST API **and** the server-rendered
pages, so the whole app is one shareable link. No Node.js, no bundler, no
client-side framework.

---

## Data Model

```
Poll (id, admin_token, title, created_at, expires_at, closed)
  │
  ├──1:N──> Option (id, poll_id, text, position)
  │
  └──1:N──> Vote (id, poll_id, option_id, voter_token, created_at)
                                          └─ UNIQUE(poll_id, voter_token)
```

- IDs are UUID hex strings stored as `CHAR(36)`.
- **Poll** — `admin_token` is a secret only the creator holds (their private link).
  `expires_at` is null for no-expiry polls. `closed` is set when the creator closes it.
- **Option** — belongs to a poll, ordered by `position`.
- **Vote** — one row per cast vote. The `UNIQUE(poll_id, voter_token)` constraint
  enforces one vote per voter per poll at the database level (defence in depth on top
  of the cookie check).

See [`schema.sql`](schema.sql) for the full DDL.

---

## API

Base path: `/api`

| Method | Endpoint                          | Description                              | Codes |
|--------|-----------------------------------|------------------------------------------|-------|
| GET    | `/api/health`                     | Health check                             | `200` |
| POST   | `/api/polls`                      | Create a poll → returns poll + `admin_token` | `201`, `422` |
| GET    | `/api/polls/<id>`                 | Public poll + results (sets voter cookie) | `200`, `404` |
| POST   | `/api/polls/<id>/vote`            | Cast a vote `{ "option_id": "..." }`     | `200`, `400`, `409`, `404` |
| GET    | `/api/polls/<id>/stream`          | **SSE** live results stream              | `200`, `404` |
| GET    | `/api/polls/<id>/admin?token=...` | Creator detail view                      | `200`, `403`, `404` |
| POST   | `/api/polls/<id>/close?token=...` | Close the poll (creator only)            | `200`, `403`, `404` |
| GET    | `/api/qr?url=...`                 | Server-rendered QR code (SVG data URL)   | `200`, `400` |

**Status code semantics**
- `201` poll created · `200` ok · `422` validation error (bad title / wrong option count)
- `400` invalid option for this poll
- `409` already voted **or** poll is closed/expired
- `403` invalid admin token · `404` poll not found

Error bodies use the shape `{"detail": "..."}`.

### Example

```bash
# Create a poll
curl -X POST http://localhost:8090/api/polls \
  -H 'Content-Type: application/json' \
  -d '{"title":"Best language?","options":["Python","Rust","Go"],"expires_in_minutes":60}'

# Vote (use -c/-b to persist the voter cookie -> dedupe works)
curl -c jar.txt -X POST http://localhost:8090/api/polls/<ID>/vote \
  -H 'Content-Type: application/json' -d '{"option_id":"<OPTION_ID>"}'

# Live stream (SSE)
curl -N http://localhost:8090/api/polls/<ID>/stream

# Close as creator
curl -X POST "http://localhost:8090/api/polls/<ID>/close?token=<ADMIN_TOKEN>"
```

### Pages (server-rendered, no JavaScript)

The browser-facing UI is pure HTML/CSS — all interactions are standard `<form>`
submits that the server handles and then redirects (Post/Redirect/Get).

| Method | Route                  | Page / action                                   |
|--------|------------------------|-------------------------------------------------|
| GET    | `/`                    | Create-a-poll form                              |
| POST   | `/`                    | Create the poll → redirect to the success page  |
| GET    | `/p/<id>/created`      | Success page: share links + QR (creator only)   |
| GET    | `/p/<id>`              | Public voting form **or** live results          |
| POST   | `/p/<id>/vote`         | Cast a vote → redirect back to the poll          |
| GET    | `/p/<id>/admin?token=` | Creator dashboard (results + close)             |
| POST   | `/p/<id>/close`        | Close the poll → redirect back to the dashboard |

**Live results without JavaScript:** while a poll is open, the results and admin
pages include a `<meta http-equiv="refresh" content="5">` tag, so the browser
reloads the page every 5 seconds and re-fetches fresh counts server-side. Once a
poll is closed/expired the refresh tag is omitted (final results are static).

---

## Local Setup

### Prerequisites
- Python 3.10+
- MySQL 8 running locally

### 1. Create the MySQL database and user

```sql
CREATE DATABASE quickpoll CHARACTER SET utf8mb4 COLLATE utf8mb4_unicode_ci;
CREATE USER 'quickpoll'@'localhost' IDENTIFIED BY 'quickpoll_pw';
GRANT ALL PRIVILEGES ON quickpoll.* TO 'quickpoll'@'localhost';
FLUSH PRIVILEGES;
```

> On Ubuntu/Debian the `root` MySQL user uses the `auth_socket` plugin (TCP logins
> fail), so QuickPoll uses a dedicated password-authenticated `quickpoll` user.

### 2. Configure (optional — defaults match the user above)

```bash
cp .env.example .env   # then edit MYSQL_* if needed
```

### 3. Run

```bash
./run.sh
```

`run.sh` creates the venv (first run), installs deps, ensures the schema exists,
then starts the server. Open **http://localhost:8090**.

### Manual run

```bash
python3 -m venv venv
./venv/bin/pip install -r requirements.txt
./venv/bin/python init_db.py        # create tables
./venv/bin/python app.py            # start the server
```

---

## Configuration (`.env`)

See [`.env.example`](.env.example). All values have sane defaults.

| Variable               | Default      | Description                       |
|------------------------|--------------|-----------------------------------|
| `PORT`                 | `8090`       | Port the server listens on        |
| `HOST`                 | `0.0.0.0`    | Bind address                      |
| `QUICKPOLL_SECRET_KEY` | dev value    | Flask secret key                  |
| `MYSQL_HOST`           | `127.0.0.1`  | MySQL host                        |
| `MYSQL_PORT`           | `3306`       | MySQL port                        |
| `MYSQL_USER`           | `quickpoll`  | MySQL user                        |
| `MYSQL_PASSWORD`       | `quickpoll_pw` | MySQL password                  |
| `MYSQL_DB`             | `quickpoll`  | MySQL database name               |

---

## How it works

- **Sharing:** Creating a poll returns its `id` (public link `/p/<id>`) and a secret
  `admin_token` (private link `/p/<id>/admin?token=...`). The admin token is also saved
  in the creator's `localStorage` as a convenience.
- **Voting & dedupe:** On first contact the server sets an HTTP-only `qp_voter` cookie
  with a random token. A second vote with the same token → `409`. The DB `UNIQUE`
  constraint guarantees it even under a race.
- **Real-time:** The results and admin pages carry a `<meta http-equiv="refresh">`
  tag while the poll is open, so the browser reloads every few seconds and the
  server re-renders fresh counts — live updates with **zero client-side JavaScript**.
  (The JSON API still has a real SSE `/stream` endpoint backed by an in-process
  pub/sub broker for any programmatic consumer that wants push updates.)
- **Expiry:** `expires_at` is checked on every read and vote. An expired poll reports
  `is_open=false` and rejects new votes.

---

## Project layout

```
quickpoll/
├── app.py            # Flask app: REST API + SSE + server-rendered pages
├── config.py         # Env-driven config (port, MySQL, cookies)
├── database.py       # MySQL connection helper (get_cursor context manager)
├── broker.py         # In-process thread-based SSE pub/sub
├── schema.sql        # MySQL DDL (polls, options, votes)
├── init_db.py        # Creates the schema
├── requirements.txt
├── run.sh            # Create venv, install, init schema, run
├── .env.example
├── templates/
│   ├── base.html        # Layout shell (Bootstrap 5 CDN + custom dark CSS)
│   ├── _macros.html     # Reusable results bar-chart macro
│   ├── create.html      # Create-poll form (pure HTML)
│   ├── created.html     # Post-create success page (share links + QR)
│   ├── poll.html        # Public voting form / live results (meta-refresh)
│   ├── admin.html       # Creator dashboard (close form, meta-refresh)
│   ├── not_found.html   # 404 page
│   └── admin_denied.html# 403 page (bad/missing admin token)
└── static/
    └── style.css        # Dark "liquid glass" theme layered over Bootstrap (no JS)
```

---

## License

MIT
