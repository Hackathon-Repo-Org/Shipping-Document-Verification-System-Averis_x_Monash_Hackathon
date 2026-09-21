/**
 * REVIEW QUEUE — built for someone doing fifty of these, not for a screenshot.
 *
 * KEYBOARD FIRST. A reviewer working a queue keeps their hands on the keyboard;
 * every action that requires reaching for a mouse costs a second and, fifty times
 * over, costs the reviewer's willingness to use the tool at all.
 *
 *   J / K or arrows  move          Enter  open the record
 *   A  confirm       C  correct to the SI value      R  retry
 *   E  clear escalation             Esc  close
 *   N  next unreviewed
 *
 * "NEXT UNREVIEWED" skips anything already decided, so an interrupted session
 * resumes where it left off rather than at the top of the list.
 *
 * Only ACTIONABLE items appear. Awaiting-documents records are excluded by the API
 * default, which is the point of that default existing.
 */
import { useCallback, useEffect, useMemo, useRef, useState } from "react";
import { useNavigate } from "react-router-dom";
import { ApiError, api, getReviewer, type RecordDetail, type RecordRow } from "../lib/api";
import { Badge, termOf, useVocab } from "../lib/vocab";
import { EvidenceDrawer, type EvidenceTarget } from "../components/Evidence";

