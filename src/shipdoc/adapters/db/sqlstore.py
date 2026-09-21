"""M17c — one SQLAlchemy implementation of `Repository`, two dialects.

PostgreSQL in production (Azure Database for PostgreSQL, Flexible Server) and SQLite
for tests and the clean-clone gate. One implementation rather than two, because two
implementations of the same interface drift, and the one that drifts is always the
one the tests do not run against.

The dialect differences are confined to `models.py` (`JSONBType`, `GUID`) so nothing
in this file branches on which database it is talking to.

R2 IS VISIBLE HERE: `save_run` only ever INSERTs. There is no update path for a
finished run's rows, and re-processing an email produces a new `run_id` rather than
mutating the old one. The audit question "what did we say on the 20th?" has an answer
only if nobody overwrote it.
"""
from __future__ import annotations

import hashlib
import json
import uuid
from typing import Any

from sqlalchemy import create_engine, delete, or_, select, update
from sqlalchemy import func as _sqlfunc

def sqlfunc_lower(col):
    return _sqlfunc.lower(col)

def sqlfunc_count():
    return _sqlfunc.count()

from sqlalchemy.orm import Session, sessionmaker

from shipdoc.adapters.db.models import (
    Attachment,
    Base,
    DBComparison,
    DBRecord,
    Email,
    Evaluation,
    LabelProposal,
    LearnedLabel,
    LLMCall,
    ReviewDecision,
    Run,
    StageEventRow,
    Submission,
)
from shipdoc.adapters.db.repository import RecordFilter, RunInput, utcnow


def _normalise_url(url: str) -> str:
    """Accept the plain `postgresql://` form Azure hands out and route it to psycopg3.

    Azure's connection-string blade gives `postgresql://…`; SQLAlchemy 2 defaults
    that to psycopg2, which is not what this project installs. Rewriting it here
    means nobody has to remember to type `+psycopg`.
    """
    if url.startswith("postgres://"):
        url = "postgresql://" + url[len("postgres://"):]
    if url.startswith("postgresql://"):
        url = "postgresql+psycopg://" + url[len("postgresql://"):]
    return url


