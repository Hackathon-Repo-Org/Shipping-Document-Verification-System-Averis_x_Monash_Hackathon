# Shipping Document Verification

When a freight customer sends a **Shipping Instruction** (SI) — what they want shipped,
to whom, to which port — the carrier replies with a **draft Bill of Lading** (BL), the
document of title that actually moves the cargo. Someone has to check that the draft
says what the customer asked for, because a wrong consignee or a wrong discharge port
on a BL is expensive and slow to unwind once the vessel has sailed.

This system reads a shipping team's inbox, works out which emails are asking for that
check, and compares **seven fields** between the SI and the draft BL: shipper,
consignee, notify party, port of loading, port of discharge, container count and gross
weight. Every field gets one of three verdicts — **match**, **mismatch**, or
**cannot determine** — and that third verdict is the point: where the documents are
unreadable, the wrong document was attached, or a value was left blank, the record is
escalated to a human with the evidence attached rather than guessed at.

**Live demo: <https://shipdoc.duckdns.org>** (API health check:
<https://shipdoc.duckdns.org/api/health>). Hosted on AWS: one EC2 server runs the
website and the API, and the data lives in an RDS PostgreSQL database. Reading is
open to everyone; review actions need the demo passcode, which is given in the
submission.

## Current score

Measured **2026-09-21** against the organisers' own `scoring.py`, imported directly
rather than reimplemented.

| Axis | Weight | `deepseek` (default) | `ollama` (one config line) |
|---|---|---|---|
| Stage 1 — email classification (macro-F1) | 0.30 | 0.7591 | **0.9526** |
| Stage 3 — defect detection (F1) | 0.20 | **1.0000** · P 1.000 R 1.000 | **1.0000** · P 1.000 R 1.000 |
| End-to-end — defects caught exactly | 0.50 | **1.0000** — 46/46 | **1.0000** — 46/46 |
| Reliability — escalation (diagnostic, unweighted) | 0.00 | recall **1.000** | recall **1.000** |
| **Final** | | **0.9277** | **0.9858** |

All **20/20** planted edge cases correct on both. 776 tests pass. Two runs produce
byte-identical output.

> **The default is the hosted model, and that is a deliberate trade.**
> `deepseek-chat` ships as the default because the deployed target is a CPU-only AWS
> EC2 server with no GPU, so a hosted API is the realistic path for new email traffic.
> It costs **0.058 of final score** on this corpus.
>
> **Defect detection and end-to-end are identical**: both providers find all 46
> defect emails exactly. The whole difference is stage-1 classification, and almost
> all of *that* is one systematic error — deepseek reads 106 gold `SI_REQUEST` emails
> as `BL_COMPARISON`, where qwen gets 124 of 125 right. The prompt was developed
> against qwen and never adapted; rewriting it until the hosted number improved would
> be tuning against the answer key.
>
> **For 0.9858, change one line** in `config/pipeline.yaml` to `provider: ollama`.
> Neither option needs an API key or a GPU — the committed cache answers every prompt
> for both models.

**Two numbers, and the difference is the point.** Deleting
`config/learned_labels.yaml` returns the system to hand-written rules only, byte for
byte. The four approved label mappings are what close the last three end-to-end
misses. Every run records which vocabulary produced it, in `run_summary.json` →
`learned_labels_sha256`.

**On that precision of 1.000:** it is measured on the provided 520-email corpus, and
it is a fact about that corpus rather than a property of the system — on three
hand-written adversarial emails the same build reported four false defects before
Phase 10 and two after, both of which are the known bare-UN/LOCODE case. Those three
emails now live in `tests/fixtures/adversarial/` and run in CI alongside the 520.

## Which AI model — measured, not assumed

The classifier sits behind a one-method protocol, so the provider is a config line.
Both were run over the same corpus with the same prompts; cache keys include the
model name, so the hosted run missed every local entry and genuinely re-queried.

| | `qwen2.5:7b-instruct` (local, Ollama) | `deepseek-chat` (hosted) |
|---|---|---|
| Stage 1 — macro-F1 | **0.9526** | 0.7591 |
| Stage 3 — defect-F1 | 1.0000 | 1.0000 |
| End-to-end | 46/46 | 46/46 |
| **Final score** | **0.9858** | **0.9277** |
| Attachment-free 394 (macro-F1) | **0.9425** | 0.7246 |
| Replies not in the closed vocabulary (of 33) | 23 | **0** |
| Cost | free, needs a GPU | cents, no GPU |

