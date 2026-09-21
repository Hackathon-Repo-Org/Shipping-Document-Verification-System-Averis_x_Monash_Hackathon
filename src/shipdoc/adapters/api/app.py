"""M18 — the HTTP API. Phase 14.

    THE API IS A TRANSLATION LAYER. NO LOGIC LIVES HERE.

Every route below turns a request into one call on the Phase 13 repository interface
and turns the result back into JSON. If a comparison rule or a state transition ever
appears in a handler, it is in the wrong file — it belongs in the engine, where it is
tested by 737 tests and reproducible from a clean clone.

That is not tidiness. The engine's guarantees (immutable runs, monotone state,
decisions that outlive runs, no gold labels) hold because exactly one place decides
each of them. A handler that "just tweaks" a status is a second decider.

WHY THIS IS AN ADAPTER AND NOT A NEW PACKAGE
--------------------------------------------
`adapters/` is the top layer, so this may import freely and nothing may import it.
Putting the API here rather than at `src/shipdoc/api/` means the layering test needs
no new entry and the architecture makes the same statement it always did: the outside
world talks to this system through adapters.

DEMO SAFETY (Part 3)
--------------------
  * Reads are OPEN. A judge must be able to browse without a code.
  * WRITES need a passcode, sent as a custom header. That header is what makes every
    write a preflighted CORS request — see the CORS block below, which is where this
    breaks in the cloud.
  * The corpus demo answers from the COMMITTED CACHE: no model call, no API key,
    nothing that can rate-limit halfway through judging.
  * Every failure path returns a readable message. A stack trace in a response body
    is both a bad demo and an information leak.
"""
from __future__ import annotations

import os
import uuid
from pathlib import Path
from typing import Any

from fastapi import Depends, FastAPI, Header, HTTPException, Query, Response
from fastapi.middleware.cors import CORSMiddleware
from fastapi.responses import JSONResponse

def _repo_root() -> Path:
    """Where `config/`, `dataset/` and `cache/` live.

    NOT derived from `__file__`. When the package is pip-installed — which is exactly
    what the container does — `__file__` is inside site-packages and walking up four
    parents lands in `/usr/local/lib/python3.12`, where there is no config. The API
    then reports `provider: null` and a degraded health check, and the cause looks
    like anything except a path.

    Found by running the image rather than by reading the code. Resolution order:
      1. SHIPDOC_ROOT, for an explicit deployment layout
      2. the working directory, if it looks like the project (this is the container,
         which sets WORKDIR /app)
      3. four parents up from this file, for `pip install -e .` on a laptop
    """
    env = (os.environ.get("SHIPDOC_ROOT") or "").strip()
    if env and (Path(env) / "config").is_dir():
        return Path(env)
    cwd = Path.cwd()
    if (cwd / "config" / "fields.yaml").is_file():
        return cwd
    guess = Path(__file__).resolve().parents[4]
    return guess if (guess / "config").is_dir() else cwd


REPO_ROOT = _repo_root()

# The passcode header. A CUSTOM header, deliberately, not Authorization: it is a
# shared demo code, not a credential, and calling it Authorization would invite
# someone to treat it as one.
PASSCODE_HEADER = "X-Demo-Passcode"

DEFAULT_DB = "sqlite:///output/shipdoc.db"


def _origins() -> list[str]:
    """Exact origins from the environment. NEVER "*".

    Two reasons it cannot be "*":
      1. Browsers reject a wildcard origin once credentials or custom headers are in
         play, and our writes carry a custom header.
      2. A wildcard on a public URL invites anyone's page to drive this API.

    The local dev origin comes from the same env var rather than being hardcoded, so
    a deployment cannot accidentally ship a localhost exception.
    """
    raw = os.environ.get("CORS_ORIGINS", "http://localhost:5173,http://127.0.0.1:5173")
    return [o.strip().rstrip("/") for o in raw.split(",") if o.strip()]


