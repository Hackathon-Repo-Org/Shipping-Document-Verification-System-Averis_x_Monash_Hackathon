"""M13 — orchestration. Sits above everything and imports freely.

Two rules carry the design here:
  * `run_stage` is one of exactly two broad `except Exception` sites, and its FAILED
    assignment is UNCONDITIONAL — v1.0's `if state is None` preserved a half-assigned
    RESOLVED when the evaluator raised after setting it.
  * The pipeline SHORT-CIRCUITS after an escalating stage. v1.0 ran every stage
    unconditionally, which is how an escalated record still reached `evaluate` with an
    empty comparison map.
"""
from __future__ import annotations

import json
import os
import sys
import tempfile
import traceback
from collections import Counter
from pathlib import Path
from typing import Any, Callable, Mapping

from shipdoc.adapters.submission import SubmissionAdapter
from shipdoc.errors import LLMUnavailable, ShipdocError, reason_key
from shipdoc.state.machine import evaluate
from shipdoc.types import (
    Config,
    Decision,
    Record,
    RecordState,
    ReasonKey,
    StageEvent,
)


def _halted(rec: Record) -> bool:
    """True iff a stage escalated or failed.

    Exact, and it depends on HARD RULE 4: before `evaluate` runs, the only writer of
    `state` is `run_stage`. `tests/test_registry_guard.py` asserts that rule rather
    than trusting it.
    """
    return rec.state is not None


def run_stage(rec: Record, fn: Callable[[Record, Config], Record],
              name: str, cfg: Config) -> Record:
    """The ONLY broad `except Exception` outside the extractor decorator."""
    try:
        return fn(rec, cfg)
    except ShipdocError as e:
        from shipdoc.state.machine import raise_state
        raise_state(rec, RecordState.ESCALATED, reason_key(e))
        rec.trace.append(StageEvent(name, "escalated", str(e), seq=len(rec.trace)))
        return rec
    except Exception as e:  # noqa: BLE001 — deliberate, see docstring
        rec.state, rec.reason = RecordState.FAILED, ReasonKey.ERR_UNHANDLED
        rec.trace.append(
            StageEvent(name, "failed", f"{type(e).__name__}: {e}\n{traceback.format_exc()}",
                       seq=len(rec.trace)))
        return rec


def stage_classify(rec: Record, cfg: Config) -> Record:
    from shipdoc.classify.arbiter import classify
    how: dict = {}
    category, confidence = classify(rec, cfg, _LLM.get("client"), trace=how)
    rec.category = category
    rec.decided_by = how.get("decided_by")
    rec.trace.append(StageEvent(
        "classify", "ok" if category else "undecided",
        f"{category} conf={confidence:.2f} via={how.get('source', '?')}",
        seq=len(rec.trace)))
    return rec


# The LLM handle is threaded through a module-level slot rather than through every
# stage signature, because `run_stage` fixes the stage signature at (rec, cfg).
_LLM: dict[str, Any] = {"client": None}


def stage_route(rec: Record, cfg: Config) -> Record:
    from shipdoc.classify.intent import awaiting_documents
    from shipdoc.route.by_name import route_by_name

    # An attachment-free comparison request is usually the shipper ASKING for the
    # draft BL, not a broken submission. Decide from the body before calling it a
    # missing attachment (review item: the queue was 85 of these).
    if not (rec.raw.get("attachments") or ()) and awaiting_documents(rec):
        rec.awaiting_docs = True
        rec.trace.append(StageEvent("route", "ok", "awaiting_documents",
                                    seq=len(rec.trace)))
        return rec

    _CTX["roles"][rec.email_id] = route_by_name(rec)
    return rec


def stage_extract(rec: Record, cfg: Config) -> Record:
    from shipdoc.detect.mime import detect
    from shipdoc.errors import ExtractionError

    inbox, registry = _CTX["inbox"], _CTX["registry"]
    for role, path in _CTX["roles"][rec.email_id].items():
        data = inbox.read_bytes(path)
        det = detect(data, path)
        if det.disagrees:
            # A signal, recorded — never a failure. It may be the planted
            # wrong_doc_type case, or a customer whose export names files badly.
            rec.trace.append(StageEvent(
                "detect", "ok",
                f"{path}: claimed {det.extension_claimed}, detected {det.mime}",
                seq=len(rec.trace)))
        doc = registry.extract(data, path, det.mime,
                               cfg.thresholds.min_extract_chars,
                               cache=_CTX["doccache"])
        rec.documents[role] = doc
        if not doc.ok:
            raise ExtractionError(f"{path}: {doc.failure}")
    return rec