class SQLRepository:
    """Implements `Repository`. See that module for why the surface is narrow."""

    def __init__(self, url: str, *, create: bool | None = None, echo: bool = False):
        self.url = _normalise_url(url)
        self.engine = create_engine(self.url, echo=echo, future=True,
                                    pool_pre_ping=not self.url.startswith("sqlite"))
        self._session = sessionmaker(self.engine, expire_on_commit=False,
                                     future=True)
        # SQLite is created in place; PostgreSQL is owned by Alembic, because a
        # schema that two different mechanisms can create is a schema that differs
        # between environments.
        if create is None:
            create = self.url.startswith("sqlite")
        if create:
            Base.metadata.create_all(self.engine)

    def session(self) -> Session:
        return self._session()

    # ------------------------------------------------------------ INPUT (seed)

    def upsert_email(self, s: Session, email_id: str, *, subject: str = "",
                     body: str = "", source: str = "corpus",
                     received_at=None) -> None:
        """Idempotent by primary key — B3 requires seeding twice to change nothing."""
        row = s.get(Email, email_id)
        if row is None:
            s.add(Email(email_id=email_id, subject=subject, body=body,
                        source=source, received_at=received_at))
        else:
            row.subject, row.body, row.source = subject, body, source

    def upsert_attachment(self, s: Session, email_id: str, filename: str, *,
                          sha256: str, size_bytes: int | None = None,
                          content_type: str | None = None,
                          blob_url: str | None = None,
                          detected_type: str | None = None) -> None:
        """Idempotent on (email_id, filename), which is the natural key."""
        row = s.scalar(select(Attachment).where(
            Attachment.email_id == email_id, Attachment.filename == filename))
        if row is None:
            s.add(Attachment(email_id=email_id, filename=filename, sha256=sha256,
                             size_bytes=size_bytes, content_type=content_type,
                             blob_url=blob_url, detected_type=detected_type))
            return
        row.sha256, row.size_bytes = sha256, size_bytes
        row.content_type, row.detected_type = content_type, detected_type
        if blob_url:
            row.blob_url = blob_url

    # ------------------------------------------------------------ WRITING A RUN

    def save_run(self, run: RunInput) -> uuid.UUID:
        """INSERT ONLY (R2). Returns the new run_id."""
        run_id = run.run_id or uuid.uuid4()
        with self.session() as s, s.begin():
            s.add(Run(
                run_id=run_id, status="complete", started_at=utcnow(),
                finished_at=utcnow(), code_version=run.code_version,
                config_sha256=run.config_sha256,
                learned_labels_sha256=run.learned_labels_sha256 or "",
                decisions_sha256=run.decisions_sha256 or "",
                llm_provider=run.llm_provider, llm_model=run.llm_model,
                prompt_version=run.prompt_version, degraded=bool(run.degraded),
                record_count=len(run.records),
                submission_sha256=run.submission_sha256,
                run_summary_sha256=run.run_summary_sha256,
            ))
            # Emails must exist: records.email_id is a foreign key, and a run that
            # silently invented emails would make the corpus unauditable.
            known = set(s.scalars(select(Email.email_id)))
            for row in run.records:
                if row["email_id"] not in known:
                    s.add(Email(email_id=row["email_id"]))
                    known.add(row["email_id"])
            s.flush()

            for row in run.records:
                rec = DBRecord(
                    run_id=run_id, email_id=row["email_id"],
                    category=row.get("category"), decided_by=row.get("decided_by"),
                    state=row["state"], reason_key=row.get("reason_key"),
                    status=row["status"], review_reason=row.get("review_reason"),
                    has_defect=bool(row.get("has_defect")),
                    awaiting_documents=bool(row.get("awaiting_documents")),
                )
                s.add(rec)
                s.flush()
                for c in row.get("comparisons", ()):
                    s.add(DBComparison(record_id=rec.record_id, **c))
                for e in row.get("events", ()):
                    s.add(StageEventRow(record_id=rec.record_id, **e))

            for call in run.llm_calls:
                s.add(LLMCall(run_id=run_id, **call))

            s.add(Submission(run_id=run_id, payload=run.submission,
                             sha256=run.submission_sha256))
        return run_id

    # ------------------------------------------------------------------ READING

    def list_records(self, filters: RecordFilter) -> list[dict]:
        q = select(DBRecord)
        if filters.run_id is not None:
            q = q.where(DBRecord.run_id == filters.run_id)
        else:
            latest = self.latest_run_id()
            if latest is not None:
                q = q.where(DBRecord.run_id == latest)
        if filters.status:
            q = q.where(DBRecord.status == filters.status)
        if filters.category:
            q = q.where(DBRecord.category == filters.category)
        if filters.email_id:
            q = q.where(DBRecord.email_id == filters.email_id)
        if filters.has_defect is not None:
            q = q.where(DBRecord.has_defect == filters.has_defect)
        q = q.order_by(DBRecord.email_id).limit(filters.limit).offset(filters.offset)
        with self.session() as s:
            return [self._record_dict(r) for r in s.scalars(q)]

    def get_record_detail(self, email_id: str,
                          run_id: uuid.UUID | None = None) -> dict | None:
        run_id = run_id or self.latest_run_id()
        if run_id is None:
            return None
        with self.session() as s:
            rec = s.scalar(select(DBRecord).where(
                DBRecord.run_id == run_id, DBRecord.email_id == email_id))
            if rec is None:
                return None
            out = self._record_dict(rec)
            out["comparisons"] = [{
                "field": c.field, "verdict": c.verdict, "leaning": c.leaning,
                "si_value": c.si_value, "bl_value": c.bl_value,
                "si_numeric": None if c.si_numeric is None else str(c.si_numeric),
                "bl_numeric": None if c.bl_numeric is None else str(c.bl_numeric),
                "si_evidence": c.si_evidence, "bl_evidence": c.bl_evidence,
                "strategy": c.strategy, "detail": c.detail,
            } for c in sorted(rec.comparisons, key=lambda x: x.field)]
            out["events"] = [{"seq": e.seq, "stage": e.stage, "outcome": e.outcome,
                              "detail": e.detail}
                             for e in sorted(rec.events, key=lambda x: x.seq)]
            out["decisions"] = self.list_decisions(email_id)
            out["attachments"] = [
                {"attachment_id": a.attachment_id, "filename": a.filename,
                 "content_type": a.content_type, "detected_type": a.detected_type,
                 "blob_url": a.blob_url}
                for a in s.scalars(select(Attachment).where(
                    Attachment.email_id == email_id).order_by(Attachment.filename))]

            email_row = s.get(Email, email_id)
            if email_row is not None:
                out["subject"] = email_row.subject
                out["body"] = email_row.body
                out["received_at"] = email_row.received_at.isoformat() if email_row.received_at else None

            sender = None
            try:
                from pathlib import Path
                for candidate in [Path("dataset/inbox"), Path(__file__).resolve().parents[4] / "dataset" / "inbox"]:
                    p = candidate / f"{email_id}.json"
                    if p.is_file():
                        with open(p, "r", encoding="utf-8") as f:
                            sender = json.load(f).get("from")
                        break
            except Exception:
                pass
            out["sender"] = sender
        # Phase 14: overlay active decisions so the caller sees CURRENT truth. The
        # stored row is never rewritten (R2); the projection happens on read.
        from shipdoc.adapters.projection import overlay
        return overlay(out, out["decisions"])

    @staticmethod
    def _record_dict(r: DBRecord) -> dict:
        return {"email_id": r.email_id, "run_id": str(r.run_id),
                "category": r.category, "decided_by": r.decided_by,
                "state": r.state, "reason_key": r.reason_key, "status": r.status,
                "review_reason": r.review_reason, "has_defect": r.has_defect,
                "awaiting_documents": r.awaiting_documents}

    def latest_run_id(self) -> uuid.UUID | None:
        with self.session() as s:
            return s.scalar(select(Run.run_id)
                            .where(Run.status == "complete")
                            .order_by(Run.started_at.desc()).limit(1))

    def get_submission(self, run_id: uuid.UUID | None = None) -> dict | None:
        run_id = run_id or self.latest_run_id()
        if run_id is None:
            return None
        with self.session() as s:
            row = s.get(Submission, run_id)
            return None if row is None else {"payload": row.payload,
                                             "sha256": row.sha256}

    # -------------------------------------------------------------- THE HUMAN

    def record_decision(self, email_id: str, field: str | None, decision_type: str,
                        reviewer: str, *, verdict: str | None = None,
                        corrected_value: str | None = None,
                        corrected_category: str | None = None,
                        note: str = "") -> int:
        """R3: no run_id. Nothing is deleted — a new ruling SUPERSEDES the old one."""
        with self.session() as s, s.begin():
            if s.get(Email, email_id) is None:
                s.add(Email(email_id=email_id))
                s.flush()
            prior = s.scalars(select(ReviewDecision).where(
                ReviewDecision.email_id == email_id,
                ReviewDecision.field.is_(field) if field is None
                else ReviewDecision.field == field,
                ReviewDecision.superseded_by.is_(None))).all()
            row = ReviewDecision(
                email_id=email_id, field=field, decision_type=decision_type,
                verdict=verdict, corrected_value=corrected_value,
                corrected_category=corrected_category, note=note,
                reviewer=reviewer, decided_at=utcnow())
            s.add(row)
            s.flush()
            for p in prior:
                p.superseded_by = row.decision_id
            return row.decision_id

    def list_decisions(self, email_id: str | None = None,
                       include_superseded: bool = False) -> list[dict]:
        q = select(ReviewDecision)
        if email_id:
            q = q.where(ReviewDecision.email_id == email_id)
        if not include_superseded:
            q = q.where(ReviewDecision.superseded_by.is_(None))
        with self.session() as s:
            return [{"decision_id": d.decision_id, "email_id": d.email_id,
                     "field": d.field, "decision_type": d.decision_type,
                     "verdict": d.verdict, "corrected_value": d.corrected_value,
                     "corrected_category": d.corrected_category,
                     "note": d.note, "reviewer": d.reviewer,
                     "decided_at": d.decided_at.isoformat() if d.decided_at else None}
                    for d in s.scalars(q.order_by(ReviewDecision.decision_id))]

    def decisions_sha256(self) -> str:
        """R4. The hash of the ACTIVE decisions, so a run can say which set it
        inherited. Two runs with the same code and different human answers are two
        different runs, and the summary has to be able to tell them apart."""
        rows = self.list_decisions()
        blob = json.dumps([{k: v for k, v in r.items() if k != "decided_at"}
                           for r in rows], sort_keys=True).encode()
        return hashlib.sha256(blob).hexdigest()

    # ------------------------------------------------------------- PROPOSALS

    def add_proposal(self, **kw) -> int | None:
        """Refuses a proposal with no evidence, the same rule the file queue applies.

        Returns None when the label already has a pending or decided proposal: one
        label, one proposal, once ever.
        """
        if not (kw.get("evidence_doc") and str(kw.get("evidence_context") or "").strip()):
            return None
        with self.session() as s, s.begin():
            existing = s.scalar(select(LabelProposal).where(
                LabelProposal.label_normalised == kw["label_normalised"]))
            if existing is not None:
                return None
            row = LabelProposal(**kw)
            s.add(row)
            s.flush()
            return row.proposal_id

    def list_proposals(self, status: str = "pending") -> list[dict]:
        q = select(LabelProposal)
        if status:
            q = q.where(LabelProposal.status == status)
        with self.session() as s:
            return [{"proposal_id": p.proposal_id, "label_raw": p.label_raw,
                     "label_normalised": p.label_normalised,
                     "proposed_field": p.proposed_field,
                     "proposed_by": p.proposed_by,
                     "prompt_version": p.prompt_version,
                     "evidence_doc": p.evidence_doc,
                     "evidence_line": p.evidence_line,
                     "evidence_context": p.evidence_context,
                     "status": p.status, "decided_by": p.decided_by,
                     "note": p.note}
                    for p in s.scalars(q.order_by(LabelProposal.proposal_id))]

    def decide_proposal(self, proposal_id: int, status: str, decided_by: str,
                        note: str = "") -> None:
        """AI proposes, a HUMAN approves. `decided_by` is a person's name and is
        required — an approval with no name is not auditable."""
        if status not in ("approved", "rejected"):
            raise ValueError("status must be 'approved' or 'rejected'")
        if not decided_by.strip():
            raise ValueError("decide_proposal requires the name of the PERSON "
                             "deciding; an approval with no name is not auditable")
        with self.session() as s, s.begin():
            p = s.get(LabelProposal, proposal_id)
            if p is None:
                raise KeyError(f"no proposal {proposal_id}")
            p.status, p.decided_by, p.note = status, decided_by, note or p.note
            p.decided_at = utcnow()
            if status == "approved":
                # Retire whatever was active for this label; never delete it.
                s.execute(update(LearnedLabel)
                          .where(LearnedLabel.label_normalised == p.label_normalised,
                                 LearnedLabel.active.is_(True))
                          .values(active=False, superseded_at=utcnow()))
                s.add(LearnedLabel(
                    label_normalised=p.label_normalised, field=p.proposed_field,
                    proposal_id=p.proposal_id, approved_by=decided_by,
                    approved_at=utcnow(), active=True))

    def active_learned_labels(self) -> list[dict]:
        with self.session() as s:
            rows = s.scalars(select(LearnedLabel)
                             .where(LearnedLabel.active.is_(True))
                             .order_by(LearnedLabel.label_normalised))
            return [{"label_normalised": r.label_normalised, "field": r.field,
                     "approved_by": r.approved_by,
                     "approved_at": r.approved_at.isoformat()} for r in rows]

    # ------------------------------------------------------- SCORES ONLY (R5)

    def save_evaluation(self, run_id: uuid.UUID, scores: dict) -> None:
        """SCORES ONLY. There is deliberately no method that stores gold labels."""
        from decimal import Decimal

        def num(key):
            v = scores.get(key)
            return None if v is None else Decimal(str(round(float(v), 4)))

        with self.session() as s, s.begin():
            s.merge(Evaluation(
                run_id=run_id, macro_f1=num("macro_f1"), defect_f1=num("defect_f1"),
                end_to_end=num("end_to_end"), reliability_p=num("reliability_p"),
                reliability_r=num("reliability_r"), final_score=num("final_score"),
                evaluated_at=utcnow()))

    def latest_evaluation(self) -> dict | None:
        with self.session() as s:
            row = s.scalar(select(Evaluation)
                           .order_by(Evaluation.evaluated_at.desc()).limit(1))
            if row is None:
                return None
            return {"run_id": str(row.run_id),
                    "macro_f1": _f(row.macro_f1), "defect_f1": _f(row.defect_f1),
                    "end_to_end": _f(row.end_to_end),
                    "reliability_p": _f(row.reliability_p),
                    "reliability_r": _f(row.reliability_r),
                    "final_score": _f(row.final_score),
                    "evaluated_at": row.evaluated_at.isoformat()}

    def counts(self) -> dict[str, int]:
        """Row counts per table — what B3's idempotency gate asserts."""
        from sqlalchemy import func as sqlfunc
        out = {}
        with self.session() as s:
            for model in (Email, Attachment, Run, DBRecord, DBComparison,
                          StageEventRow, LLMCall, ReviewDecision, LabelProposal,
                          LearnedLabel, Submission, Evaluation):
                out[model.__tablename__] = s.scalar(
                    select(sqlfunc.count()).select_from(model)) or 0
        return out


    # ================================================================ Phase 14
    # Everything the reviewer UI reads. Added here rather than in the API so the
    # API stays a translation layer with no SQL and no rules in it.

    def list_runs(self) -> list[dict]:
        with self.session() as s:
            rows = s.scalars(select(Run).order_by(Run.started_at.desc()).limit(50))
            return [{
                "run_id": str(r.run_id), "status": r.status,
                "started_at": r.started_at.isoformat() if r.started_at else None,
                "finished_at": r.finished_at.isoformat() if r.finished_at else None,
                "record_count": r.record_count, "degraded": r.degraded,
                "llm_provider": r.llm_provider, "llm_model": r.llm_model,
                "code_version": r.code_version,
                "config_sha256": r.config_sha256,
                "learned_labels_sha256": r.learned_labels_sha256,
                "decisions_sha256": r.decisions_sha256,
                "submission_sha256": r.submission_sha256,
            } for r in rows]

    def run_summary(self, run_id) -> dict | None:
        from sqlalchemy import func as sqlfunc
        with self.session() as s:
            r = s.get(Run, run_id)
            if r is None:
                return None
            by_status = dict(s.execute(
                select(DBRecord.status, sqlfunc.count())
                .where(DBRecord.run_id == run_id)
                .group_by(DBRecord.status)).all())
            by_category = dict(s.execute(
                select(DBRecord.category, sqlfunc.count())
                .where(DBRecord.run_id == run_id)
                .group_by(DBRecord.category)).all())
        return {"run_id": str(run_id), "by_status": by_status,
                "by_category": by_category, "record_count": r.record_count,
                "llm_provider": r.llm_provider, "llm_model": r.llm_model,
                "degraded": r.degraded,
                "submission_sha256": r.submission_sha256,
                "learned_labels_sha256": r.learned_labels_sha256,
                "started_at": r.started_at.isoformat() if r.started_at else None}

    def _active_decisions_by_email(self, s, email_ids=None) -> dict:
        q = select(ReviewDecision).where(ReviewDecision.superseded_by.is_(None))
        if email_ids is not None:
            q = q.where(ReviewDecision.email_id.in_(list(email_ids)))
        out: dict[str, list[dict]] = {}
        for d in s.scalars(q.order_by(ReviewDecision.decision_id)):
            out.setdefault(d.email_id, []).append({
                "decision_id": d.decision_id, "email_id": d.email_id,
                "field": d.field, "decision_type": d.decision_type,
                "verdict": d.verdict, "corrected_value": d.corrected_value,
                "corrected_category": d.corrected_category, "note": d.note,
                "reviewer": d.reviewer,
                "decided_at": d.decided_at.isoformat() if d.decided_at else None})
        return out

    def list_records_page(self, f) -> dict:
        """Filtered, searched, paginated — and with decisions overlaid.

        The overlay is why a decision shows up instantly: the stored row is never
        rewritten (R2), so the current truth is computed when the row is read.
        """
        from shipdoc.adapters.projection import overlay

        run_id = f.run_id or self.latest_run_id()
        if run_id is None:
            return {"total": 0, "items": [], "limit": f.limit, "offset": f.offset}

        q = select(DBRecord).where(DBRecord.run_id == run_id)
        if f.status:
            q = q.where(DBRecord.status == f.status)
        if f.category:
            q = q.where(DBRecord.category == f.category)
        if f.review_reason:
            q = q.where(DBRecord.review_reason == f.review_reason)
        if f.email_id:
            q = q.where(DBRecord.email_id == f.email_id)
        if f.has_defect is not None:
            q = q.where(DBRecord.has_defect == f.has_defect)
        # Default EXCLUDES awaiting-documents: they are OK, not work.
        if f.awaiting_documents is None:
            q = q.where(DBRecord.awaiting_documents.is_(False))
        else:
            q = q.where(DBRecord.awaiting_documents == f.awaiting_documents)

        with self.session() as s:
            if f.q:
                needle = f"%{f.q.strip().lower()}%"
                # email id, or any compared VALUE on either side — which is how a
                # reviewer actually searches: by a party name or a port, not by id.
                hits = select(DBComparison.record_id).where(
                    or_(sqlfunc_lower(DBComparison.si_value).like(needle),
                        sqlfunc_lower(DBComparison.bl_value).like(needle)))
                q = q.where(or_(sqlfunc_lower(DBRecord.email_id).like(needle),
                                DBRecord.record_id.in_(hits)))

            rows = list(s.scalars(q.order_by(DBRecord.email_id)))
            decisions = self._active_decisions_by_email(
                s, [r.email_id for r in rows])
            items = []
            for r in rows:
                base = self._record_dict(r)
                base["comparisons"] = [
                    {"field": c.field, "verdict": c.verdict}
                    for c in sorted(r.comparisons, key=lambda x: x.field)]
                merged = overlay(base, decisions.get(r.email_id, []))
                merged["defect_count"] = len(merged.get("defect_fields") or [])
                merged.pop("comparisons", None)
                items.append(merged)

        if f.decided is not None:
            items = [i for i in items if bool(i.get("human_decided")) == f.decided]
        total = len(items)
        return {"total": total, "limit": f.limit, "offset": f.offset,
                "items": items[f.offset: f.offset + f.limit]}

    def get_attachment(self, email_id: str, attachment_id: str) -> dict | None:
        """By numeric id OR by filename, because the UI has the filename in the
        evidence and making it look up an id first is a pointless round trip."""
        with self.session() as s:
            row = None
            if str(attachment_id).isdigit():
                row = s.get(Attachment, int(attachment_id))
            if row is None:
                row = s.scalar(select(Attachment).where(
                    Attachment.email_id == email_id,
                    Attachment.filename == attachment_id))
            if row is None or row.email_id != email_id:
                return None
            return {"attachment_id": row.attachment_id, "filename": row.filename,
                    "content_type": row.content_type, "blob_url": row.blob_url,
                    "detected_type": row.detected_type, "sha256": row.sha256}

    def apply_decision(self, *, email_id, field, decision_type, reviewer,
                       verdict=None, corrected_value=None,
                       corrected_category=None, note="") -> dict:
        """Record a decision and return the record's NEW state, immediately.

        No pipeline re-run: the projection is computed on read, so the caller can
        render the new status straight from this response. The next full run reaches
        the same answer through the engine's own `apply_decisions`.
        """
        detail = self.get_record_detail(email_id)
        if detail is None:
            raise KeyError(email_id)
        self.record_decision(email_id, field, decision_type, reviewer,
                             verdict=verdict, corrected_value=corrected_value,
                             corrected_category=corrected_category, note=note)
        updated = self.get_record_detail(email_id)
        run_id = self.latest_run_id()
        if run_id is not None and updated is not None:
            with self.session() as s, s.begin():
                db_rec = s.scalar(select(DBRecord).where(
                    DBRecord.run_id == run_id, DBRecord.email_id == email_id))
                if db_rec is not None:
                    db_rec.category = updated.get("category", db_rec.category)
                    db_rec.status = updated.get("status", db_rec.status)
                    db_rec.review_reason = updated.get("review_reason")
                    db_rec.has_defect = bool(updated.get("has_defect", False))
        return updated

    def dashboard_stats(self) -> dict:
        """Counts, the AI's share of decisions, and the cache-hit rate."""
        from sqlalchemy import func as sqlfunc
        run_id = self.latest_run_id()
        if run_id is None:
            return {"run_id": None, "by_status": {}, "by_category": {},
                    "decided_by": {}, "totals": {}}
        with self.session() as s:
            by_status = dict(s.execute(
                select(DBRecord.status, sqlfunc.count())
                .where(DBRecord.run_id == run_id)
                .group_by(DBRecord.status)).all())
            by_category = dict(s.execute(
                select(DBRecord.category, sqlfunc.count())
                .where(DBRecord.run_id == run_id)
                .group_by(DBRecord.category)).all())
            decided_by = dict(s.execute(
                select(DBRecord.decided_by, sqlfunc.count())
                .where(DBRecord.run_id == run_id)
                .group_by(DBRecord.decided_by)).all())
            awaiting = s.scalar(select(sqlfunc.count()).select_from(DBRecord).where(
                DBRecord.run_id == run_id,
                DBRecord.awaiting_documents.is_(True))) or 0
            calls = s.scalar(select(sqlfunc.count()).select_from(LLMCall)
                             .where(LLMCall.run_id == run_id)) or 0
            hits = s.scalar(select(sqlfunc.count()).select_from(LLMCall).where(
                LLMCall.run_id == run_id, LLMCall.cache_hit.is_(True))) or 0
            decisions = s.scalar(select(sqlfunc.count()).select_from(ReviewDecision)
                                 .where(ReviewDecision.superseded_by.is_(None))) or 0
            pending = s.scalar(select(sqlfunc.count()).select_from(LabelProposal)
                               .where(LabelProposal.status == "pending")) or 0
            r = s.get(Run, run_id)
        total = sum(by_status.values()) or 1
        ai = decided_by.get("llm", 0)
        return {
            "run_id": str(run_id),
            "by_status": {k: v for k, v in by_status.items() if k},
            "by_category": {k: v for k, v in by_category.items() if k},
            "decided_by": {k or "unknown": v for k, v in decided_by.items()},
            "ai_share": round(ai / total, 4),
            "awaiting_documents": awaiting,
            "llm_calls": calls,
            "cache_hit_rate": round(hits / calls, 4) if calls else None,
            "active_decisions": decisions,
            "pending_proposals": pending,
            "totals": {"records": sum(by_status.values())},
            "provider": r.llm_provider if r else None,
            "model": r.llm_model if r else None,
            "started_at": r.started_at.isoformat() if r and r.started_at else None,
            "submission_sha256": r.submission_sha256 if r else None,
            "learned_labels_sha256": r.learned_labels_sha256 if r else None,
            "code_version": r.code_version if r else None,
        }



def _f(v):
    return None if v is None else float(v)
