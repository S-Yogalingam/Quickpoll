"""QuickPoll — Flask + MySQL.

A polling web app. Anyone can create a poll, share a unique link, and watch
results update in real time (via Server-Sent Events). The creator gets a
private admin link to view detailed results and close the poll.

REST API (base /api), identical contract to the original FastAPI service:
    GET    /api/health                 health check
    POST   /api/polls                  create a poll -> poll + admin_token (201)
    GET    /api/polls/<id>             public poll + results (sets voter cookie)
    POST   /api/polls/<id>/vote        cast a vote (dedupe via cookie)
    GET    /api/polls/<id>/stream      SSE live results
    GET    /api/polls/<id>/admin?token= creator detail view (requires admin_token)
    POST   /api/polls/<id>/close?token= close the poll (requires admin_token)

Server-rendered pages (Jinja2), replacing the React SPA routes:
    GET    /                          create page
    GET    /p/<id>                     public voting / results page
    GET    /p/<id>/admin               creator dashboard
"""
from __future__ import annotations

import base64
import json
import time
import uuid
from datetime import datetime, timedelta, timezone
from io import BytesIO

import qrcode
import qrcode.image.svg
from flask import (
    Flask,
    Response,
    abort,
    jsonify,
    make_response,
    redirect,
    render_template,
    request,
    url_for,
)

import config
from broker import broker
from database import get_cursor

app = Flask(__name__, static_folder="static", template_folder="templates")
app.secret_key = config.SECRET_KEY


# ---------------------------------------------------------------------------
# Time helpers
# ---------------------------------------------------------------------------
def utcnow() -> datetime:
    return datetime.now(timezone.utc)


def _as_utc(dt: datetime | None) -> datetime | None:
    """MySQL TIMESTAMP comes back naive (UTC). Attach tzinfo for comparisons."""
    if dt is None:
        return None
    if dt.tzinfo is None:
        return dt.replace(tzinfo=timezone.utc)
    return dt


def _iso(dt: datetime | None) -> str | None:
    dt = _as_utc(dt)
    return dt.isoformat() if dt else None


def _uuid() -> str:
    return uuid.uuid4().hex


# ---------------------------------------------------------------------------
# Data access (raw SQL, replacing the SQLAlchemy ORM)
# ---------------------------------------------------------------------------
def _fetch_poll(cur, poll_id: str) -> dict | None:
    cur.execute(
        "SELECT id, admin_token, title, created_at, expires_at, closed "
        "FROM polls WHERE id = %s",
        (poll_id,),
    )
    return cur.fetchone()


def _fetch_options(cur, poll_id: str) -> list[dict]:
    cur.execute(
        "SELECT id, text, position FROM options WHERE poll_id = %s ORDER BY position",
        (poll_id,),
    )
    return cur.fetchall()


def _fetch_vote_counts(cur, poll_id: str) -> dict[str, int]:
    cur.execute(
        "SELECT option_id, COUNT(*) AS n FROM votes WHERE poll_id = %s GROUP BY option_id",
        (poll_id,),
    )
    return {row["option_id"]: int(row["n"]) for row in cur.fetchall()}


def _voted_option(cur, poll_id: str, voter_token: str | None) -> str | None:
    if not voter_token:
        return None
    cur.execute(
        "SELECT option_id FROM votes WHERE poll_id = %s AND voter_token = %s",
        (poll_id, voter_token),
    )
    row = cur.fetchone()
    return row["option_id"] if row else None


def _is_expired(poll: dict) -> bool:
    exp = _as_utc(poll.get("expires_at"))
    if exp is None:
        return False
    return utcnow() >= exp


def _is_open(poll: dict) -> bool:
    return not poll["closed"] and not _is_expired(poll)