def stage_confirm(rec: Record, cfg: Config) -> Record:
    from shipdoc.route.confirm import confirm_doc_type
    confirm_doc_type(rec.documents, cfg, _CTX["label_index"])
    return rec


def stage_normalise(rec: Record, cfg: Config) -> Record:
    normaliser = _CTX["normaliser"]
    for role, doc in rec.documents.items():
        values, seen = normaliser.normalise_with_labels(
            doc, _CTX["roles"][rec.email_id].get(role, role))
        rec.fields[role] = values
        rec.labels_seen[role] = seen
        # Phase 11. Notice unfamiliar label-shaped lines. This only RECORDS them;
        # proposing is a separate step and approving is a human one.
        if rec.category == cfg.comparison_category:
            from shipdoc.normalise.unknown import find_unknown
            rec.unknown_labels.extend(find_unknown(
                doc.text, _CTX["label_index"],
                _CTX["roles"][rec.email_id].get(role, role), role))
    return rec


def stage_compare(rec: Record, cfg: Config) -> Record:
    from shipdoc.compare.comparators import compare_all
    # Flag OFF => the resolver is not passed, so cmp_port takes the pre-Phase-5
    # string path exactly. Not an equivalent path — the same one.
    resolver = (_CTX["normaliser"].ports
                if cfg.flags.port_resolution_enabled else None)
    rec.comparisons = compare_all(rec.fields.get("SI", {}), rec.fields.get("BL", {}),
                                  cfg, resolver)
    return rec


# Per-run collaborators the (rec, cfg) stage signature cannot carry.
_CTX: dict[str, Any] = {"inbox": None, "registry": None, "normaliser": None,
                        "roles": {}, "doccache": None, "cache_dir": None,
                        "label_index": None, "llm_metrics": None}


def process(rec: Record, cfg: Config, llm: Any,
            decisions: Mapping[tuple[str, str], Decision]) -> Record:
    """Per-record stage sequence.

    The SHORT-CIRCUIT is load-bearing: v1.0 ran every stage unconditionally after a
    raising stage, which is how an escalated record still reached `evaluate` with an
    empty comparison map and was reported clean.

    """
    _LLM["client"] = llm
    rec = run_stage(rec, stage_classify, "classify", cfg)

    if rec.category == cfg.comparison_category and rec.state is not RecordState.FAILED:
        for fn, name in ((stage_route, "route"), (stage_extract, "extract"),
                         (stage_confirm, "confirm"), (stage_normalise, "normalise"),
                         (stage_compare, "compare")):
            if _halted(rec) or rec.awaiting_docs:
                break
            rec = run_stage(rec, fn, name, cfg)

    # Decisions are an INPUT, applied before evaluate so a human answer can move a
    # record out of escalation in the same single pass.
    from shipdoc.review.queue import apply_decisions
    rec = apply_decisions(rec, decisions)

    rec = evaluate(rec, cfg)                      # the ONLY state authority
    assert rec.state is not None, f"T1 violated: {rec.email_id}"
    return rec


