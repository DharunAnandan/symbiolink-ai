# Deploying SymbioLink AI to Render

This gets the app live on the internet with a real Postgres database,
running the exact same code as your local dev copy. Everything on the code
side is already done and tested (see "What's already been done" at the
bottom) — the steps below are the browser/account actions only you can do.

## 1. Push the code to GitHub

If you don't already have a GitHub account, create one free at
github.com/join.

Then, from a terminal in this project folder:

```bash
git init
git add .
git commit -m "Ready for deployment"
```

Go to github.com/new, create a new **empty** repository (don't check
"Add a README" — you already have one), then copy the two commands GitHub
shows you under "…or push an existing repository from the command line",
something like:

```bash
git remote add origin https://github.com/<your-username>/<repo-name>.git
git branch -M main
git push -u origin main
```

## 2. Create a Render account and deploy the Blueprint

1. Go to [render.com](https://render.com) and sign up (free — you can use
   your GitHub account to sign in, which also makes step 3 easier).
2. From the Render dashboard, click **New +** → **Blueprint**.
3. Connect your GitHub account if asked, then pick the repository you just
   pushed.
4. Render will read `render.yaml` from the repo root and show you two
   resources it's about to create: a **Postgres database** and a **web
   service**. Click **Apply** / **Create**.
5. Render builds the app (`pip install -r requirements.txt`) and starts it
   (`gunicorn app:app`). The first build takes a few minutes — you can watch
   the logs live on the service's page.

When it finishes, Render gives you a URL like
`https://symbiolink-ai.onrender.com` — that's your live site.

## 3. Seed the database with demo data (one-time)

A fresh database has no companies/listings in it yet. To load the same demo
dataset (`data.py`'s ~24 sample units) that you've been testing with
locally:

1. On the web service's Render page, open the **Shell** tab (top nav).
2. Run:
   ```bash
   python database_migration.py
   ```
3. This creates demo login accounts too — printed at the end, but for
   reference: `admin` / `admin123` for the admin account, and `u1`, `u2`,
   `u3`... / `demo123` for each seeded company.

You only need to do this once. Real signups after that (via the site's own
"Create your account" page) work immediately with no shell access needed —
that's the flow the last several rounds of changes were built around.

## 4. (Optional) Add real integrations

The app runs and demos fully without any of these — every one of them has a
working "simulation mode" that just prints to the log instead of sending a
real message/charge, so nothing is broken by leaving them unset. Add them
later, any time, with no redeploy needed:

Render dashboard → your web service → **Environment** tab → add any of:

| Variable | What it enables |
|---|---|
| `TWILIO_ACCOUNT_SID`, `TWILIO_AUTH_TOKEN`, `TWILIO_PHONE_NUMBER` | Real WhatsApp messages instead of console-logged ones |
| `RAZORPAY_KEY_ID`, `RAZORPAY_KEY_SECRET` | Real payment processing |
| `GOOGLE_MAPS_API_KEY` | Real driving distances instead of straight-line estimates |
| `SMTP_HOST`, `SMTP_PORT`, `SMTP_USERNAME`, `SMTP_PASSWORD` | Real emails instead of console-logged ones |

Saving any of these triggers an automatic redeploy (10-30 seconds, no
downtime for a demo).

## What's already been done (no action needed here)

- **Database**: `config.py` now reads `DATABASE_URL` for production and
  normalizes Render's `postgres://` URLs to the `postgresql://` scheme
  SQLAlchemy needs. Verified end-to-end against a real local Postgres
  instance: `db.create_all()` created all 14 tables, `database_migration.py`
  seeded it, and the app ran correctly under gunicorn against it (login,
  dashboard, search, orders all checked).
- **Trained ML models**: the `.joblib` files in `ml_artifacts/` are
  committed (not gitignored) specifically so the deployed app runs the same
  5 trained models as your local copy, not an untrained fallback.
- **Production server**: `gunicorn` (2 workers, 4 threads, 120s timeout) —
  see `Procfile` — replaces Flask's own dev server, which isn't meant for
  real traffic.
- **`render.yaml`**: a one-click "Blueprint" that provisions the web service
  and its Postgres database together, wiring `DATABASE_URL` between them
  automatically — you never see or copy a database password by hand.

## If you'd rather use Railway instead of Render

Everything above except `render.yaml` and the exact click-path in step 2
still applies — Railway also reads a standard git repo, runs
`pip install -r requirements.txt` + your `Procfile`'s start command, and can
give you either Postgres (same as above, zero extra changes) or actual
MySQL (skip the Postgres normalization entirely; just set `DATABASE_URL` to
the `mysql+pymysql://...` connection string Railway shows you for its MySQL
add-on). Ask if you want a Railway-specific walkthrough instead.