def _serialize(cur, poll: dict, voter_token: str | None = None) -> dict:
    """Build the PollPublic dict (same shape the React client expects)."""
    options = _fetch_options(cur, poll["id"])
    counts = _fetch_vote_counts(cur, poll["id"])
    voted_option_id = _voted_option(cur, poll["id"], voter_token)
    out_options = [
        {"id": o["id"], "text": o["text"], "votes": counts.get(o["id"], 0)}
        for o in options
    ]
    return {
        "id": poll["id"],
        "title": poll["title"],
        "options": out_options,
        "total_votes": sum(counts.values()),
        "closed": bool(poll["closed"]),
        "expired": _is_expired(poll),
        "is_open": _is_open(poll),
        "created_at": _iso(poll["created_at"]),
        "expires_at": _iso(poll["expires_at"]),
        "has_voted": voted_option_id is not None,
        "voted_option_id": voted_option_id,
    }


# ---------------------------------------------------------------------------
# Cookies
# ---------------------------------------------------------------------------
def _get_voter_token() -> str:
    """Read the voter token from the request cookie, or mint a fresh one."""
    token = request.cookies.get(config.VOTER_COOKIE)
    if not token:
        token = _uuid()
    return token


def _set_voter_cookie(resp: Response, token: str) -> None:
    resp.set_cookie(
        config.VOTER_COOKIE,
        token,
        max_age=config.COOKIE_MAX_AGE,
        httponly=True,
        samesite="Lax",
    )


def _err(status: int, detail: str):
    """Match FastAPI's {"detail": "..."} error body."""
    return jsonify({"detail": detail}), status


def _publish_results(poll_id: str) -> None:
    with get_cursor() as (_conn, cur):
        poll = _fetch_poll(cur, poll_id)
        if not poll:
            return
        payload = _serialize(cur, poll)
    broker.publish(poll_id, json.dumps(payload))


# ---------------------------------------------------------------------------
# API: health
# ---------------------------------------------------------------------------
@app.get("/api/health")
def health():
    return jsonify({"status": "ok"})


# ---------------------------------------------------------------------------
# API: create poll
# ---------------------------------------------------------------------------
@app.post("/api/polls")
def create_poll():
    body = request.get_json(silent=True) or {}

    # --- validation (replacing Pydantic) ---
    title = (body.get("title") or "").strip()
    if not title:
        return _err(422, "Title cannot be empty")
    if len(title) > 300:
        return _err(422, "Title is too long (max 300)")

    raw_options = body.get("options")
    if not isinstance(raw_options, list):
        return _err(422, "options must be a list")
    cleaned = [str(o).strip() for o in raw_options if o and str(o).strip()]
    if len(cleaned) < 2:
        return _err(422, "A poll needs at least 2 non-empty options")
    if len(cleaned) > 5:
        return _err(422, "A poll can have at most 5 options")
    for o in cleaned:
        if len(o) > 200:
            return _err(422, "An option is too long (max 200)")

    expires_in = body.get("expires_in_minutes")
    expires_at = None
    if expires_in is not None:
        try:
            mins = int(expires_in)
        except (TypeError, ValueError):
            return _err(422, "expires_in_minutes must be an integer")
        if mins < 1 or mins > 525600:
            return _err(422, "expires_in_minutes out of range (1..525600)")
        expires_at = utcnow() + timedelta(minutes=mins)

    poll_id = _uuid()
    admin_token = _uuid()

    with get_cursor(commit=True) as (_conn, cur):
        cur.execute(
            "INSERT INTO polls (id, admin_token, title, expires_at, closed) "
            "VALUES (%s, %s, %s, %s, 0)",
            (
                poll_id,
                admin_token,
                title,
                expires_at.strftime("%Y-%m-%d %H:%M:%S") if expires_at else None,
            ),
        )
        for i, text in enumerate(cleaned):
            cur.execute(
                "INSERT INTO options (id, poll_id, text, position) VALUES (%s, %s, %s, %s)",
                (_uuid(), poll_id, text, i),
            )

    with get_cursor() as (_conn, cur):
        poll = _fetch_poll(cur, poll_id)
        data = _serialize(cur, poll)
    data["admin_token"] = admin_token
    return jsonify(data), 201


# ---------------------------------------------------------------------------
# API: public poll view (sets voter cookie)
# ---------------------------------------------------------------------------
@app.get("/api/polls/<poll_id>")
def get_poll(poll_id: str):
    token = _get_voter_token()
    with get_cursor() as (_conn, cur):
        poll = _fetch_poll(cur, poll_id)
        if not poll:
            return _err(404, "Poll not found")
        data = _serialize(cur, poll, voter_token=token)
    resp = make_response(jsonify(data))
    _set_voter_cookie(resp, token)
    return resp