def run_corpus(inbox, cfg: Config, llm: Any = None,
               decisions: Mapping[tuple[str, str], Decision] | None = None,
               cache_dir: Path | str | None = None) -> dict:
    """Process the whole corpus and project it. Returns the artifacts, unwritten."""
    decisions = decisions or {}
    emails = list(inbox.emails())
    corpus_ids = [e["email_id"] for e in emails]

    degraded_reasons: list[str] = []
    if llm is None:
        degraded_reasons.append("llm_unavailable_semantic_classification")

    from shipdoc.extract.registry import default_registry
    from shipdoc.normalise import Normaliser
    from shipdoc.normalise.labels import LabelIndex
    _CTX["inbox"] = inbox
    _CTX["registry"] = default_registry(ocr_enabled=cfg.flags.ocr_enabled)
    # Built once and shared by both documents: normalising in two places is the
    # classic source of guaranteed false alarms (M09).
    _CTX["normaliser"] = Normaliser(cfg)
    # route/confirm.py needs label matching but may not import `normalise` (v2 §3
    # puts normalise above route). The orchestrator sits above both and owns
    # construction. Built ONCE and reused: LabelIndex assigns state only in
    # __init__ — match() and is_any_label() are pure reads — and Normaliser already
    # builds one per run and reuses it across all 520 records.
    _CTX["label_index"] = LabelIndex(cfg)
    _CTX["roles"] = {}

    from shipdoc.infra.cache import Cache
    from shipdoc.infra.doccache import DocCache
    cache_dir = cache_dir or _CTX.get("cache_dir")
    _CTX["doccache"] = DocCache(Cache(Path(cache_dir) / "extract")) if cache_dir \
        else DocCache(None)

    records = [process(Record(email_id=e["email_id"], raw=e), cfg, llm, decisions)
               for e in emails]

    assert len(records) == len(corpus_ids), "record count diverged from the corpus"

    submission = SubmissionAdapter().emit(records, expected_ids=corpus_ids)
    return {
        "records": records,
        "submission": submission,
        "summary": summarise(records, degraded_reasons, cfg),
    }


def summarise(records: list[Record], degraded_reasons: list[str],
              cfg: Config | None = None) -> dict:
    """The operational metrics artifact. Structured counts, never prose — these are
    what a GROUP BY runs over.

    Counters use the INTERNAL reason vocabulary (`err_no_value`), not the external
    `review_reason` spellings: this is an operations artifact, not a submission, and
    conflating the two would undo the boundary patch §5 established.

    Deterministic by construction: counts only, no wall-clock time, no random ids.
    Both counters are zero-filled from their enums so a metric never silently vanishes
    from the output just because no record hit it this run.
    """
    by_state = {s.value: 0 for s in RecordState}
    by_state.update(Counter(r.state.value for r in records if r.state is not None))

    by_reason = {k.value: 0 for k in ReasonKey}
    by_reason.update(Counter(r.reason.value for r in records if r.reason is not None))

    return {
        "records_total":    len(records),
        "by_state":         by_state,
        "by_reason":        by_reason,
        "degraded":         bool(degraded_reasons),
        "degraded_reasons": sorted(degraded_reasons),
        "baseline":         None,
        # Phase 11. WHICH label vocabulary produced this run. Once rules can change,
        # "two runs produce identical output" is only true for a fixed vocabulary —
        # a run that does not state which one it used has quietly stopped being
        # reproducible. "" means no learned file, i.e. hand-written rules only.
        "learned_labels_sha256": (cfg.learned.sha256 if cfg is not None else ""),
        "learned_labels_count": (len(cfg.learned.approved) if cfg is not None else 0),
        # Phase 12 A4. Counts and milliseconds only — never a prompt, a response or
        # a key, because this file is committed and shipped.
        "llm_provider": (cfg.llm.provider if cfg is not None else ""),
        "llm_model": (cfg.llm.model if cfg is not None else ""),
        "llm_calls": (_CTX["llm_metrics"].as_dict()
                      if _CTX.get("llm_metrics") is not None else None),
    }


def file_sha256(path: Path) -> str:
    """Hash of the bytes ACTUALLY ON DISK.

    Phase 13 gate 3 asks for the submission in the database to match the file. The
    obvious implementation — re-serialise the payload and hash that — is wrong, and
    wrong in a way that took a real measurement to catch:

    `write_atomic` opens the file in TEXT mode, so on Windows Python translates every
    `\\n` into `\\r\\n`. The published hash c02b4f44 / 676d2fc4 is therefore a hash of
    CRLF bytes. A re-serialisation in memory produces LF and can never match, and
    "fixing" that by writing binary changes every artifact this project has ever
    published — which is exactly what happened here before this function existed.

    Hashing the file after writing it is both simpler and immune to the question.
    """
    import hashlib
    h = hashlib.sha256()
    with open(path, "rb") as fh:
        for chunk in iter(lambda: fh.read(65536), b""):
            h.update(chunk)
    return h.hexdigest()


