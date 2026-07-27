"""
Auditoria · The Back Office Exposure Check
Built for Auditoria.AI by Navvai.

Serves the single page app and captures what comes out of it:
  GET  /                  the check itself
  GET  /api/config        tells the front end whether it is live and whether email is wired
  POST /api/lead          a completed check (name, company, verdict, cost, every answer)
  POST /api/email-report  emails the rendered report to the prospect
  POST /api/event         funnel events (home viewed, lane started, gate reached, report viewed)
  GET  /api/leads         all leads as JSON            (needs ?key=ADMIN_KEY)
  GET  /api/leads.csv     all leads as a spreadsheet   (needs ?key=ADMIN_KEY)
  GET  /api/events        all funnel events            (needs ?key=ADMIN_KEY)
  GET  /healthz           health check for the platform

Every write also goes to stdout, so the platform log is a durable second copy
even if the disk is ephemeral.
"""

import csv
import io
import json
import os
import time
from collections import deque
from datetime import datetime, timezone
from threading import Lock

import requests
from flask import Flask, Response, jsonify, request, send_from_directory

APP_DIR = os.path.dirname(os.path.abspath(__file__))
DATA_DIR = os.environ.get("DATA_DIR", os.path.join(APP_DIR, "data"))
LEADS_FILE = os.path.join(DATA_DIR, "leads.json")
EVENTS_FILE = os.path.join(DATA_DIR, "events.json")

ADMIN_KEY = os.environ.get("ADMIN_KEY", "")
RESEND_API_KEY = os.environ.get("RESEND_API_KEY", "")
FROM_EMAIL = os.environ.get("FROM_EMAIL", "")            # e.g. Auditoria Check <check@auditoria.ai>
NOTIFY_EMAIL = os.environ.get("NOTIFY_EMAIL", "")        # internal copy of every lead
BOOKING_URL = os.environ.get("BOOKING_URL", "https://info.auditoria.ai/request-a-demo")

app = Flask(__name__, static_folder="static", static_url_path="")
_lock = Lock()
_recent_posts = deque(maxlen=400)      # cheap in memory rate limit


# ----------------------------------------------------------------------------- helpers

def _now():
    return datetime.now(timezone.utc).isoformat(timespec="seconds")


def _ensure_dir():
    os.makedirs(DATA_DIR, exist_ok=True)