def get_repo():
    """The repository, or a clear 503. Never a stack trace."""
    from shipdoc.adapters.db import build_repository

    url = (os.environ.get("DATABASE_URL") or "").strip() or DEFAULT_DB
    try:
        repo = build_repository(url)
    except Exception as e:  # noqa: BLE001 - surfaced as a readable message
        raise HTTPException(
            503, detail=f"The database is not reachable ({type(e).__name__}). "
                        f"If this is a deployment, check that the container's "
                        f"outbound IP is allowed by the PostgreSQL firewall.") from None
    if repo is None:
        raise HTTPException(503, detail="No database is configured.")
    return repo


def require_passcode(passcode: str | None = Header(None, alias=PASSCODE_HEADER)):
    """WRITE actions only. Reads stay open so judges can browse.

    When no passcode is configured the API is read-only rather than wide open:
    failing closed is the only safe default for something with a public URL.
    """
    expected = (os.environ.get("DEMO_PASSCODE") or "").strip()
    if not expected:
        raise HTTPException(
            503, detail="This deployment is read-only: no demo passcode is "
                        "configured, so write actions are disabled.")
    if (passcode or "").strip() != expected:
        raise HTTPException(
            401, detail="Wrong or missing passcode. The code is on the slide; "
                        f"send it in the {PASSCODE_HEADER} header.")
    return True


def create_app() -> FastAPI:
    app = FastAPI(
        title="shipdoc API",
        version="1.0",
        description="Thin HTTP wrapper over the shipdoc engine. No logic lives here.",
    )

    # CORS. The single most common reason a split deployment fails.
    #
    # `allow_headers` MUST include the passcode header, and `allow_methods` MUST
    # include OPTIONS, or the browser's preflight for a write is refused — and the
    # symptom is "reads work, writes fail", which looks exactly like a backend bug
    # and is not one.
    app.add_middleware(
        CORSMiddleware,
        allow_origins=_origins(),
        allow_credentials=False,          # header auth, never cookies: see below
        allow_methods=["GET", "POST", "OPTIONS"],
        allow_headers=["Content-Type", PASSCODE_HEADER],
        max_age=600,
    )

    @app.exception_handler(Exception)
    async def _unhandled(request, exc):   # noqa: ANN001
        """A readable message, never a traceback. Part 3."""
        return JSONResponse(
            status_code=500,
            content={"detail": f"Unexpected server error ({type(exc).__name__}). "
                               f"The run artifacts on disk are unaffected."})

    _register(app)
    return app