def write_atomic(path: Path, payload: object) -> None:
    """Temp file plus rename: a killed process must not leave a half-written artifact
    that deserialises into garbage on the next run.

    NEWLINE IS PINNED TO "\\n". THIS IS A CROSS-PLATFORM INVARIANT, NOT A STYLE.

    Python's text mode translates "\\n" to the platform separator, so the same run on
    Windows and on Linux produced *different bytes* and therefore different hashes.
    This repository's artifacts were published from Windows (CRLF); Azure Container
    Apps is Linux (LF). Left alone, the deployed service would emit a hash that
    disagrees with the published one, and the discovery would be made by a judge
    rather than by us.

    The JSON content was never platform-dependent — only the line endings were — so
    normalising costs nothing except a one-time change of the published hash, which
    is recorded in HANDOVER.md alongside the old one.

    `test_artifacts_are_byte_identical_across_platforms` guards it: an invariant with
    no test is a convention, and conventions do not survive a refactor.

    sort_keys=False deliberately: entries must keep sample_submission.json's field
    order. Determinism (I6) comes from callers emitting in sorted email_id order, not
    from re-sorting every nested object.
    """
    path = Path(path)
    path.parent.mkdir(parents=True, exist_ok=True)
    fd, tmp = tempfile.mkstemp(dir=str(path.parent), suffix=".tmp")
    try:
        with os.fdopen(fd, "w", encoding="utf-8", newline="\n") as fh:
            json.dump(payload, fh, indent=2, sort_keys=False, ensure_ascii=False)
            fh.write("\n")
        os.replace(tmp, path)
    except BaseException:
        Path(tmp).unlink(missing_ok=True)
        raise


LLM_CACHE_DIR = Path("cache") / "llm"


def build_llm(cfg: Config, out_dir: Path, cache_dir: Path | None = None):
    """Construct the cached client, probing once so degraded mode is entered a single
    time rather than 394 times.

    Returns a CachedLLM whenever the cache exists, even with no model reachable: a
    warm cache answers every prompt this corpus asks, so a clone with no Ollama still
    reproduces the published run. Returns None only when there is neither.
    """
    from shipdoc.infra.cache import Cache
    from shipdoc.llm.client import CachedLLM, OllamaClient, probe

    root = Path(cache_dir) if cache_dir else LLM_CACHE_DIR
    provider = (cfg.llm.provider or "ollama").lower()

    # Phase 12. `none` means: never build a model. Deterministic keyword rules only.
    # A cache is still not consulted, because the point of this setting is to answer
    # "what does this system do with no AI at all?" honestly.
    if provider == "none":
        return None

    def _cached(inner):
        return CachedLLM(inner, Cache(root), prompt_version=cfg.llm.prompt_version,
                         model=cfg.llm.model)

    def _warm_cache_only():
        """A committed cache answers every prompt this corpus asks, so a clone with
        no model and no API key still reproduces the published run."""
        return _cached(_Unreachable(cfg.llm.model)) \
            if root.is_dir() and any(root.rglob("*")) else None

    if provider != "ollama":
        from shipdoc.llm.hosted import HostedClient, MissingAPIKey
        try:
            inner = HostedClient(cfg.llm.model, provider=provider,
                                 base_url=cfg.llm.base_url,
                                 temperature=cfg.llm.temperature, seed=cfg.llm.seed)
        except MissingAPIKey as e:
            # A2: clean degrade, readable message, no stack trace, no config echoed.
            print(f"shipdoc: {e}", file=sys.stderr)
            return _warm_cache_only()
        except LLMUnavailable as e:
            print(f"shipdoc: {e}", file=sys.stderr)
            return _warm_cache_only()
        _CTX["llm_metrics"] = inner.metrics
        return _cached(inner)

    inner = OllamaClient(cfg.llm.model, temperature=cfg.llm.temperature,
                         seed=cfg.llm.seed)
    client = _cached(inner)
    if probe(inner, timeout_s=min(cfg.llm.timeout_s, 15.0)):
        return client
    # No server. If the cache has entries they are this model's own answers at
    # temperature 0, so they are exactly what the server would have returned.
    return client if root.is_dir() and any(root.rglob("*")) else None


class _Unreachable:
    """Stands in for a model we cannot reach, so a warm cache still serves.

    A cache MISS raises LLMUnavailable — the same thing a dead server raises — which
    the pipeline already degrades from. Without this the alternative is handing back
    a live client that will fail on every miss with a provider-shaped error.
    """

    def __init__(self, model: str):
        self.model = model

    def complete(self, prompt: str, *, choices=None, timeout_s: float = 30.0) -> str:
        raise LLMUnavailable("no model configured or reachable; cache miss")



