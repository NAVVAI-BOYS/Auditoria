# Auditoria · The Back Office Exposure Check

Lead magnet web app built for Auditoria.AI by Navvai. A prospect picks a lane
(Payables or Receivables), answers sixteen tapped questions, unlocks at an email
gate and gets a six part report scored entirely from their own answers and figures.

---

## Deploy on Render

**Build command**

```
pip install -r requirements.txt
```

**Start command**

```
gunicorn app:app --bind 0.0.0.0:$PORT --workers 2 --timeout 60
```

Runtime: Python. Health check path: `/healthz`.

Push this folder to the root of a GitHub repo and Render picks up `render.yaml`
automatically, including the persistent disk. If you nest it inside a subfolder
instead, set **Root Directory** to that subfolder in the Render service settings,
the same way `navvai-audit/` is set on AUDIT-LITE.

---

## Environment variables

| Variable | Needed | What it does |
|---|---|---|
| `ADMIN_KEY` | yes | Guards `/api/leads`, `/api/leads.csv` and `/api/events`. Render generates one if you use `render.yaml`. |
| `DATA_DIR` | yes on Render | Where leads are written. Set to `/var/data` so it lands on the mounted disk and survives a redeploy. Defaults to `./data` locally. |
| `BOOKING_URL` | optional | Where the Book the working session button goes. Defaults to Auditoria's demo request page. |
| `RESEND_API_KEY` | optional | Turns on email. Until it is set the app says so on the page rather than pretending. |
| `FROM_EMAIL` | with Resend | Verified sender, e.g. `Auditoria Check <check@auditoria.ai>`. Needs DKIM and SPF on the sending domain. |
| `NOTIFY_EMAIL` | optional | Internal copy of every completed check, with the lead summary above the report. |

The app is honest about its own state. With no `RESEND_API_KEY` the ribbon, the
saved line at the top of the report and the footer all say email sending is not
configured, and the Email button falls back to opening a draft. Set the key and
all four change automatically. Nothing claims a feature it does not have.

---

## Run it locally

```
pip install -r requirements.txt
ADMIN_KEY=localkey python app.py
```

Then open `http://localhost:5000`.

The file at `static/index.html` also works opened directly from disk with no
server at all. It detects there is no backend and switches to preview wording.
That is the version to send someone as an attachment.

---

## Endpoints

| Route | Method | Notes |
|---|---|---|
| `/` | GET | The check |
| `/healthz` | GET | Health check for the platform |
| `/api/config` | GET | Tells the front end whether it is live and whether email is wired |
| `/api/lead` | POST | A completed check. Writes to disk, prints to the log, emails if configured |
| `/api/email-report` | POST | Resends the report to the prospect |
| `/api/event` | POST | Funnel events: app loaded, lane started, gate reached, report viewed, each CTA |
| `/api/leads?key=` | GET | Every lead as JSON |
| `/api/leads.csv?key=` | GET | Every lead as a spreadsheet |
| `/api/events?key=` | GET | Every funnel event |

Pull the leads spreadsheet with:

```
https://your-service.onrender.com/api/leads.csv?key=YOUR_ADMIN_KEY
```

Columns: date, first name, surname, email, company, role, industry, finance
stack, lane, verdict, weakest area, weakest score, control score, gaps found,
currency, cost per month, first move, twelve month goal.

Every lead is also written to stdout as one JSON line, so the Render log is a
durable second copy even if the disk is ever detached.

---

## What is in the box

```
app.py             Flask backend: serving, lead capture, events, CSV, Resend
requirements.txt   Flask, gunicorn, requests
render.yaml        Render blueprint including the 1GB disk and health check
Procfile           Same start command, for anything that reads a Procfile
static/index.html  The whole app, one file, no build step, no dependencies
data/              Where leads.json and events.json land locally
```

`static/index.html` has no build step by design. There is no bundler, no npm
install and no framework. Edit the file, redeploy, done.

---

## Safety rails already in it

- Rate limited to twelve submissions per address per ten minutes, six for resends.
- Admin routes return 401 without the key, and `ADMIN_KEY` is never sent to the browser.
- Every backend call from the front end fails silent, so a backend outage
  degrades the app to preview mode rather than breaking the report.
- No prices for Auditoria work appear anywhere in the output.
- No figure is ever invented. If a prospect says an input is not measured, the
  cost section refuses to print a number and shows the arithmetic across three
  stated values instead, each labelled as illustrative.