# ---------------------------------------------------------------------------
# API: vote
# ---------------------------------------------------------------------------
@app.post("/api/polls/<poll_id>/vote")
def vote(poll_id: str):
    body = request.get_json(silent=True) or {}
    option_id = body.get("option_id")
    token = _get_voter_token()

    with get_cursor(commit=True) as (_conn, cur):
        poll = _fetch_poll(cur, poll_id)
        if not poll:
            return _err(404, "Poll not found")
        if not _is_open(poll):
            return _err(409, "This poll is closed")

        options = _fetch_options(cur, poll_id)
        option_ids = {o["id"] for o in options}
        if option_id not in option_ids:
            return _err(400, "Invalid option for this poll")

        # Cookie-based dedupe (DB UNIQUE constraint is the backstop).
        cur.execute(
            "SELECT id FROM votes WHERE poll_id = %s AND voter_token = %s",
            (poll_id, token),
        )
        if cur.fetchone():
            return _err(409, "You have already voted in this poll")

        try:
            cur.execute(
                "INSERT INTO votes (id, poll_id, option_id, voter_token) "
                "VALUES (%s, %s, %s, %s)",
                (_uuid(), poll_id, option_id, token),
            )
        except Exception:
            # UNIQUE(poll_id, voter_token) violation under a race.
            return _err(409, "You have already voted in this poll")

    # Re-read fresh state and publish to SSE subscribers.
    with get_cursor() as (_conn, cur):
        poll = _fetch_poll(cur, poll_id)
        data = _serialize(cur, poll, voter_token=token)
    _publish_results(poll_id)

    resp = make_response(jsonify(data))
    _set_voter_cookie(resp, token)
    return resp


# ---------------------------------------------------------------------------
# API: SSE live results stream
# ---------------------------------------------------------------------------
@app.get("/api/polls/<poll_id>/stream")
def stream(poll_id: str):
    with get_cursor() as (_conn, cur):
        if not _fetch_poll(cur, poll_id):
            return _err(404, "Poll not found")

    q = broker.subscribe(poll_id)

    def event_gen():
        try:
            # Snapshot on connect so late joiners are in sync.
            with get_cursor() as (_conn, cur):
                poll = _fetch_poll(cur, poll_id)
                snapshot = json.dumps(_serialize(cur, poll))
            yield f"data: {snapshot}\n\n"
            while True:
                try:
                    data = q.get(timeout=15.0)
                    yield f"data: {data}\n\n"
                except Exception:
                    # Timeout -> keepalive comment frame.
                    yield ": keepalive\n\n"
        finally:
            broker.unsubscribe(poll_id, q)

    return Response(
        event_gen(),
        mimetype="text/event-stream",
        headers={
            "Cache-Control": "no-cache",
            "X-Accel-Buffering": "no",
            "Connection": "keep-alive",
        },
    )


# ---------------------------------------------------------------------------
# API: admin view
# ---------------------------------------------------------------------------
@app.get("/api/polls/<poll_id>/admin")
def admin_view(poll_id: str):
    token = request.args.get("token", "")
    with get_cursor() as (_conn, cur):
        poll = _fetch_poll(cur, poll_id)
        if not poll:
            return _err(404, "Poll not found")
        if token != poll["admin_token"]:
            return _err(403, "Invalid admin token")
        data = _serialize(cur, poll)
    return jsonify(data)


# ---------------------------------------------------------------------------
# API: close poll
# ---------------------------------------------------------------------------
@app.post("/api/polls/<poll_id>/close")
def close_poll(poll_id: str):
    token = request.args.get("token", "")
    with get_cursor(commit=True) as (_conn, cur):
        poll = _fetch_poll(cur, poll_id)
        if not poll:
            return _err(404, "Poll not found")
        if token != poll["admin_token"]:
            return _err(403, "Invalid admin token")
        cur.execute("UPDATE polls SET closed = 1 WHERE id = %s", (poll_id,))

    with get_cursor() as (_conn, cur):
        poll = _fetch_poll(cur, poll_id)
        data = _serialize(cur, poll)
    _publish_results(poll_id)
    return jsonify(data)