def _apply_temp_labels(cfg: Config, extra_labels: list[dict]) -> Config:
    from dataclasses import replace
    from shipdoc.learned import normalise_label
    
    new_fields = dict(cfg.fields)
    for extra in extra_labels:
        field_name = extra.get("proposed_field")
        label = extra.get("label_raw") or extra.get("label")
        if not field_name or not label or field_name not in new_fields:
            continue
        
        spec = new_fields[field_name]
        norm = normalise_label(label)
        if norm not in [normalise_label(s) for s in spec.synonyms]:
            new_syns = tuple(list(spec.synonyms) + [label])
            new_fields[field_name] = replace(spec, synonyms=new_syns)
            
    from types import MappingProxyType
    return replace(cfg, fields=MappingProxyType(new_fields))


def _propose_for_adhoc(unknowns: list, cfg: Config, llm: Any) -> list[dict]:
    if not unknowns:
        return []
    
    from shipdoc.llm.label_proposer import propose
    import dataclasses
    
    seen = set()
    proposals = []
    for u in unknowns:
        if u.normalised in seen:
            continue
        seen.add(u.normalised)
        if llm is not None:
            p = propose(u, cfg, llm)
        else:
            p = None
            
        if p is not None:
            proposals.append(dataclasses.asdict(p))
        else:
            proposals.append({
                "normalised": u.normalised,
                "label": u.raw,
                "proposed_field": None,
                "value": u.value,
                "context": u.context,
                "doc_ref": u.doc_ref,
                "line_no": u.line_no,
                "role": u.role,
            })
    return proposals