def _register(app: FastAPI) -> None:      # noqa: C901 - a route table, not logic
    from shipdoc.adapters.db.repository import RecordFilter

    # ---------------------------------------------------------------- health
    @app.get("/api/health")
    def health() -> dict:
        """Cheap and honest: says what is actually wired up.

        The UI polls this so it can say "API: unreachable" instead of rendering an
        empty dashboard that looks like a legitimate zero.
        """
        from shipdoc.config import load_config
        out: dict[str, Any] = {"status": "ok", "database": False, "records": 0}
        try:
            cfg = load_config(REPO_ROOT / "config")
            out["provider"] = cfg.llm.provider
            out["model"] = cfg.llm.model
            out["learned_labels"] = len(cfg.learned.approved)
        except Exception:                 # noqa: BLE001
            out["status"] = "degraded"
        try:
            repo = get_repo()
            counts = repo.counts()
            out["database"] = True
            out["records"] = counts.get("records", 0)
            out["runs"] = counts.get("runs", 0)
        except HTTPException as e:
            out["status"] = "degraded"
            out["detail"] = e.detail
        out["writes_enabled"] = bool((os.environ.get("DEMO_PASSCODE") or "").strip())
        return out

    # ------------------------------------------------------------ vocabulary
    @app.get("/api/vocabulary")
    def vocabulary() -> dict:
        """Every string the UI renders. The UI hardcodes none of them."""
        from shipdoc.adapters.api import vocabulary as vocab
        from shipdoc.config import load_config
        return vocab.build(load_config(REPO_ROOT / "config"))

    # ------------------------------------------------------------------ runs
    @app.get("/api/runs")
    def list_runs(repo=Depends(get_repo)) -> list[dict]:
        return repo.list_runs()

    @app.get("/api/runs/{run_id}/summary")
    def run_summary(run_id: str, repo=Depends(get_repo)) -> dict:
        s = repo.run_summary(_uuid(run_id))
        if s is None:
            raise HTTPException(404, detail=f"No run {run_id}")
        return s

    # --------------------------------------------------------------- records
    @app.get("/api/records")
    def list_records(
        category: str | None = None,
        status: str | None = None,
        review_reason: str | None = None,
        awaiting_documents: bool | None = None,
        decided: bool | None = None,
        q: str | None = None,
        limit: int = Query(50, ge=1, le=500),
        offset: int = Query(0, ge=0),
        repo=Depends(get_repo),
    ) -> dict:
        """`awaiting_documents` defaults to EXCLUDED, not included.

        Those ~90 records are a shipper saying "the draft is coming" — they are OK,
        not work, and mixing them into the queue buries the handful of genuinely
        broken records under routine correspondence. Passing the filter explicitly
        is how you see them.
        """
        return repo.list_records_page(RecordFilter(
            category=category, status=status, review_reason=review_reason,
            awaiting_documents=awaiting_documents, decided=decided, q=q,
            limit=limit, offset=offset))

    @app.get("/api/records/{email_id}")
    def record_detail(email_id: str, repo=Depends(get_repo)) -> dict:
        d = repo.get_record_detail(email_id)
        if d is None:
            raise HTTPException(404, detail=f"No record for {email_id}")
        return d

    @app.get("/api/records/{email_id}/source/{attachment_id}")
    def record_source(email_id: str, attachment_id: str, repo=Depends(get_repo)):
        """The document behind a value — the evidence viewer's source.

        Serves a Blob SAS URL when storage is configured, and otherwise PROXIES the
        bytes from the corpus. The proxy is not a fallback for convenience: without
        it the evidence viewer works on a laptop and breaks in the cloud, which is
        the failure Part 4.5 warns about.
        """
        meta = repo.get_attachment(email_id, attachment_id)
        if meta is None:
            raise HTTPException(404, detail=f"No attachment {attachment_id}")
        if meta.get("blob_url"):
            return {"kind": "url", "url": meta["blob_url"],
                    "filename": meta["filename"],
                    "content_type": meta.get("content_type")}
        data = _read_corpus_bytes(meta["filename"])
        if data is None:
            raise HTTPException(404, detail=f"{meta['filename']} is not available.")
        return Response(
            content=data,
            media_type=meta.get("content_type") or "application/octet-stream",
            headers={"Content-Disposition":
                     f'inline; filename="{meta["filename"]}"',
                     "Cache-Control": "public, max-age=3600"})

    @app.get("/api/records/{email_id}/text/{role}")
    def record_text(email_id: str, role: str, repo=Depends(get_repo)) -> dict:
        """The extracted text with line numbers — what the highlight points at.

        The viewer highlights a LINE, and the line numbers in the evidence refer to
        the text this system extracted, not to the bytes of the original PDF. Serving
        the extraction is the only way a highlight can be accurate.
        """
        detail = repo.get_record_detail(email_id)
        if detail is None:
            raise HTTPException(404, detail=f"No record for {email_id}")
        text = _extract_text(email_id, role.upper())
        if text is None:
            raise HTTPException(404, detail=f"No {role} document for {email_id}")
        return {"email_id": email_id, "role": role.upper(),
                "lines": text.splitlines()}

    # ------------------------------------------------------------- decisions
    @app.post("/api/records/{email_id}/decisions")
    def add_decision(email_id: str, body: dict, repo=Depends(get_repo),
                     _ok=Depends(require_passcode)) -> dict:
        """Takes effect IMMEDIATELY, at projection time. No pipeline re-run.

        That is the whole point of decisions carrying no run_id (R3): the answer is
        about the email, not about one pass over it. The record's NEW status is
        returned so the UI updates without refetching, and the next run inherits the
        decision for free.
        """
        dtype = str(body.get("decision_type") or "").strip()
        reviewer = str(body.get("reviewer") or "").strip()
        if not reviewer:
            raise HTTPException(422, detail="A reviewer name is required: an "
                                            "unattributed decision is not auditable.")
        valid = {d["value"] for d in _decision_types()}
        if dtype not in valid:
            raise HTTPException(422, detail=f"decision_type must be one of "
                                            f"{sorted(valid)}")
        try:
            result = repo.apply_decision(
                email_id=email_id, field=body.get("field"), decision_type=dtype,
                reviewer=reviewer, verdict=body.get("verdict"),
                corrected_value=body.get("corrected_value"),
                corrected_category=body.get("corrected_category"),
                note=str(body.get("note") or ""))
        except KeyError:
            raise HTTPException(404, detail=f"No record for {email_id}") from None
        return result

    # ------------------------------------------------------------- proposals
    @app.get("/api/proposals")
    def list_proposals(status: str = "pending", repo=Depends(get_repo)) -> list[dict]:
        return repo.list_proposals(status)

    @app.post("/api/proposals/{proposal_id}")
    def decide_proposal(proposal_id: int, body: dict, repo=Depends(get_repo),
                        _ok=Depends(require_passcode)) -> dict:
        """AI proposes, a HUMAN approves. `reviewer` is required and is a person."""
        decision = str(body.get("decision") or "").strip()
        reviewer = str(body.get("reviewer") or "").strip()
        if decision not in ("approved", "rejected"):
            raise HTTPException(422, detail="decision must be 'approved' or 'rejected'")
        if not reviewer:
            raise HTTPException(422, detail="A reviewer name is required: an "
                                            "approval with no name is not auditable.")
        try:
            repo.decide_proposal(proposal_id, decision, reviewer,
                                 str(body.get("note") or ""))
        except KeyError:
            raise HTTPException(404, detail=f"No proposal {proposal_id}") from None
        except ValueError as e:
            raise HTTPException(422, detail=str(e)) from None
        return {"proposal_id": proposal_id, "status": decision,
                "decided_by": reviewer}

    # ------------------------------------------------------------ evaluation
    @app.get("/api/evaluation")
    def evaluation(repo=Depends(get_repo)) -> dict:
        """The four axes, plus the measured provider comparison.

        The comparison is served from a committed file rather than recomputed: it
        took two full runs and a paid API to produce, and a dashboard must not be
        able to trigger that by being refreshed.
        """
        import json
        latest = repo.latest_evaluation()
        providers = {}
        p = REPO_ROOT / "docs" / "provider-comparison.json"
        if p.is_file():
            try:
                providers = json.loads(p.read_text(encoding="utf-8"))
            except (OSError, json.JSONDecodeError):
                providers = {}
        return {"latest": latest, "providers": providers}

    # ----------------------------------------------------------------- stats
    @app.get("/api/stats")
    def stats(repo=Depends(get_repo)) -> dict:
        return repo.dashboard_stats()


    # ------------------------------------------------------ try it yourself
    MAX_CHARS = 20000

    @app.post("/api/try")
    def try_it(body: dict) -> dict:
        """Run ONE pasted email through the real engine. Nothing is stored.

        Open without a passcode, on purpose: this is how a visitor convinces
        themselves the system does what the dashboard claims, and gating it behind a
        code they do not have defeats the point. It is safe to leave open because it
        writes nothing, needs no API key, and is bounded in size.

        Same `Normaliser`, same `compare_all`, same `evaluate` as the batch run. Only
        ingest and extract are skipped, because the text arrived as text.
        """
        subject = str(body.get("subject") or "")[:2000]
        email_body = str(body.get("body") or "")[:MAX_CHARS]
        si = str(body.get("si_text") or "")[:MAX_CHARS]
        bl = str(body.get("bl_text") or "")[:MAX_CHARS]
        if not (subject or email_body or si or bl):
            raise HTTPException(422, detail="Paste at least a subject or a body.")

        from shipdoc.config import load_config
        from shipdoc.pipeline import process_adhoc, build_llm
        try:
            cfg = load_config(REPO_ROOT / "config")
            llm = build_llm(cfg, REPO_ROOT / "output", cache_dir=REPO_ROOT / "cache" / "llm")
            extra_labels = body.get("extra_labels") or []
            return process_adhoc(subject=subject, body=email_body,
                                 si_text=si, bl_text=bl,
                                 cfg=cfg, llm=llm,
                                 extra_labels=extra_labels)
        except Exception as e:  # noqa: BLE001 - a visitor gets a message, not a trace
            raise HTTPException(
                500, detail=f"Could not process that input "
                            f"({type(e).__name__}). The pasted text is not stored, "
                            f"so nothing was affected.") from None

    @app.get("/api/try/sample")
    def try_sample() -> dict:
        """A worked example to prefill the form.

        An empty textarea is a wall. This is a realistic SI/BL pair carrying one
        planted defect (the discharge port) so a visitor sees the system find
        something on their first click rather than staring at OK.
        """
        from shipdoc.adapters.api.samples import SAMPLE
        return SAMPLE

    @app.get("/api/try/sample2")
    def try_sample2() -> dict:
        from shipdoc.adapters.api.samples import SAMPLE2
        return SAMPLE2

    # ------------------------------------------------------------ demo reset
    @app.post("/api/demo/reset")
    def reset_demo(repo=Depends(get_repo), _ok=Depends(require_passcode)) -> dict:
        """Restore the seeded state so nobody can permanently break the public link.

        Clears human decisions and proposal rulings; leaves runs and records alone,
        because those are what the demo is showing.
        """
        removed = repo.reset_demo()
        return {"reset": True, **removed}