# ---------------------------------------------------------------------------
# QR helper (pure Python, no Pillow): returns an SVG data URL for any URL.
# ---------------------------------------------------------------------------
def _qr_data_url(url: str) -> str:
    img = qrcode.make(
        url, image_factory=qrcode.image.svg.SvgImage, box_size=10, border=2
    )
    buf = BytesIO()
    img.save(buf)
    b64 = base64.b64encode(buf.getvalue()).decode("ascii")
    return f"data:image/svg+xml;base64,{b64}"


# ---------------------------------------------------------------------------
# Server-rendered pages (pure HTML/CSS — no JavaScript).
#
# Forms POST to these routes, which mutate state and then redirect (Post/
# Redirect/Get). Live results are achieved with a <meta http-equiv="refresh">
# on the results templates, so the page reloads itself every few seconds.
# ---------------------------------------------------------------------------
@app.get("/")
def page_create():
    return render_template("create.html")


@app.post("/")
def page_create_submit():
    """Handle the create-poll HTML form. On success redirect to the success page."""
    form = request.form
    title = (form.get("title") or "").strip()

    # Collect option_1..option_5 (plus any legacy "options" repeated field).
    raw_options = [form.get(f"option_{i}", "") for i in range(1, 6)]
    raw_options += form.getlist("options")
    cleaned = [str(o).strip() for o in raw_options if o and str(o).strip()]

    expires_raw = (form.get("expires_in_minutes") or "").strip()

    # --- validation (same rules as the JSON API) ---
    error = None
    if not title:
        error = "Please enter a poll question."
    elif len(title) > 300:
        error = "Title is too long (max 300)."
    elif len(cleaned) < 2:
        error = "Add at least 2 non-empty options."
    elif len(cleaned) > 5:
        error = "A poll can have at most 5 options."
    elif any(len(o) > 200 for o in cleaned):
        error = "An option is too long (max 200)."

    expires_at = None
    if not error and expires_raw:
        try:
            mins = int(expires_raw)
            if mins < 1 or mins > 525600:
                error = "Expiry is out of range."
            else:
                expires_at = utcnow() + timedelta(minutes=mins)
        except (TypeError, ValueError):
            error = "Invalid expiry value."

    if error:
        # Re-render the form with the user's input preserved.
        return (
            render_template(
                "create.html",
                error=error,
                form_title=title,
                form_options=cleaned or ["", ""],
                form_expiry=expires_raw,
            ),
            422,
        )

    poll_id = _uuid()
    admin_token = _uuid()
    with get_cursor(commit=True) as (_conn, cur):
        cur.execute(
            "INSERT INTO polls (id, admin_token, title, expires_at, closed) "
            "VALUES (%s, %s, %s, %s, 0)",
            (
                poll_id,
                admin_token,
                title,
                expires_at.strftime("%Y-%m-%d %H:%M:%S") if expires_at else None,
            ),
        )
        for i, text in enumerate(cleaned):
            cur.execute(
                "INSERT INTO options (id, poll_id, text, position) VALUES (%s, %s, %s, %s)",
                (_uuid(), poll_id, text, i),
            )

    return redirect(url_for("page_created", poll_id=poll_id, token=admin_token))


@app.get("/p/<poll_id>/created")
def page_created(poll_id: str):
    """Success page shown right after creating a poll: share links + QR codes."""
    token = request.args.get("token", "")
    with get_cursor() as (_conn, cur):
        poll = _fetch_poll(cur, poll_id)
        if not poll:
            abort(404)
        if token != poll["admin_token"]:
            abort(403)
        data = _serialize(cur, poll)

    base = request.host_url.rstrip("/")
    public_url = f"{base}/p/{poll_id}"
    admin_url = f"{base}/p/{poll_id}/admin?token={token}"
    return render_template(
        "created.html",
        poll=data,
        public_url=public_url,
        admin_url=admin_url,
        public_qr=_qr_data_url(public_url),
    )