def process_adhoc(*, subject: str, body: str, si_text: str, bl_text: str,
                  cfg: Config, llm: Any = None,
                  extra_labels: list[dict] | None = None) -> dict:
    """Phase 15 — run ONE pasted email through the real engine. Nothing is stored.

    This is the "try it yourself" path: a judge pastes a shipping instruction and a
    draft bill of lading and sees what the system makes of them.

    IT IS THE REAL ENGINE, not a demo mode. The same `Normaliser`, the same
    `compare_all`, the same `evaluate`. The only stages skipped are the ones that
    have nothing to do: ingest and extract, because the text arrived as text rather
    than as a file to be parsed. Everything that decides an outcome is untouched, so
    what a visitor sees here is what the batch run would say about the same pair.

    WHAT IT DELIBERATELY DOES NOT DO
      * No database write. An anonymous visitor cannot add rows to the demo, and a
        run they trigger cannot appear in the run history and confuse a judge
        comparing hashes. R2 is about finished runs; this simply never becomes one.
      * No file upload. Pasted text only: accepting arbitrary uploads on a public URL
        means running the extractor over hostile input from strangers, which is a
        different risk conversation from the one this demo needs.
    """
    if extra_labels:
        cfg = _apply_temp_labels(cfg, extra_labels)

    from shipdoc.types import Block, ExtractedDoc, Method, SourceRef, Record, StageEvent, RecordState, ReasonKey

    def _doc(text: str, name: str) -> ExtractedDoc:
        clean = (text or "").replace("\r\n", "\n").replace("\r", "\n")
        return ExtractedDoc(
            ok=bool(clean.strip()), text=clean,
            blocks=(Block(text=clean, kind="pasted",
                          ref=SourceRef(file=name, locator="line 1")),),
            method=Method.NATIVE_TEXT, detected_mime="text/plain",
            warnings=(), failure=None if clean.strip() else "empty")

    rec = Record(email_id="adhoc", raw={
        "email_id": "adhoc", "subject": subject or "", "body": body or "",
        "attachments": [n for n, t in (("adhoc_SI.txt", si_text),
                                       ("adhoc_BL.txt", bl_text)) if (t or "").strip()],
    })

    # 1. Classification — the real arbiter, recording WHICH path decided.
    how: dict = {}
    from shipdoc.classify.arbiter import classify
    category, confidence = classify(rec, cfg, llm, trace=how)
    rec.category = category
    rec.decided_by = how.get("decided_by")
    rec.trace.append(StageEvent("classify", "ok" if category else "undecided",
                                f"{category} conf={confidence:.2f} "
                                f"via={how.get('source', '?')}", seq=0))

    documents = {}
    if (si_text or "").strip():
        documents["SI"] = _doc(si_text, "pasted SI")
    if (bl_text or "").strip():
        documents["BL"] = _doc(bl_text, "pasted BL")
    rec.documents = documents

    # 2. Normalise + compare, but only when this IS a comparison request and both
    #    documents are present — exactly the engine's own precondition.
    if category == cfg.comparison_category and len(documents) == 2:
        from shipdoc.compare.comparators import compare_all
        from shipdoc.normalise import Normaliser
        from shipdoc.normalise.labels import LabelIndex
        from shipdoc.normalise.unknown import find_unknown

        normaliser = Normaliser(cfg)
        index = LabelIndex(cfg)
        for role, doc in documents.items():
            values, seen = normaliser.normalise_with_labels(doc, f"pasted {role}")
            rec.fields[role] = values
            rec.labels_seen[role] = seen
            rec.unknown_labels.extend(find_unknown(doc.text, index, f"pasted {role}", role))
        rec.trace.append(StageEvent("normalise", "ok",
                                    f"SI {len(rec.fields.get('SI', {}))} fields, "
                                    f"BL {len(rec.fields.get('BL', {}))} fields", seq=1))
        rec.comparisons = compare_all(rec.fields.get("SI", {}), rec.fields.get("BL", {}),
                                      cfg, None)
        rec.trace.append(StageEvent("compare", "ok",
                                    f"{len(rec.comparisons)} fields compared", seq=2))
    elif category == cfg.comparison_category:
        from shipdoc.errors import reason_key
        from shipdoc.state.machine import raise_state
        raise_state(rec, RecordState.ESCALATED, ReasonKey.ERR_NO_ATTACHMENT)
        rec.trace.append(StageEvent("route", "escalated",
                                    "a comparison needs BOTH an SI and a BL", seq=1))

    # 3. THE state authority. Not a copy of it.
    from shipdoc.state.machine import evaluate
    evaluate(rec, cfg)
    
    proposals = _propose_for_adhoc(rec.unknown_labels, cfg, llm)

    from shipdoc.adapters.projection import record_to_detail
    out = record_to_detail(rec, cfg)
    out["unknown_labels"] = sorted({u.normalised for u in rec.unknown_labels})[:12]
    out["label_proposals"] = proposals
    out["decided_by"] = rec.decided_by
    return out

def save_run_to_db(result: dict, cfg: Config, config_dir: str, out_dir: Path) -> None:
    """Write one run to the database, if one is configured. Otherwise do nothing.

    Everything here is INSERT (R2). Re-processing produces a new run_id rather than
    mutating a finished run, so "what did we say on the 20th?" keeps an answer.

    Wrapped by the caller in a broad except on purpose: a database outage must
    degrade to file-only output, never take down a 520-email batch. The files on
    disk remain the source of truth for the submission.
    """
    from shipdoc.adapters.db import build_repository

    repo = build_repository()
    if repo is None:
        return

    from shipdoc.adapters.db.project import (
        code_version, config_sha256, record_rows,
    )
    from shipdoc.adapters.db.repository import RunInput

    summary = result["summary"]
    run_id = repo.save_run(RunInput(
        code_version=code_version(Path.cwd()),
        config_sha256=config_sha256(Path(config_dir)),
        learned_labels_sha256=summary.get("learned_labels_sha256", ""),
        decisions_sha256=repo.decisions_sha256(),
        submission=result["submission"],
        # The hash of the FILE AS WRITTEN, so a run row matches output/submission.json
        # byte for byte and gate 3 is a direct comparison rather than an argument.
        submission_sha256=file_sha256(out_dir / "submission.json"),
        records=record_rows(result["records"], result["submission"]),
        llm_provider=cfg.llm.provider, llm_model=cfg.llm.model,
        prompt_version=cfg.llm.prompt_version,
        degraded=bool(summary.get("degraded")),
        run_summary_sha256=file_sha256(out_dir / "run_summary.json"),
    ))
    print(f"shipdoc: run {run_id} written to the database", file=sys.stderr)