**The counterintuitive part is the useful part.** DeepSeek is much better at
*following the format* — zero malformed replies against qwen's 23, no echoing the
label back, no inventing field names — and much worse at *this corpus's category
boundary*: it calls 106 `SI_REQUEST` emails `BL_COMPARISON`, where qwen gets 124 of
125 right.

The prompt was developed against qwen and never adapted for DeepSeek. Rewriting it
until the hosted number improves would be tuning against the answer key, so it was
not done — the finding is reported instead. Full analysis, including a tempting
explanation that was **tested and falsified**, is in
[HANDOVER.md](HANDOVER.md) under *Provider comparison*.

**`deepseek-chat` ships as the default** (changed 2026-09-21), because the deployed
target has no GPU and a hosted API is the realistic path for new email traffic.

**`ollama` remains fully supported and scores higher** — one config line, and it is
also the **offline / air-gapped** option: the system runs with no outbound network at
all, which is a real requirement for a freight operator handling commercial
documents. Removing it was never on the table.

Neither needs an API key for the 520-record corpus: the committed cache holds both
models' answers, keyed by model name so they never collide.

## The web interface

A reviewer UI and a thin HTTP API ship alongside the CLI. **They are built separately**
and talk only over HTTP. In the live deployment both sit behind one address on AWS:
Caddy serves the frontend's static bundle and forwards `/api/*` to the backend
container (see [deploy/aws/README.md](deploy/aws/README.md)).

**Frontend repository:**
<https://github.com/Hackathon-Repo-Org/Front-End-Shipping-Document-Verification-System-Averis_x_Monash_Hackathon_Version-1.0>

```powershell
# backend
$env:DATABASE_URL="sqlite:///output/shipdoc.db"
$env:DEMO_PASSCODE="averis2026"
$env:CORS_ORIGINS="http://localhost:5173"
python -m shipdoc db seed
python -m shipdoc
python -m uvicorn shipdoc.adapters.api.app:app --port 8000

# frontend, in another terminal
cd ui; npm install; npm run dev        # http://localhost:5173
```

| Screen | For |
|---|---|
| Dashboard | counts, the four axes, the AI's share of decisions, cache-hit rate, run hashes |
| Inbox | filter and search; **filter state lives in the URL** so a view is shareable |
| Email detail | seven fields side by side — **click any value to open its source at the highlighted line** |
| Review queue | keyboard-first (`J`/`K`/`Enter`/`A`/`C`/`E`/`R`/`N`), built for fifty in a row |
| Label proposals | approve or reject what a model suggested, signed with a name |
| Evaluation | the four axes and the provider comparison |
| **Try it yourself** | **paste your own SI and BL and watch the real engine run** |

### Try it yourself

`/try` accepts a pasted shipping instruction and draft bill of lading and runs **the
real engine** over them — same normaliser, same comparators, same state machine as
the 520-record batch. Only ingest and extract are skipped, because the text arrived
as text.

It needs no passcode, **stores nothing**, and uses no API key, so it cannot be made
to burn credits by being refreshed. "Load a worked example" prefills a realistic pair
with one planted defect, so the first click finds something instead of returning OK
and looking broken.

### The API

Thin by construction: every route turns a request into one call on the repository
interface and back. Two tests enforce it — one parses the handlers with `ast` looking
for engine vocabulary, one forbids importing `compare/` or `state/`.

`GET /api/health · /api/vocabulary · /api/runs · /api/records · /api/records/{id} ·
/api/records/{id}/source/{attachment} · /api/proposals · /api/evaluation · /api/stats`
· `POST /api/records/{id}/decisions · /api/proposals/{id} · /api/try · /api/demo/reset`

**Reads are open; writes need a passcode** sent as the `X-Demo-Passcode` header,
never a cookie — the two halves are different origins, and a third-party cookie is
blocked in incognito, which is how a judge opens a link.

**A decision takes effect immediately, with no pipeline re-run.** Decisions carry no
`run_id`: they are about the email, not one pass over it. The stored run is never
rewritten; decisions are applied when a record is read, so the audit question "what
did the system say before a human touched it?" keeps its answer.

## Deployment

**Live on AWS** at <https://shipdoc.duckdns.org>:

```
Browser --HTTPS--> EC2 (Ubuntu, t3.small, Singapore)
                     +-- Caddy: website + HTTPS certificate, forwards /api/* to the API
                     +-- shipdoc API container (this repository's Dockerfile)
                           |
                           v
                   RDS PostgreSQL (private, reachable only from the EC2 server)
```

[deploy/aws/README.md](deploy/aws/README.md) has the Compose file, the Caddy config
and the exact deploy and update commands. Secrets (database URL, DeepSeek key, demo
passcode) live only in a `.env` file on the server and are never committed.