@app.get("/p/<poll_id>")
def page_poll(poll_id: str):
    """Public voting / live-results page (server-rendered)."""
    token = _get_voter_token()
    with get_cursor() as (_conn, cur):
        poll = _fetch_poll(cur, poll_id)
        if not poll:
            return render_template("not_found.html"), 404
        data = _serialize(cur, poll, voter_token=token)

    base = request.host_url.rstrip("/")
    public_url = f"{base}/p/{poll_id}"
    show_results = data["has_voted"] or not data["is_open"]
    just_voted = request.args.get("voted") == "1"
    vote_error = request.args.get("err")

    resp = make_response(
        render_template(
            "poll.html",
            poll=data,
            public_url=public_url,
            public_qr=_qr_data_url(public_url),
            show_results=show_results,
            just_voted=just_voted,
            vote_error=vote_error,
            # Auto-refresh while open and showing live results.
            auto_refresh=show_results and data["is_open"],
        )
    )
    _set_voter_cookie(resp, token)
    return resp


@app.post("/p/<poll_id>/vote")
def page_vote(poll_id: str):
    """Handle a vote from the HTML form, then redirect back to the poll page."""
    option_id = request.form.get("option_id")
    token = _get_voter_token()

    err = None
    with get_cursor(commit=True) as (_conn, cur):
        poll = _fetch_poll(cur, poll_id)
        if not poll:
            return render_template("not_found.html"), 404
        if not _is_open(poll):
            err = "This poll is closed."
        else:
            options = _fetch_options(cur, poll_id)
            if option_id not in {o["id"] for o in options}:
                err = "Please choose an option."
            else:
                cur.execute(
                    "SELECT id FROM votes WHERE poll_id = %s AND voter_token = %s",
                    (poll_id, token),
                )
                if cur.fetchone():
                    err = "You have already voted in this poll."
                else:
                    try:
                        cur.execute(
                            "INSERT INTO votes (id, poll_id, option_id, voter_token) "
                            "VALUES (%s, %s, %s, %s)",
                            (_uuid(), poll_id, option_id, token),
                        )
                    except Exception:
                        err = "You have already voted in this poll."

    if not err:
        _publish_results(poll_id)

    target = url_for("page_poll", poll_id=poll_id)
    target += "?voted=1" if not err else f"?err={err}"
    resp = make_response(redirect(target))
    _set_voter_cookie(resp, token)
    return resp


@app.get("/p/<poll_id>/admin")
def page_admin(poll_id: str):
    """Creator dashboard (server-rendered). Requires the admin token."""
    token = request.args.get("token", "")
    with get_cursor() as (_conn, cur):
        poll = _fetch_poll(cur, poll_id)
        if not poll:
            return render_template("not_found.html"), 404
        if token != poll["admin_token"]:
            return render_template("admin_denied.html", poll_id=poll_id), 403
        data = _serialize(cur, poll)

    base = request.host_url.rstrip("/")
    public_url = f"{base}/p/{poll_id}"
    return render_template(
        "admin.html",
        poll=data,
        token=token,
        public_url=public_url,
        public_qr=_qr_data_url(public_url),
        auto_refresh=data["is_open"],
    )


@app.post("/p/<poll_id>/close")
def page_close(poll_id: str):
    """Close the poll from the dashboard form, then redirect back to it."""
    token = request.form.get("token", "")
    with get_cursor(commit=True) as (_conn, cur):
        poll = _fetch_poll(cur, poll_id)
        if not poll:
            return render_template("not_found.html"), 404
        if token != poll["admin_token"]:
            return render_template("admin_denied.html", poll_id=poll_id), 403
        cur.execute("UPDATE polls SET closed = 1 WHERE id = %s", (poll_id,))
    _publish_results(poll_id)
    return redirect(url_for("page_admin", poll_id=poll_id, token=token))


# Kept for API parity: returns a QR code data URL as JSON.
@app.get("/api/qr")
def qr():
    url = request.args.get("url", "")
    if not url:
        return _err(400, "Missing url")
    return jsonify({"data_url": _qr_data_url(url)})


if __name__ == "__main__":
    app.run(host=config.HOST, port=config.PORT, threaded=True, debug=False)