def propose_labels(records: list[Record], cfg: Config, llm: Any,
                   queue_path: Path) -> int:
    """Phase 11, the PROPOSES step. Returns the number of pending proposals.

    Only labels from records that were ACTUALLY COMPARED are considered: a label on a
    document we never compared is not evidence that our vocabulary is short, and
    asking about it spends a reviewer's attention on a document nobody was reading.

    One label, one proposal, once ever — keyed by the normalised label string and
    deduplicated against approvals, rejections AND the existing pending queue.
    """
    from shipdoc.llm.label_proposer import propose
    from shipdoc.review.proposals import already_known, read_proposals, write_proposals

    pending = read_proposals(queue_path)
    known = {p.normalised for p in pending}

    for rec in records:
        if not rec.comparisons:
            continue                       # never compared: not evidence
        for unknown in rec.unknown_labels:
            if unknown.normalised in known:
                continue
            known.add(unknown.normalised)
            if already_known(unknown.normalised, cfg.learned, pending):
                continue
            proposal = propose(unknown, cfg, llm)
            if proposal is not None:
                pending.append(proposal)

    return write_proposals(queue_path, pending)


def main_run(source: str, config_dir: str, out_dir: str,
             submit: bool = False, server_url: str | None = None,
             no_llm: bool = False) -> dict:
    """Entry point for the CLI. Returns the run summary."""
    from shipdoc.config import load_config
    from shipdoc.ingest.loader_port import LoaderInbox

    from shipdoc.review.queue import load_decisions, read_raw, write_queue

    cfg = load_config(Path(config_dir))
    inbox = LoaderInbox(source)
    llm = None if no_llm else build_llm(cfg, Path(out_dir))

    # P-HANDOFF: ONE file. The queue the reviewer was handed is the queue we read.
    queue_path = Path(out_dir) / "review_queue.json"
    prior = read_raw(queue_path)
    decisions = load_decisions(queue_path)

    # Extraction cache lives beside the LLM cache, under the run's out dir.
    _CTX["cache_dir"] = Path(out_dir) / "cache"
    result = run_corpus(inbox, cfg, llm=llm, decisions=decisions)

    from shipdoc.adapters.report import ReportAdapter

    out = Path(out_dir)
    write_atomic(out / "submission.json", result["submission"])
    write_atomic(out / "run_summary.json", result["summary"])

    write_queue(result["records"], queue_path, preserve=prior)

    # Phase 13. Persist the run IF a database is configured. `None` is the supported
    # default, not a degraded mode: with no DATABASE_URL this is a no-op and the run
    # is byte-identical to one on a machine with no database driver installed.
    try:
        save_run_to_db(result, cfg, config_dir, out)
    except Exception as e:  # noqa: BLE001 - the database must never break a run
        print(f"shipdoc: database write skipped ({type(e).__name__}: {e})",
              file=sys.stderr)

    # Phase 11. Propose label->field mappings for labels nobody has ruled on yet.
    # This writes a QUEUE. Nothing here changes how this run behaved, and nothing
    # here can change how the next one behaves either — only an approval can.
    propose_labels(result["records"], cfg, llm, Path(out_dir) / "label_proposals.json")
    result["summary"]["decisions_applied"] = len(decisions)
    write_atomic(out / "run_summary.json", result["summary"])

    try:
        from shipdoc.llm.client import write_manifest
        write_manifest(LLM_CACHE_DIR, cfg.llm.model, cfg.llm.prompt_version)
    except Exception:
        pass                      # a manifest must never fail a run

    report = ReportAdapter().emit(result["records"])
    out.mkdir(parents=True, exist_ok=True)
    (out / "report.txt").write_text(report, encoding="utf-8")

    # report.txt is written above (stage 4).
    # v2 reorder dropped adapters/report from the build sequence when pass 2 was
    # deleted. It lands at stage 4, when real SI/BL values first exist to put
    # side by side. The scoreboard cannot measure this output, which is exactly why
    # it is easy to forget.

    if submit:
        target = LoaderInbox(server_url) if server_url else inbox
        scoreboard = target.submit(result["submission"])
        write_atomic(out / "scoreboard.json", scoreboard)
        result["summary"]["baseline"] = scoreboard.get("final_score")
        write_atomic(out / "run_summary.json", result["summary"])

    return result["summary"]
