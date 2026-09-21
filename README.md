# ShipDoc: Shipping Document Verification (Backend)

The engine, API and database behind **ShipDoc**, a system that checks a customer's **Shipping Instruction (SI)** against the carrier's **draft Bill of Lading (BL)** and flags any differences before the ship sails.

This repository holds the **backend**: the verification engine, the command-line tool, the HTTP API, the database layer and the deployment files. The reviewer website lives in the frontend repository:

**Frontend:** <https://github.com/Hackathon-Repo-Org/Front-End-Shipping-Document-Verification-System-Averis_x_Monash_Hackathon_Version-1.0>

Built for the **Averis x Monash Hackathon 2026** by team **Player 4 Not Found**.

---

## Contents

1. [Live demo](#live-demo)
2. [What the system does](#what-the-system-does)
3. [Results](#results)
4. [How it fits together](#how-it-fits-together)
5. [Tech stack](#tech-stack)
6. [Project structure](#project-structure)
7. [Configuration](#configuration)
8. [Run locally](#run-locally)
9. [Optional add-ons](#optional-add-ons)
10. [Command reference](#command-reference)
11. [API reference](#api-reference)
12. [Testing](#testing)
13. [Common problems](#common-problems)
14. [Deployment (cloud)](#deployment-cloud)
15. [Known limitations](#known-limitations)
16. [More documentation](#more-documentation)

---

## Live demo

The system is deployed on **AWS** and ready to use. Nothing needs to be installed to try it.

| | Link |
|---|---|
| Website | <https://shipdoc.duckdns.org> |
| API health check | <https://shipdoc.duckdns.org/api/health> |

- **Reading is open to everyone.** Browse the dashboard, inbox, records and evaluation freely.
- **Review actions need a passcode.** To confirm, correct or approve something, open **Reviewer** in the page header, type any name in **Your name**, enter the passcode below in **Demo passcode**, and click **Save**.

```
averis2026
```

The "Run locally" section is only needed if you want to run your own copy.

---

## What the system does

1. **Reads the inbox.** Every email and its attachments (text, PDF, Excel and Word files; scanned PDFs with optional OCR).
2. **Sorts each email** into one of five categories. Only document-check requests (`BL_COMPARISON`) go on to be checked.
3. **Pulls out seven fields** from the SI and the BL: shipper, consignee, notify party, port of loading, port of discharge, container count and gross weight. Different labels for the same field (for example "Port of Discharge" and "POD") are recognised.
4. **Compares the fields.** Each field gets one of three verdicts: **match**, **mismatch** or **cannot determine**.
5. **Gives each email a status:**

| Status | Meaning |
|---|---|
| `OK` | All seven fields match. "No mismatch detected." |
| `MISMATCH` | At least one real difference, shown with both values side by side |
| `NEEDS_REVIEW` | The system will not guess: a document is missing, unreadable or the wrong type, or a value is blank. The email goes to a person with the evidence and the reason |

Two rules hold for every email:

- **Nothing is silently passed.** Every email ends in a named state, and a crash or failure can never produce `OK`.
- **No invented values.** Every extracted value must appear word for word in the text read from the document, and each flag links back to the exact source line.

The AI model is used in only two narrow places: sorting emails that have no attachments, and suggesting new field labels (which a person must approve). Whether two values match is always decided by plain, testable rules.

---

## Results

Measured on **21 September 2026** with the organisers' own `scoring.py` (imported directly, not rewritten) on the 520-email dataset.

| Scoring axis | Weight | DeepSeek (hosted, current default) | Qwen 2.5 7B (local, via Ollama) |
|---|---|---|---|
| Email classification (macro-F1) | 0.30 | 0.7591 | **0.9526** |
| Defect detection (F1) | 0.20 | **1.0000** | **1.0000** |
| End-to-end: defects caught exactly | 0.50 | **46 / 46** | **46 / 46** |
| Escalation recall (not scored) | 0.00 | 1.000 | 1.000 |
| **Final score** | | **0.9277** | **0.9858** |

- All **20 of 20** planted edge cases are correct with both models.
- **776 automated tests** pass, and two runs produce byte-identical output.
- The live run of 520 emails gives 464 decided automatically, 56 sent to a person and 0 failed.

**Why two models?** Both find every defect. The whole difference is email sorting: DeepSeek labels 106 real `SI_REQUEST` emails as `BL_COMPARISON`, while Qwen gets 124 of 125 right. DeepSeek is the default because the cloud server has no GPU (graphics card), so a hosted model is the practical choice for new emails. To use Qwen instead, change one line in `config/pipeline.yaml` to `provider: ollama`. Qwen is also the option for running with **no internet connection at all**.

**No API key is needed to reproduce either score.** The answers both models gave for all 520 emails are saved in `cache/llm/`. These are the system's own model outputs, not the answer key: each file name is a one-way hash, so no email text is stored. The full comparison is in `docs/provider-comparison.json` and on the website's **Evaluation** page.

A precision of 1.000 is a fact about this dataset, not a promise about new data. Three hand-written test emails in `tests/fixtures/adversarial/` check behaviour on cases the dataset does not cover.

---

## How it fits together

```
Browser
   |
   v
https://shipdoc.duckdns.org   (AWS EC2 server, Singapore)
   |-- Caddy web server
   |      serves the frontend website
   |      forwards every /api/* request to the API
   |
   |-- API container (this repository's Dockerfile)
   |      FastAPI  ->  verification engine
   |
   v
PostgreSQL database (AWS RDS, private: only the EC2 server can reach it)
```

Inside the engine, each email passes through nine layers in order:

```
ingest -> classify -> route -> extract -> normalise -> compare -> state -> project -> output
```

| Layer | What it does |
|---|---|
| ingest | Reads the emails and attachments |
| classify | Puts each email in one of five categories (rules first, AI for attachment-free emails) |
| route | Decides which emails need a document check and finds the SI and BL files |
| extract | Reads text from each file type and finds the seven fields |
| normalise | Puts values in a standard form (units, separators, placeholders such as "N/A") |
| compare | Gives each field a verdict: match, mismatch or cannot determine |
| state | Moves the email to RESOLVED, ESCALATED or FAILED (it can never move backwards) |
| project | Turns the internal result into the output status and review reason |
| output | Writes the submission file, the report, the review queue and the database rows |

A rule enforced by a test (`tests/property/test_layering.py`) stops any layer from reaching into a layer after it.

---

## Tech stack

| Part | Used |
|---|---|
| Language | Python 3.12 or newer |
| File reading | PyMuPDF (PDF), openpyxl (Excel), python-docx (Word); Tesseract through pytesseract for OCR (optional) |
| Matching | RapidFuzz (similarity of names), PyYAML (config files) |
| AI models | DeepSeek (hosted, OpenAI-compatible API) or Qwen 2.5 7B through Ollama (local); OpenAI, Together and Groq also supported |
| API | FastAPI + Uvicorn |
| Database | PostgreSQL (SQLAlchemy + Alembic migrations, 12 tables); SQLite for local use and tests |
| Tests | pytest (776 tests) |
| Container | Docker (non-root user, port 8000) |
| Cloud | AWS EC2 + RDS PostgreSQL, Caddy for HTTPS, DuckDNS for the domain |

---

## Project structure

```
src/shipdoc/
  ingest/  classify/  route/  extract/  normalise/
  compare/  state/  review/  detect/  llm/  infra/
  adapters/
    api/             The HTTP API (FastAPI)
    db/              Database models and repository
    projection.py    Internal result -> output status
    submission.py    Writes submission.json
    report.py        Writes report.txt
  pipeline.py        Runs the nine layers in order
  cli.py             The command-line tool (python -m shipdoc)
  doctor.py          Self-check command
  inspect.py         Shows one email's seven fields side by side
  labels_cli.py      Approve or reject AI-suggested labels
config/
  pipeline.yaml      Which AI provider and model, feature flags
  fields.yaml        The seven fields and their known labels
  learned_labels.yaml  Labels a person approved (delete to undo them all)
  unlocode.csv.gz    Port code table (port resolution is switched off, see below)
dataset/             The organisers' 520 emails and 250 attachments
cache/llm/           Saved AI answers, so no API key is needed for the dataset
tests/               unit/  property/  integration/  golden/  fixtures/adversarial/
eval/evaluate.py     Scores a run with the organisers' scoring.py
alembic/             Database migrations
deploy/aws/          Docker Compose file, Caddyfile and deploy steps for AWS
docs/                Specification, reports, design decisions, provider comparison
Dockerfile           The deployable image (starts the API by default)
.env.example         Every environment variable the system reads
```

---

## Configuration

### Environment variables

Secrets are read from the **environment only**. They are never stored in config files, the cache, logs or the database. Copy `.env.example` to `.env` for local use; `.env` is ignored by Git.

| Variable | Needed for | Example |
|---|---|---|
| `DATABASE_URL` | Saving runs and review decisions | `sqlite:///output/shipdoc.db` (local) or `postgresql+psycopg://user:password@host:5432/shipdoc` |
| `DEMO_PASSCODE` | Allowing review actions through the API. If unset, the API is read-only | any text you choose |
| `CORS_ORIGINS` | Letting the website call the API from another address | `http://localhost:5173` (exact address, no trailing `/`) |
| `DEEPSEEK_API_KEY` | New emails with the DeepSeek model | your key |
| `OPENAI_API_KEY`, `TOGETHER_API_KEY`, `GROQ_API_KEY` | Only if you switch to those providers | your key |
| `SHIPDOC_ROOT` | Where to find `config/`, `dataset/` and `cache/` when installed as a package (set to `/app` in the container) | `/app` |

A test (`tests/property/test_no_secret_leak.py`) searches the whole repository and the cache for anything that looks like a key or a password and fails if it finds one.

### Choosing the AI model

In `config/pipeline.yaml`:

```yaml
llm:
  provider: deepseek        # deepseek | ollama | openai | together | groq | none
  model: deepseek-chat      # for ollama: qwen2.5:7b-instruct
```

With no model and no key, the system falls back to keyword rules, keeps running and records `"degraded": true` in `output/run_summary.json`.

---

## Run locally

Use this if you want your own copy instead of the live demo.

### What you need

| Tool | Version | Check with |
|---|---|---|
| Git | any recent | `git --version` |
| Python | 3.12 or newer | `python --version` |
| Node.js (only for the website) | 20 (18 or newer works) | `node --version` |

No Docker, GPU or model download is needed.

The commands below are for **Windows PowerShell**. On macOS or Linux, use `source .venv/bin/activate` instead of `.\.venv\Scripts\Activate.ps1`, and `export NAME=value` instead of `$env:NAME="value"`. If your folder path contains a space, put the path in quotes.

### Step 1: install

```powershell
git clone https://github.com/Hackathon-Repo-Org/Shipping-Document-Verification-System-Averis_x_Monash_Hackathon.git backend
cd backend
python -m venv .venv
.\.venv\Scripts\Activate.ps1
python -m pip install --upgrade pip
pip install -e ".[dev,db,api]"
```

Your prompt should now start with `(.venv)`. If it does not, the virtual environment is not active and later commands will use the wrong Python.

### Step 2: check the install

```powershell
python -m shipdoc doctor
```

The last line should be `RESULT: PASS - shipdoc is ready`. Lines marked `[--  ]` (such as OCR or LLM) are **optional and normal on a fresh machine**. Add `--fast` to skip the test run inside the check.

### Step 3: run the engine on all 520 emails

```powershell
python -m shipdoc                      # full run, results in output\
python -m shipdoc inspect email_013    # one email, seven fields side by side
```

This takes about 3 seconds with the saved AI answers. Four files are written to `output\`:

| File | What it is |
|---|---|
| `submission.json` | 520 entries in the organisers' format |
| `run_summary.json` | Counts by status and reason, and whether the run was degraded |
| `report.txt` | Readable report with SI and BL values side by side |
| `review_queue.json` | Every escalated email with its evidence |

### Step 4: start the API with a database

```powershell
$env:DATABASE_URL="sqlite:///output/shipdoc.db"
$env:DEMO_PASSCODE="choose-any-passcode"
$env:CORS_ORIGINS="http://localhost:5173"

python -m shipdoc db seed      # load the 520 emails into the database
python -m shipdoc              # run the engine again so the results are saved
python -m uvicorn shipdoc.adapters.api.app:app --port 8000
```

Open <http://localhost:8000/api/health>. You should see `"status":"ok"` and `"records":520`.

### Step 5: start the website (optional)

In a second terminal, follow the "Run locally" steps in the [frontend repository](https://github.com/Hackathon-Repo-Org/Front-End-Shipping-Document-Verification-System-Averis_x_Monash_Hackathon_Version-1.0). Then open <http://localhost:5173> and sign in as a reviewer with any name and the passcode you set in Step 4.

---

## Optional add-ons

The system runs without any of these and says what it is missing.

| Add-on | What it adds | How |
|---|---|---|
| **OCR** | Reads the three scanned PDFs (`email_512` to `email_514`). Without it they go to review as unreadable, which is the correct answer | `winget install UB-Mannheim.TesseractOCR`, then `pip install -e ".[ocr,dev]"`. Reopen the terminal and run `python -m shipdoc doctor --fast` |
| **Local AI (Qwen)** | Sorting with the higher-scoring local model, fully offline | `winget install Ollama.Ollama`, then `ollama pull qwen2.5:7b-instruct` (about 4.7 GB). Set `provider: ollama` in `config/pipeline.yaml` |
| **Hosted AI (DeepSeek)** | Sorting new emails without a GPU | `pip install -e ".[hosted]"`, put `DEEPSEEK_API_KEY` in `.env`, keep `provider: deepseek` |
| **PostgreSQL** | A shared, durable database instead of SQLite | Set `DATABASE_URL` to your PostgreSQL address, then run `alembic upgrade head` and `python -m shipdoc db seed` |
| **Scoring server** | Scores a run the official way | Put the organisers' Docker bundle **outside** this repository (it contains the answer key, which must never be committed), run `docker compose up --build -d` there, then `python -m shipdoc --submit --server-url http://localhost:8080` |

Seeding the database is safe to repeat: running it twice changes nothing. Each engine run saves a **new** run in the database instead of overwriting the last one, so earlier results are kept.

---

## Command reference

| Command | What it does |
|---|---|
| `python -m shipdoc` | Runs the engine on the whole dataset and writes `output\` (and the database, if `DATABASE_URL` is set) |
| `python -m shipdoc --no-llm` | Same, using keyword rules only |
| `python -m shipdoc --source <folder> --out <folder>` | Runs on a different set of emails |
| `python -m shipdoc doctor [--fast]` | Checks the install, dataset, config and a sample run |
| `python -m shipdoc inspect <email_id>` | Shows one email's seven fields, verdicts and evidence |
| `python -m shipdoc labels list` | Lists field labels the AI suggested, with evidence |
| `python -m shipdoc labels approve "<label>"` | Turns a suggestion into a rule from the next run |
| `python -m shipdoc labels reject "<label>"` | Rejects a suggestion so it is never asked again |
| `python -m shipdoc db seed` | Loads the emails and attachment details into the database |
| `python -m shipdoc db check` / `db counts` | Shows row counts in the database |
| `python eval\evaluate.py` | Scores the last run with the organisers' `scoring.py` (needs the organisers' bundle beside this repository) |
| `python -m pytest -q` | Runs all tests |

---

## API reference

Every route turns a request into one call on the database layer and back. Two tests make sure no verification logic ends up inside the API.

**Reads are open to everyone. Writes need the passcode** in the `X-Demo-Passcode` header (a header, not a cookie, so it still works in private / incognito windows).

| Method | Route | What it returns or does |
|---|---|---|
| GET | `/api/health` | Status, database connection, record count, AI provider |
| GET | `/api/vocabulary` | Category, status and reason labels |
| GET | `/api/runs` | List of saved runs |
| GET | `/api/runs/{run_id}/summary` | Totals for one run |
| GET | `/api/records` | All emails with category and status (filter and search) |
| GET | `/api/records/{email_id}` | One email with its seven compared fields and evidence |
| GET | `/api/records/{email_id}/source/{attachment_id}` | The original attachment file |
| GET | `/api/records/{email_id}/text/{role}` | The text read from the SI or BL |
| POST | `/api/records/{email_id}/decisions` | Saves a reviewer's confirm, correct or override (passcode needed) |
| GET | `/api/proposals` | AI-suggested field labels |
| POST | `/api/proposals/{proposal_id}` | Approves or rejects a suggestion (passcode needed) |
| GET | `/api/evaluation` | Scoring axes and the model comparison |
| GET | `/api/stats` | Dashboard totals |
| POST | `/api/try` | Runs the real engine on a pasted SI and BL. Nothing is stored and no passcode is needed |
| GET | `/api/try/sample`, `/api/try/sample2` | Example SI and BL pairs for the "Try it yourself" page |

A reviewer's decision takes effect at once, without re-running the engine. The original run is never rewritten: decisions are applied when a record is read, so you can always see what the system said before a person changed it.

---

## Testing

You do not need to understand the system to test it. Each level stands alone; stop when you have what you need. All commands assume Step 1 of "Run locally" is done and the virtual environment is active.

### Level 0: is it working?

```powershell
python -m shipdoc doctor
```

Expected: `RESULT: PASS - shipdoc is ready`.

### Level 1: do the automatic tests pass?

```powershell
python -m pytest -q
```

Expected: `776 passed` (about 50 seconds). Zero failures is what matters.

| Folder | What it covers |
|---|---|
| `tests/unit/` | One part at a time: sorting, extraction, placeholders, label matching, the review queue |
| `tests/property/` | Rules that must hold for every input: extraction never crashes, states never move backwards, layers stay separate, no secrets and no answer key in the code |
| `tests/integration/` | The whole dataset end to end, the output format and the command-line tool |
| `tests/golden/` | The 20 planted edge cases, each checked by name |

### Level 2: run it and check the output was written

```powershell
Remove-Item output\submission.json -ErrorAction SilentlyContinue
python -m shipdoc
if ($LASTEXITCODE -ne 0) { "FAILED: exit $LASTEXITCODE" }
Test-Path output\submission.json      # must print True
```

Deleting the file first proves this run wrote it, instead of an old file passing the check.

### Level 3: look at single emails

```powershell
python -m shipdoc inspect email_013
```

| Email | What you should see |
|---|---|
| `email_013` | A real defect: port of discharge is MOMBASA in the SI and TUTICORIN in the BL |
| `email_501` | Sent to review as **wrong document type**: the BL attachment is a commercial invoice, so no comparison is made |
| `email_503` | **Wrong document type**: a certificate of origin |
| `email_507` | Sent to review as **missing attachment**: only the SI was attached |
| `email_511` | Sent to review as **unreadable**: the PDF is damaged |
| `email_512` | A scanned PDF: read with OCR if installed, otherwise unreadable |
| `email_516` | Placeholders such as `N/A` and `_______ MTS`, which must never count as matching each other |

### Level 4: emails the system has never seen

```powershell
python -m pytest tests/integration/test_adversarial.py -q
python -m shipdoc --source tests\fixtures\adversarial --out output_adversarial
```

Expected: `19 passed, 1 xfailed`. The expected failure is on purpose: it only passes if someone switches on port-code resolution, which forces them to read the decision note first. The three emails test cases such as "NOTIFY PARTY: SAME AS CONSIGNEE", European number format (`22.450,50`), swapped ports and a net weight that must not be read as gross weight.

### Level 5: is it correct?

Needs the organisers' bundle beside this repository.

```powershell
python -m shipdoc
python eval\evaluate.py
```

Prints the four scoring axes, every disagreement grouped by type and the 20 edge cases, and saves them to `eval\report.txt`. The answer key is only a measuring tool: no code under `src/shipdoc/` may read it, and `tests/property/test_no_answer_key_leak.py` enforces this.

### Level 6: try to break it

```powershell
python -m pytest tests/property/test_extractor_contract.py -q
```

This feeds the file reader random bytes, 10 MB of zeros, corrupt PDFs, renamed images and fake Word files. Every case must end as a failed result that goes to review, never a crash.

### Level 7: teach it a label

```powershell
python -m shipdoc labels list
python -m shipdoc labels approve "Containers:"
python -m pytest tests/unit/test_learned_labels.py -q
```

Approved labels are saved in `config/learned_labels.yaml` with who approved them and when. Deleting that one file undoes every learned label. The system can learn that two **labels** mean the same field, but never that two **values** mean the same thing, because that would hide a real change on future shipments.

### Testing the API and website

Follow the manual test list in the frontend repository's README. It covers the health check, filters, the source viewer, the three review reasons, a reviewer decision and a phone in a private window.

---

## Common problems

| What you see | Likely cause | Fix |
|---|---|---|
| `No module named shipdoc` | Package not installed, or the virtual environment is not active | Check the prompt shows `(.venv)`, then run `pip install -e ".[dev,db,api]"` from the repository folder |
| `python` opens the Microsoft Store | No real Python installed | Install Python 3.12+ from python.org or `winget install Python.Python.3.12` |
| `Activate.ps1 cannot be loaded` | PowerShell blocks scripts by default | `Set-ExecutionPolicy -Scope CurrentUser -ExecutionPolicy RemoteSigned`, then reopen PowerShell |
| `doctor` shows `0 emails, 0 attachments` | Running from the wrong folder | `cd` to the folder that contains `pyproject.toml` |
| Commands fail halfway through a folder name | The path contains a space | Put every path in quotes |
| `Access is denied` on `output\` | OneDrive is syncing, or a file is open | Close anything using `output\`, pause OneDrive, try again |
| `/api/health` shows `provider: null` in a container | `SHIPDOC_ROOT` is not set | Set `SHIPDOC_ROOT=/app` |
| Website reads work but review actions fail | Wrong passcode, or `CORS_ORIGINS` does not match the website address exactly | Check the passcode; set `CORS_ORIGINS` to the exact address with no trailing `/` |
| `Permission denied` when mounting a folder onto `output/` in Docker | The container runs as user 10001 (not root) | Give the folder to user 10001, or read results from the database instead |

---

## Deployment (cloud)

The live system runs on **AWS** in the Singapore region:

| Part | Service |
|---|---|
| Server | EC2 t3.small, Ubuntu 24.04, fixed public IP |
| Website and HTTPS | Caddy (free Let's Encrypt certificate), domain `shipdoc.duckdns.org` |
| API | Docker container built from this repository's `Dockerfile` |
| Database | RDS PostgreSQL db.t4g.micro, private, reachable only from the server |

`deploy/aws/README.md` has the Docker Compose file, the Caddyfile and the full setup commands. Secrets (database address, DeepSeek key, demo passcode) live only in a `.env` file on the server and are never committed.

**Updating the server after a backend change:**

```bash
cd /opt/shipdoc/backend
git pull
cd ..
sudo docker compose up -d --build api
```

The container runs as a non-root user on port 8000 and includes Tesseract and the saved AI answers. The full 520-email run inside the image needs no API key and gives the same output hash on Linux and Windows.

An earlier Azure plan is kept for reference in `docs/deploy-azure.md`. It is not used by the live system.

---

## Known limitations

- **Tested on one dataset.** All scores come from the organisers' 520 emails. Results on real inbox traffic are not yet measured.
- **Email sorting is the weakest part**, especially with DeepSeek (see Results). The prompt was written against Qwen and deliberately not tuned against the answer key.
- **Port-code resolution is built but switched off** (`flags.port_resolution_enabled: false`). Measured on this dataset, it lost 15 real defect detections to gain 14, because the dataset hides port defects as a name that disagrees with its code. Read `docs/decisions/port-resolution.md` before switching it on.
- **Some emails the answer key marks OK are sent to review.** This is on purpose: the system prefers asking a person over guessing, and it catches every email that should be reviewed (escalation recall 1.000).
- **The status shown after a reviewer decision is recalculated in `adapters/projection.py`** instead of calling the engine's own rule. A test checks that both always agree, but one shared implementation would be better.
- **Demo-level security.** One shared passcode instead of individual logins, one server in one availability zone, and 1-day database backups. Fine for judging; not yet ready for real customer data.

---

## More documentation

| Location | What is in it |
|---|---|
| [deploy/aws/README.md](deploy/aws/README.md) | The live AWS deployment: Compose file, Caddy config, deploy and update commands |
| [docs/spec/](docs/spec/) | The architecture specification and its patches |
| [docs/reports/](docs/reports/) | Dataset survey and scoring analysis |
| [docs/decisions/](docs/decisions/) | Why port resolution is off, and why two output rules were kept (both measured) |
| [docs/provider-comparison.json](docs/provider-comparison.json) | The measured Qwen vs DeepSeek comparison shown on the Evaluation page |
| [docs/deploy-azure.md](docs/deploy-azure.md) | The earlier Azure plan (not used) |
| [.env.example](.env.example) | Every environment variable the system reads |
| [Dockerfile](Dockerfile) | The deployable image. Starts the API by default; `run` starts a batch run instead |
