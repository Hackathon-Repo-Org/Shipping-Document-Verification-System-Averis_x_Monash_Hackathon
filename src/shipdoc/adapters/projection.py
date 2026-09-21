"""M18b — overlay human decisions onto a stored run, at READ time. Phase 14.

THE PROBLEM THIS SOLVES
-----------------------
Two Phase 13 rules pull in opposite directions:

  R2  a finished run's rows are IMMUTABLE
  R3  a decision is about the EMAIL, not about one run, and must take effect
      immediately — the UI shows the new status with no pipeline re-run

Writing the new status into `records` would satisfy R3 by breaking R2, and the audit
question "what did the system say before a human touched it?" would lose its answer.

So the stored row is never modified. Decisions are applied as an OVERLAY when a
record is read. The run keeps saying what it said; the reviewer sees the current
truth; and the next full run inherits the same decisions through the engine's own
`apply_decisions`, arriving at the same answer by a different route.

THIS FILE MIRRORS `state/machine.evaluate` AND MUST NOT DRIFT FROM IT
--------------------------------------------------------------------
Recomputing a status here is duplicated logic, and duplicated logic is how a
dashboard ends up disagreeing with the submission it was built from. It is done
anyway, for the same reason `learned.normalise_label` duplicates
`normalise.labels.label_key`: the alternative is reconstructing a full `Record` on
every page load.

The duplication is made safe the same way — by a test, not by a comment.
`tests/unit/test_api_projection.py::test_overlay_agrees_with_the_engine` runs both
over the whole corpus and asserts they produce identical statuses. If someone changes
the engine's projection and not this file, that test fails.

The rule being mirrored, from `state/machine.evaluate`:

    any MISMATCH            -> MISMATCH        (a confirmed defect wins)
    else any CANNOT_DETERMINE -> NEEDS_REVIEW   (honest uncertainty)
    else                    -> OK
"""
from __future__ import annotations

MISMATCH = "MISMATCH"
CANNOT_DETERMINE = "CANNOT_DETERMINE"
MATCH = "MATCH"

NEEDS_REVIEW = "NEEDS_REVIEW"
OK = "OK"


def status_from_verdicts(verdicts) -> str:
    """The engine's projection rule, and nothing else."""
    vs = list(verdicts)
    if any(v == MISMATCH for v in vs):
        return MISMATCH
    if any(v == CANNOT_DETERMINE for v in vs):
        return NEEDS_REVIEW
    return OK


def overlay(record: dict, decisions: list[dict]) -> dict:
    """Return `record` with active decisions applied. Does not mutate the input.

    `record` is the stored row as the repository read it; `decisions` are the ACTIVE
    (non-superseded) rulings for that email. The result carries `human_decided` and,
    where relevant, `bypassed_state_rule`, so the UI can show *that* a human
    intervened rather than silently presenting their answer as the system's.
    """
    out = dict(record)
    comparisons = [dict(c) for c in (record.get("comparisons") or [])]
    by_field = {c["field"]: c for c in comparisons}

    applied: list[dict] = []
    bypassed = False
    category = out.get("category")

    for d in decisions:
        dtype = d.get("decision_type")
        field = d.get("field")

        if dtype == "override_category" and d.get("corrected_category"):
            category = d["corrected_category"]
            if out.get("status") == NEEDS_REVIEW:
                bypassed = True
            applied.append(d)
            continue

        if dtype == "clear_escalation" and field is None:
            # A record-level override. It MAY bypass the monotone state rule — that
            # is a deliberate human act, and the one thing that must never happen is
            # for it to be invisible. Flagged here, shown in the UI, logged in the
            # database as its own row.
            if out.get("status") == NEEDS_REVIEW:
                bypassed = True
            applied.append(d)
            continue

        if dtype == "retry":
            applied.append(d)
            continue

        if field and field in by_field:
            c = by_field[field]
            if dtype == "correct_value" and d.get("corrected_value") is not None:
                c["bl_value"] = d["corrected_value"]
                c["verdict"] = d.get("verdict") or MATCH
                c["detail"] = f"corrected by {d.get('reviewer', 'a reviewer')}"
            elif dtype == "confirm":
                c["verdict"] = d.get("verdict") or c.get("verdict")
                c["detail"] = f"confirmed by {d.get('reviewer', 'a reviewer')}"
            c["decided_by_human"] = True
            applied.append(d)

    if comparisons:
        out["comparisons"] = comparisons
        status = status_from_verdicts(c.get("verdict") for c in comparisons)
    else:
        status = out.get("status", NEEDS_REVIEW)

    if bypassed:
        status = OK

    out["category"] = category
    out["status"] = status
    out["has_defect"] = status == MISMATCH
    out["defect_fields"] = sorted(c["field"] for c in comparisons
                                  if c.get("verdict") == MISMATCH)
    # A review_reason only makes sense while the record is still under review.
    if status != NEEDS_REVIEW:
        out["review_reason"] = None
    out["human_decided"] = bool(applied)
    out["bypassed_state_rule"] = bypassed
    out["decisions"] = decisions
    return out


def record_to_detail(rec, cfg) -> dict:
    """A live `Record` in the API's detail shape.

    Used by the ad-hoc "try it yourself" path, where there is no database row to read
    — the record exists only for the duration of one request.

    The external fields (status, review_reason, has_defect) come from
    `SubmissionAdapter`, NOT from a second computation here. That adapter is the one
    place that translates the internal vocabulary to the external one, and a copy of
    that translation in this file is exactly the class of duplication that produced
    the projection bugs of earlier phases.
    """
    from shipdoc.adapters.submission import SubmissionAdapter

    entry = SubmissionAdapter().emit([rec], expected_ids=[rec.email_id])[rec.email_id]

    comparisons = []
    for name, c in sorted((rec.comparisons or {}).items()):
        comparisons.append({
            "field": name,
            "verdict": c.verdict.value.upper(),
            "leaning": c.leaning.value.upper() if c.leaning is not None else None,
            "si_value": getattr(c.si, "raw_text", None),
            "bl_value": getattr(c.bl, "raw_text", None),
            "si_evidence": _live_evidence(c.si),
            "bl_evidence": _live_evidence(c.bl),
            "strategy": c.strategy,
            "detail": c.detail,
        })

    return {
        "email_id": rec.email_id,
        "category": entry.get("category"),
        "status": entry.get("status"),
        "review_reason": entry.get("review_reason"),
        "has_defect": bool(entry.get("has_defect")),
        "defect_fields": entry.get("defect_fields") or [],
        "awaiting_documents": bool(getattr(rec, "awaiting_docs", False)),
        "state": rec.state.value if rec.state is not None else None,
        "reason_key": rec.reason.value if rec.reason is not None else None,
        "comparisons": comparisons,
        "events": [{"seq": e.seq, "stage": e.stage, "outcome": e.outcome,
                    "detail": (e.detail or "")[:400]} for e in (rec.trace or [])],
        "decisions": [],
        "attachments": [],
        "human_decided": False,
        "bypassed_state_rule": False,
    }


def _live_evidence(fv) -> dict | None:
    if fv is None:
        return None
    src = getattr(fv, "source", None)
    ev = {"file": getattr(src, "file", None),
          "locator": getattr(src, "locator", None),
          "method": getattr(getattr(fv, "method", None), "value", None),
          "label_seen": getattr(fv, "label_seen", None)}
    if getattr(fv, "resolved_from", None):
        ev["resolved_from"] = fv.resolved_from
        ev["reference_text"] = fv.reference_text
    return ev