def _read(path):
    try:
        with open(path, "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception:
        return []


def _append(path, record):
    _ensure_dir()
    with _lock:
        rows = _read(path)
        rows.append(record)
        tmp = path + ".tmp"
        with open(tmp, "w", encoding="utf-8") as f:
            json.dump(rows, f, ensure_ascii=False, indent=1)
        os.replace(tmp, path)
    return record


def _client_ip():
    fwd = request.headers.get("X-Forwarded-For", "")
    return (fwd.split(",")[0].strip() if fwd else request.remote_addr) or "unknown"


def _rate_limited(limit=12, window=600):
    """No more than `limit` posts from one address every `window` seconds."""
    ip, now = _client_ip(), time.time()
    with _lock:
        while _recent_posts and now - _recent_posts[0][1] > window:
            _recent_posts.popleft()
        hits = sum(1 for a, t in _recent_posts if a == ip)
        if hits >= limit:
            return True
        _recent_posts.append((ip, now))
    return False


def _authorised():
    return bool(ADMIN_KEY) and request.args.get("key", "") == ADMIN_KEY


def _email_ready():
    return bool(RESEND_API_KEY and FROM_EMAIL)


def _send_email(to, subject, html):
    if not _email_ready():
        return False, "email not configured"
    try:
        r = requests.post(
            "https://api.resend.com/emails",
            headers={"Authorization": "Bearer " + RESEND_API_KEY,
                     "Content-Type": "application/json"},
            json={"from": FROM_EMAIL, "to": [to], "subject": subject, "html": html},
            timeout=20,
        )
        ok = 200 <= r.status_code < 300
        print(json.dumps({"t": _now(), "kind": "email", "to": to, "ok": ok,
                          "status": r.status_code}), flush=True)
        return ok, ("sent" if ok else r.text[:200])
    except Exception as exc:                                    # never break the flow
        print(json.dumps({"t": _now(), "kind": "email_error", "err": str(exc)[:200]}), flush=True)
        return False, str(exc)[:200]


def _wrap_email(inner_html, headline):
    """Report HTML arrives already styled. This just gives it an envelope."""
    return f"""<div style="background:#05030d;padding:26px 0;font-family:Inter,Segoe UI,Arial,sans-serif">
  <div style="max-width:720px;margin:0 auto;padding:0 18px;color:#F6F4FF">
    <p style="font-size:11px;letter-spacing:.2em;text-transform:uppercase;color:#A9A3C9;margin:0 0 8px">
      Auditoria · The Back Office Exposure Check</p>
    <h1 style="font-size:26px;margin:0 0 14px;color:#fff">{headline}</h1>
    <p style="color:#CFCAE8;font-size:15px;margin:0 0 20px">
      Your full report is below. Every score came from an answer you tapped and every figure from a
      number you gave. It carries a glossary at the end, so it can be forwarded to people who never
      saw the check.</p>
    <p style="margin:0 0 26px">
      <a href="{BOOKING_URL}" style="display:inline-block;background:#8B5CF6;color:#fff;
         text-decoration:none;padding:13px 24px;border-radius:999px;font-weight:600">
        Book the working session</a></p>
    <hr style="border:0;border-top:1px solid rgba(255,255,255,.14);margin:0 0 22px">
    {inner_html}
  </div></div>"""


# ----------------------------------------------------------------------------- routes

@app.after_request
def _headers(resp):
    resp.headers["X-Content-Type-Options"] = "nosniff"
    resp.headers["Referrer-Policy"] = "strict-origin-when-cross-origin"
    if resp.mimetype == "text/html":
        resp.headers["Cache-Control"] = "no-cache"
    return resp


@app.get("/")
def index():
    return send_from_directory(app.static_folder, "index.html")


@app.get("/healthz")
def healthz():
    return jsonify(ok=True, time=_now(), email=_email_ready())


@app.get("/api/config")
def config():
    """The front end asks this on load so it never claims a feature it does not have."""
    return jsonify(live=True, email=_email_ready(), booking=BOOKING_URL)


@app.post("/api/lead")
def lead():
    if _rate_limited():
        return jsonify(ok=False, error="rate limited"), 429
    body = request.get_json(silent=True) or {}
    record = {
        "t": _now(),
        "ip": _client_ip(),
        "first": str(body.get("first", ""))[:80],
        "last": str(body.get("last", ""))[:80],
        "email": str(body.get("email", ""))[:160],
        "company": str(body.get("company", ""))[:160],
        "role": str(body.get("role", ""))[:120],
        "industry": str(body.get("industry", ""))[:120],
        "erp": str(body.get("erp", ""))[:120],
        "lane": str(body.get("lane", ""))[:40],
        "verdict": str(body.get("verdict", ""))[:40],
        "weakest": str(body.get("weakest", ""))[:80],
        "weakest_score": body.get("weakest_score"),
        "control_score": body.get("control_score"),
        "gaps": body.get("gaps"),
        "cost_month": body.get("cost_month"),
        "currency": str(body.get("currency", ""))[:4],
        "goal": str(body.get("goal", ""))[:400],
        "first_move": str(body.get("first_move", ""))[:200],
        "answers": body.get("answers", {}),
    }
    print(json.dumps({"kind": "lead", **{k: v for k, v in record.items() if k != "answers"}}),
          flush=True)
    _append(LEADS_FILE, record)

    emailed = False
    html = body.get("report_html", "")
    if _email_ready() and record["email"] and html:
        head = f"{record['verdict']}: your back office exposure check"
        emailed, _ = _send_email(record["email"], head, _wrap_email(html, record["verdict"]))
        if NOTIFY_EMAIL:
            summary = (f"<p>{record['first']} {record['last']}, {record['role']} at "
                       f"{record['company']}<br>{record['email']}<br>"
                       f"Lane: {record['lane']} · Verdict: {record['verdict']} · "
                       f"Weakest: {record['weakest']} {record['weakest_score']} · "
                       f"Gaps: {record['gaps']} · Cost/month: {record['currency']}"
                       f"{record['cost_month']}<br>Goal: {record['goal']}</p>")
            _send_email(NOTIFY_EMAIL,
                        f"New check · {record['company']} · {record['verdict']}",
                        _wrap_email(summary + html, f"{record['company']} · {record['verdict']}"))
    return jsonify(ok=True, emailed=emailed)


@app.post("/api/email-report")
def email_report():
    if _rate_limited(limit=6):
        return jsonify(ok=False, error="rate limited"), 429
    body = request.get_json(silent=True) or {}
    to, html = str(body.get("email", ""))[:160], body.get("report_html", "")
    if not to or not html:
        return jsonify(ok=False, error="email and report_html required"), 400
    ok, msg = _send_email(to, str(body.get("subject", "Your back office exposure check"))[:160],
                          _wrap_email(html, str(body.get("headline", "Your report"))[:80]))
    return jsonify(ok=ok, detail=msg)


@app.post("/api/event")
def event():
    body = request.get_json(silent=True) or {}
    record = {"t": _now(), "ip": _client_ip(),
              "name": str(body.get("name", ""))[:60],
              "lane": str(body.get("lane", ""))[:40],
              "detail": str(body.get("detail", ""))[:160]}
    print(json.dumps({"kind": "event", **record}), flush=True)
    _append(EVENTS_FILE, record)
    return jsonify(ok=True)


@app.get("/api/leads")
def leads():
    if not _authorised():
        return jsonify(ok=False, error="unauthorised"), 401
    return jsonify(count=len(_read(LEADS_FILE)), leads=_read(LEADS_FILE))


@app.get("/api/events")
def events():
    if not _authorised():
        return jsonify(ok=False, error="unauthorised"), 401
    return jsonify(count=len(_read(EVENTS_FILE)), events=_read(EVENTS_FILE))


@app.get("/api/leads.csv")
def leads_csv():
    if not _authorised():
        return Response("unauthorised", status=401, mimetype="text/plain")
    cols = ["t", "first", "last", "email", "company", "role", "industry", "erp", "lane",
            "verdict", "weakest", "weakest_score", "control_score", "gaps",
            "currency", "cost_month", "first_move", "goal"]
    buf = io.StringIO()
    w = csv.writer(buf)
    w.writerow(["Date", "First name", "Surname", "Email", "Company", "Role", "Industry",
                "Finance stack", "Lane", "Verdict", "Weakest area", "Weakest score",
                "Control score %", "Gaps", "Currency", "Cost per month", "First move",
                "12 month goal"])
    for row in _read(LEADS_FILE):
        w.writerow([row.get(c, "") for c in cols])
    return Response(buf.getvalue(), mimetype="text/csv",
                    headers={"Content-Disposition": "attachment; filename=auditoria-check-leads.csv"})


if __name__ == "__main__":
    app.run(host="0.0.0.0", port=int(os.environ.get("PORT", 5000)), debug=False)