# ------------------------------------------------------------------- helpers

def _decision_types() -> list[dict]:
    from shipdoc.adapters.api.vocabulary import DECISION_TYPES
    return DECISION_TYPES


def _uuid(value: str) -> uuid.UUID:
    try:
        return uuid.UUID(value)
    except (ValueError, AttributeError):
        raise HTTPException(422, detail=f"{value!r} is not a run id") from None


def _read_corpus_bytes(filename: str) -> bytes | None:
    from shipdoc.ingest.loader_port import LoaderInbox
    try:
        inbox = LoaderInbox(str(REPO_ROOT / "dataset"))
        return inbox.read_bytes(f"attachments/{filename}")
    except Exception:                     # noqa: BLE001
        return None


def _extract_text(email_id: str, role: str) -> str | None:
    """Re-extract one document's text, for the highlight viewer.

    Uses the SAME extractor the pipeline used, through the shared cache, so the line
    numbers the evidence refers to and the lines shown here cannot disagree.
    """
    from shipdoc.config import load_config
    from shipdoc.detect.mime import detect
    from shipdoc.extract.registry import default_registry
    from shipdoc.ingest.loader_port import LoaderInbox

    suffix = {"SI": "_SI", "BL": "_BL"}.get(role)
    if suffix is None:
        return None
    cfg = load_config(REPO_ROOT / "config")
    inbox = LoaderInbox(str(REPO_ROOT / "dataset"))
    for email in inbox.emails():
        if email["email_id"] != email_id:
            continue
        for path in (email.get("attachments") or ()):
            if suffix not in Path(path).stem.upper():
                continue
            data = inbox.read_bytes(path)
            doc = default_registry(ocr_enabled=cfg.flags.ocr_enabled).extract(
                data, path, detect(data, path).mime,
                cfg.thresholds.min_extract_chars)
            return doc.text if doc.ok else None
    return None


app = create_app()