export function Queue() {
  const vocab = useVocab();
  const nav = useNavigate();
  const [rows, setRows] = useState<RecordRow[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [cursor, setCursor] = useState(0);
  const [detail, setDetail] = useState<RecordDetail | null>(null);
  const [target, setTarget] = useState<EvidenceTarget | null>(null);
  const [flash, setFlash] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [showEmail, setShowEmail] = useState(true);
  const [selectedCategory, setSelectedCategory] = useState<string>("BL_COMPARISON");
  const listRef = useRef<HTMLTableSectionElement | null>(null);

  const load = useCallback(() => {
    api.records({ status: "NEEDS_REVIEW", limit: 200 })
      .then((p) => setRows(p.items))
      .catch((e: ApiError) => setErr(e.message));
  }, []);
  useEffect(load, [load]);

  const current = rows?.[cursor];

  // Load the focused record's detail so an action can be taken without opening it.
  useEffect(() => {
    if (!current) { setDetail(null); return; }
    let alive = true;
    api.record(current.email_id)
      .then((d) => { if (alive) setDetail(d); })
      .catch(() => { if (alive) setDetail(null); });
    return () => { alive = false; };
  }, [current?.email_id]);

  useEffect(() => {
    if (detail?.category) {
      setSelectedCategory(detail.category);
    } else if (current?.category) {
      setSelectedCategory(current.category);
    }
  }, [detail?.category, current?.category]);

  const byReason = useMemo(() => {
    const m = new Map<string, number>();
    for (const r of rows ?? []) {
      const k = r.review_reason ?? "other";
      m.set(k, (m.get(k) ?? 0) + 1);
    }
    return [...m.entries()].sort((a, b) => b[1] - a[1]);
  }, [rows]);

  const act = useCallback(async (decision_type: string, extra: Record<string, unknown> = {}) => {
    const reviewer = getReviewer();
    if (!current) return;
    if (!reviewer) { setErr("Set your name first (top right)."); return; }
    setBusy(true);
    try {
      const updated = await api.decide(current.email_id,
        { decision_type, reviewer, ...extra } as never);
      setFlash(`${current.email_id} → ${termOf(vocab?.categories, updated.category).label} (${updated.status})`);
      setDetail(updated);
      setRows((rs) => (rs ?? []).map((r) =>
        r.email_id === current.email_id
          ? { ...r, status: updated.status, category: updated.category, human_decided: true,
              defect_count: updated.defect_fields?.length ?? 0 }
          : r));
      setTimeout(() => setFlash(null), 2500);
    } catch (e) {
      setErr((e as ApiError).message);
    } finally {
      setBusy(false);
    }
  }, [current]);

  const nextUnreviewed = useCallback(() => {
    if (!rows) return;
    for (let i = cursor + 1; i < rows.length; i++) {
      if (!rows[i].human_decided) { setCursor(i); return; }
    }
    for (let i = 0; i <= cursor; i++) {
      if (!rows[i].human_decided) { setCursor(i); return; }
    }
    setFlash("Nothing left unreviewed.");
    setTimeout(() => setFlash(null), 2200);
  }, [rows, cursor]);

  useEffect(() => {
    listRef.current?.querySelectorAll("tr")[cursor]
      ?.scrollIntoView({ block: "nearest" });
  }, [cursor]);

  if (err && !rows) {
    return <div className="banner err"><i className="bi bi-exclamation-triangle" /><span>{err}</span></div>;
  }

  return (
    <>
      <h1 style={{ fontSize: "1.1rem", marginTop: 0 }}>Review queue</h1>

      {flash && <div className="banner info"><i className="bi bi-check2-circle" /><span>{flash}</span></div>}
      {err && <div className="banner err"><i className="bi bi-exclamation-triangle" /><span>{err}</span></div>}

      <div className="filters">
        {byReason.map(([reason, n]) => (
          <span key={reason} className="tag tone-muted">
            {termOf(vocab?.review_reasons, reason).label}: <strong>{n}</strong>
          </span>
        ))}
        <button className="btn" onClick={nextUnreviewed}>
          <i className="bi bi-arrow-right-circle" /> Next unreviewed
        </button>
      </div>

      <div style={{ display: "grid", gridTemplateColumns: "minmax(0,1fr) minmax(0,1fr)", gap: "1rem" }}
           className="queue-split">
        <div className="card-panel" style={{ maxHeight: "68vh", overflow: "auto" }}>
          <table className="data">
            <thead><tr><th>Email</th><th>Reason</th><th>Category</th><th>Status</th><th /></tr></thead>
            <tbody ref={listRef}>
              {(rows ?? []).map((r, i) => (
                <tr key={r.email_id} className={`row${i === cursor ? " sel" : ""}`}
                    onClick={() => setCursor(i)}>
                  <td className="mono">{r.email_id}</td>
                  <td className="muted">{termOf(vocab?.review_reasons, r.review_reason).label}</td>
                  <td><span className="tag tone-primary" style={{ fontSize: ".7rem" }}>{termOf(vocab?.categories, r.category).label}</span></td>
                  <td><Badge term={termOf(vocab?.statuses, r.status)} /></td>
                  <td>{r.human_decided && <i className="bi bi-person-check" title="decided" />}</td>
                </tr>
              ))}
              {rows?.length === 0 && (
                <tr><td colSpan={5} className="muted" style={{ padding: "1rem" }}>
                  <i className="bi bi-check2-circle" /> Queue empty — nothing needs review.
                </td></tr>
              )}
            </tbody>
          </table>
        </div>

        <div className="card-panel" style={{ maxHeight: "78vh", overflow: "auto" }}>
          {!current && <span className="muted">Nothing selected.</span>}
          {current && (
            <>
              <div style={{ display: "flex", justifyContent: "space-between", alignItems: "center", gap: ".5rem", flexWrap: "wrap", marginBottom: ".4rem" }}>
                <div style={{ display: "flex", gap: ".5rem", alignItems: "center" }}>
                  <strong className="mono" style={{ fontSize: "1.05rem" }}>{current.email_id}</strong>
                  <Badge term={termOf(vocab?.statuses, detail?.status ?? current.status)} />
                </div>
                <button className="btn" onClick={() => nav(`/records/${current.email_id}`)} title="View full details">
                  <i className="bi bi-box-arrow-up-right" /> Details
                </button>
              </div>

              {!detail && <p className="muted"><span className="spinner" /> Loading&hellip;</p>}

              {detail && (
                <div className="email-box">
                  <div className="email-box-header">
                    <div style={{ flex: 1, minWidth: 0 }}>
                      <div className="email-subject">
                        {detail.subject || "(No Subject)"}
                      </div>
                      <div className="email-meta">
                        {detail.sender && (
                          <span><i className="bi bi-envelope" /> <strong>From:</strong> {detail.sender}</span>
                        )}
                        {detail.received_at && (
                          <span><i className="bi bi-clock" /> {detail.received_at.replace("T", " ").slice(0, 19)}</span>
                        )}
                        {detail.category && (
                          <span className="tag tone-primary">{termOf(vocab?.categories, detail.category).label}</span>
                        )}
                      </div>
                    </div>
                    <button className="btn" style={{ padding: ".2rem .5rem", fontSize: ".72rem" }}
                            onClick={() => setShowEmail((v) => !v)}
                            title={showEmail ? "Collapse email content" : "Expand email content"}>
                      <i className={`bi bi-chevron-${showEmail ? "up" : "down"}`} />
                      {showEmail ? "Hide email" : "View email"}
                    </button>
                  </div>

                  {showEmail && detail.body && (
                    <div className="email-body">
                      {detail.body}
                    </div>
                  )}
                  {showEmail && !detail.body && (
                    <div className="muted" style={{ fontSize: ".76rem", fontStyle: "italic", padding: ".4rem" }}>
                      No email body recorded.
                    </div>
                  )}

                  {detail.attachments && detail.attachments.length > 0 && (
                    <div className="email-attachments">
                      <span className="muted" style={{ fontWeight: 600 }}>
                        <i className="bi bi-paperclip" /> Attachments ({detail.attachments.length}):
                      </span>
                      {detail.attachments.map((att) => (
                        <span key={att.attachment_id} className="tag tone-muted" title={att.detected_type ?? "Attachment"}>
                          {att.filename} {att.detected_type ? `(${att.detected_type})` : ""}
                        </span>
                      ))}
                    </div>
                  )}
                  {detail.attachments && detail.attachments.length === 0 && (
                    <div className="email-attachments">
                      <span className="tag tone-warning" style={{ fontSize: ".7rem" }}>
                        <i className="bi bi-paperclip" /> No attachments found
                      </span>
                    </div>
                  )}
                </div>
              )}

              {detail && detail.comparisons.length > 0 && (
                <table className="cmp" style={{ marginTop: ".8rem" }}>
                  <thead><tr><th>Field</th><th>SI</th><th>BL</th><th /></tr></thead>
                  <tbody>
                    {orderFields(detail, vocab?.fields).map((c) => (
                      <tr key={c.field} className={`v-${c.verdict}`}>
                        <td>{vocab?.fields.find((f) => f.value === c.field)?.label ?? c.field}</td>
                        <td>
                          <button className="val" onClick={() => setTarget({
                            emailId: detail.email_id, role: "SI", field: c.field,
                            value: c.si_value, evidence: c.si_evidence,
                            attachmentId: detail.attachments?.find((a) => a.filename.includes("_SI"))?.attachment_id,
                          })}>{c.si_value ?? "—"}</button>
                        </td>
                        <td>
                          <button className="val" onClick={() => setTarget({
                            emailId: detail.email_id, role: "BL", field: c.field,
                            value: c.bl_value, evidence: c.bl_evidence,
                            attachmentId: detail.attachments?.find((a) => a.filename.includes("_BL"))?.attachment_id,
                          })}>{c.bl_value ?? "—"}</button>
                        </td>
                        <td><Badge term={termOf(vocab?.verdicts, c.verdict)} /></td>
                      </tr>
                    ))}
                  </tbody>
                </table>
              )}
              {detail && detail.comparisons.length === 0 && (
                <p className="muted" style={{ marginTop: ".8rem" }}>
                  <i className="bi bi-info-circle" /> No comparison was performed — {termOf(vocab?.review_reasons, detail.review_reason).label}.
                </p>
              )}

              <div className="card-panel" style={{ marginTop: "1rem", background: "var(--bg-main)", border: "1px solid var(--border-color)", padding: ".9rem 1rem" }}>
                <div style={{ fontWeight: 600, fontSize: ".88rem", marginBottom: ".3rem", display: "flex", alignItems: "center", gap: ".4rem" }}>
                  <i className="bi bi-tag-fill" style={{ color: "var(--primary)" }} />
                  <span>Manual Email Classification</span>
                </div>
                <p className="muted" style={{ margin: "0 0 .75rem", fontSize: ".76rem" }}>
                  Select which category this email belongs to and confirm:
                </p>
                <div style={{ display: "flex", gap: ".6rem", alignItems: "center", flexWrap: "wrap" }}>
                  <select
                    value={selectedCategory}
                    onChange={(e) => setSelectedCategory(e.target.value)}
                    disabled={busy}
                    style={{ minWidth: "220px", padding: ".42rem .65rem", borderRadius: "var(--r)", border: "1px solid var(--border-color)", background: "#fff", fontWeight: 500, fontSize: ".84rem" }}
                  >
                    <option value="BL_COMPARISON">BL Comparison (Bill of Lading vs SI)</option>
                    <option value="SI_REQUEST">SI Request (Shipping Instructions)</option>
                    <option value="INVOICE_QUERY">Invoice Query (Billing / Accounting)</option>
                    <option value="GENERAL">General (General correspondence)</option>
                    <option value="SPAM">Spam (Unrelated / Junk)</option>
                  </select>
                  <button
                    className="btn primary"
                    disabled={busy || !selectedCategory}
                    onClick={() => act("override_category", { field: null, corrected_category: selectedCategory })}
                  >
                    {busy ? <span className="spinner" /> : <i className="bi bi-check-lg" />}
                    Confirm Classification
                  </button>
                </div>
              </div>
            </>
          )}
        </div>
      </div>

      {target && <EvidenceDrawer target={target} onClose={() => setTarget(null)} />}
    </>
  );
}

/** Document order, from the vocabulary — same reason as on the detail screen. */
function orderFields(d: RecordDetail, fields?: { value: string }[]) {
  const order = fields?.map((f) => f.value) ?? [];
  return [...d.comparisons].sort(
    (a, b) => (order.indexOf(a.field) + 1 || 99) - (order.indexOf(b.field) + 1 || 99));
}