`docs/deploy-azure.md` is the earlier Azure runbook, kept for reference; the live
deployment moved to AWS. `SETUP.md` tiers 4–6 cover the database and the container
in more detail.

The container was rehearsed locally: non-root (uid 10001), port 8000, Tesseract
present, the LLM cache baked in, and **the full 520-record run completes inside the
image with no API key and reproduces the published hash.**

## Quick start

Python 3.12+. Nothing else — no Docker, no GPU, no model download.

```powershell
git clone https://github.com/Hackathon-Repo-Org/Shipping-Document-Verification-System-Averis_x_Monash_Hackathon.git
cd Shipping-Document-Verification-System-Averis_x_Monash_Hackathon
python -m venv .venv
.\.venv\Scripts\Activate.ps1
pip install -e ".[dev]"
python -m shipdoc doctor
```

`doctor` checks the install, the corpus, the config and a live sample run, then prints
`RESULT: PASS`. Optional capabilities it cannot find (OCR, the LLM) are reported as
`[--  ]` and are **expected to be missing** — the system runs without them and says
what it lost.

Then:

```powershell
python -m shipdoc                      # full run -> output\
python -m shipdoc inspect email_013    # one record, seven fields, side by side
python -m pytest -q                    # 776 tests
```

## Why the model cache is committed

`cache/llm/` holds this system's **own model outputs**, produced at temperature 0, from
**both** providers — `cache/llm/MANIFEST.json` lists every model that has written
there. Cache keys include the model name, so entries never collide and swapping
providers re-queries rather than silently reusing the other model's answers.
Committing it means a fresh clone reproduces the published score in seconds with no
Ollama installed, and it is what makes runs byte-identical — temperature 0 alone does
not guarantee that, because batching and GPU reduction order vary between calls.

**It is not the answer key and contains no gold labels.** Each filename is a
`sha256(model + prompt_version + prompt + choices)` — irreversible, so no email text
is stored — and each file's entire contents is one short string this system's own
models produced: either one of the five category strings (the classifier), or one
compared-field name, `NONE`, or a rejected free-text reply (the Phase 11 label
proposer). Nothing in it is derived from the organisers' labels. The model name and
prompt version are recorded in `cache/llm/MANIFEST.json`, and they are part of every
cache key, so a cache built by a different model misses and re-queries rather than
silently returning the wrong answers.

Checkable rather than asserted — clear it and rebuild (needs Ollama and
`qwen2.5:7b-instruct`, ~15 minutes):

```powershell
Remove-Item -Recurse -Force cache\llm
python -m shipdoc                      # re-queries the model, rewrites the cache
```

## Documentation

| File | For |
|---|---|
| **[SETUP.md](SETUP.md)** | Installing, in five tiers — laptop, OCR, LLM, database, Azure. Most people need only Tier 1. |
| **[TESTING.md](TESTING.md)** | Testing and inspecting, in six levels, without needing to understand the system. |
| **[HANDOVER.md](HANDOVER.md)** | Current state, what changed, what is known broken. |
| **[docs/spec/](docs/spec/)** | The architecture specification and its patches. |
| **[docs/reports/](docs/reports/)** | Corpus survey, scoring-rubric analysis. |
| **[docs/decisions/](docs/decisions/)** | Why port resolution is off, and why projection rows 2 and 3 were kept — both measured. |
| **[Dockerfile](Dockerfile)** | The deployable image. Starts the API by default; `run` gives the batch job. No secrets in it. |
| **[deploy/aws/README.md](deploy/aws/README.md)** | The live AWS deployment: Compose file, Caddy config, deploy and update commands. |
| **[.env.example](.env.example)** | Every environment variable this system reads. Keys and connection strings come from the environment only. |
| **[docs/deploy-azure.md](docs/deploy-azure.md)** | The earlier Azure deploy sequence, kept for reference (the live deployment is on AWS). |
| **[docs/design-notes.md](docs/design-notes.md)** | What the UI adopted from the team's prototype, and what it did not. |
| **[ui/README.md](ui/README.md)** | The reviewer UI — screens, shortcuts, configuration. |

## Not built yet

UN/LOCODE port-code resolution is built, tested and **switched off**: measured against
the answer key it destroyed more real defect detections than it created, because this
corpus writes port defects as a name/code contradiction. The reasoning and the fix
direction are recorded; the flag is one boolean.

A `BACKUP/` directory of superseded specifications and experiment output exists in the
authors' working copy and is deliberately **not published** — nothing was deleted, it
is simply not part of this repository.
