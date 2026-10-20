# Local dashboard development

See [Windows Persian guide](../WINDOWS-FA.md) for installation, backups and privacy notes.

Architecture: Flask + vanilla JavaScript RTL UI, SQLite WAL, one sequential background worker, original gosom v1.18.1 engine. No CDN, frontend build, paid API, account or external database. Run **one Gunicorn worker only**; four HTTP threads share a single background worker. Multiple replicas/processes are not supported. Engine job IDs persist between restarts; SQLite transactions make repeated import idempotent when stable source identifiers exist. A crash between remote job creation and local ID persistence can leave an orphan engine job. Unknown or failed jobs require inspection, not an automatic blind retry.

```bash
python3 -m venv /tmp/azar-venv
/tmp/azar-venv/bin/pip install -r dashboard/requirements.txt
/tmp/azar-venv/bin/python -m unittest discover -s tests -v
python3 scripts/test_scrape.py
docker compose -p azar-dandeh -f compose.dashboard.yml up -d --build --wait
```

Local development without a second scraper (reuse the existing loopback engine):

```bash
DASHBOARD_DB=/tmp/azar-dev/leads.sqlite3 ENGINE_URL=http://127.0.0.1:8080 /tmp/azar-venv/bin/python -m dashboard.app
```

The Flask development server is only for development; Compose uses Gunicorn. Open `http://localhost:8090` manually. The index establishes an HttpOnly SameSite session and CSRF token; `/api/*` requests must provide it. Host and Origin checks prevent ordinary cross-origin mutation and DNS rebinding. There is no multiuser login: the host OS and loopback binding form the local access boundary. Engine API is not host-published in dashboard Compose. Service listeners use container interfaces only to support Docker networking; the sole host publication is `127.0.0.1:8090`.

Enrichment is opt-in. Social extraction visits public website homepages, rejects private addresses and redirects, and never visits social platforms to extract profiles. A failed enrichment adds a visible warning; it does not discard successful lead rows. Google/Nominatim access and category relevance are never guaranteed. `empty` is a separate outcome because engine `ok` can mask navigation failures. Job progress is completed search combinations, not a synthetic percentage inside the browser job.

Tests use a deterministic fake engine: multilingual projects, queue limits, durable resume, alias deduplication, editable field preservation, failed/empty distinction, CSRF/Host/Origin guards, formula-safe Unicode exports and private URL rejection. No real scrape is started by the test suite.

The worker retains the original raw engine results in the scraper volume and stores curated lead fields plus stable identifier aliases in SQLite. Existing manual edits win over subsequent imports. Country/city are the **requested search market**, not verified business geography; inspect addresses before outreach. The initial version returns matching leads as one table; pagination and large-dataset load testing remain future work.

Browser smoke test (requires separately installed Playwright and a local Chromium; these are test-only dependencies):

```bash
python3 -m pip install playwright==1.62.0
# Set CHROMIUM_PATH to an existing browser executable as needed.
python3 tests/browser_smoke.py
```

In Codex the HTTPS inspection CA must be trusted during Docker builds. The Dockerfile supports an **optional** BuildKit `build_ca` secret containing the environment-provided public CA certificate. It adds this CA to the image trust store while keeping TLS verification enabled. This Cloud-specific override is not necessary on ordinary Windows networks and is not included in the Windows startup configuration. Never use insecure pip flags or disable TLS validation to resolve a certificate error. A corporate Windows proxy may require its own administrator-approved CA configuration.
