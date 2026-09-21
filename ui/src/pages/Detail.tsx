/**
 * EMAIL DETAIL — the most important screen.
 *
 * Built from what `python -m shipdoc inspect <email_id>` already prints, because
 * that command was designed for exactly this question and has been read by humans
 * for eleven phases. The columns, the ordering and the evidence line are its layout.
 *
 * Three things this screen must never do:
 *   1. Show a value without letting you open its source. Every value is a button.
 *   2. Present an INFERRED value as if it were read. A value resolved by reference
 *      (SAME AS CONSIGNEE), pre-filled by OCR, or found via a learned label says so
 *      on the row.
 *   3. Distinguish verdicts by colour alone.
 */
import { useCallback, useEffect, useState } from "react";
import { useParams, Link } from "react-router-dom";
import { ApiError, api, getReviewer, type Comparison, type RecordDetail } from "../lib/api";
import { Badge, termOf, useVocab } from "../lib/vocab";
import { EvidenceDrawer, type EvidenceTarget } from "../components/Evidence";
import { DecisionBar } from "../components/DecisionBar";

export function Detail() {
  const { emailId = "" } = useParams();
  const vocab = useVocab();
  const [rec, setRec] = useState<RecordDetail | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [target, setTarget] = useState<EvidenceTarget | null>(null);
  const [showTrace, setShowTrace] = useState(false);
  const [showEmail, setShowEmail] = useState(true);

  const load = useCallback(() => {
    setErr(null);
    api.record(emailId).then(setRec).catch((e: ApiError) => setErr(e.message));
  }, [emailId]);
  useEffect(load, [load]);

  const attachmentFor = (role: "SI" | "BL") =>
    rec?.attachments?.find((a) => a.filename.toUpperCase().includes(`_${role}.`))
    ?? rec?.attachments?.find((a) => a.detected_type === role);

  if (err) return <div className="banner err"><i className="bi bi-exclamation-triangle" /><span>{err}</span></div>;
  if (!rec) return <div className="muted"><span className="spinner" /> Loading&hellip;</div>;

  // Render in DOCUMENT order, not alphabetically. The vocabulary supplies the order
  // the fields appear on a bill of lading, so the screen reads like the paper it is
  // checking. The API returns them sorted by name, which is convenient for a machine
  // and wrong for a person.
  const order = vocab?.fields.map((f) => f.value) ?? [];
  const comparisons = [...rec.comparisons].sort(
    (a, b) => (order.indexOf(a.field) + 1 || 99) - (order.indexOf(b.field) + 1 || 99));

  const statusTerm = termOf(vocab?.statuses, rec.status);
  const catTerm = termOf(vocab?.categories, rec.category);
  const fieldLabel = (f: string) =>
    vocab?.fields.find((x) => x.value === f)?.label ?? f;

  return (
    <>
      <div style={{ display: "flex", alignItems: "center", gap: ".6rem", marginBottom: ".8rem", flexWrap: "wrap" }}>
        <Link to="/inbox" className="btn"><i className="bi bi-arrow-left" /> Inbox</Link>
        <h1 className="mono" style={{ fontSize: "1.1rem", margin: 0 }}>{rec.email_id}</h1>
        <Badge term={catTerm} />
        <Badge term={statusTerm} />
        {rec.human_decided && (
          <span className="tag tone-primary" title="A human has ruled on this record">
            <i className="bi bi-person-check" /> human decided
          </span>
        )}
        {rec.bypassed_state_rule && (
          <span className="tag tone-warning"
                title="A record-level override moved this out of review. That bypasses the monotone state rule and is shown deliberately.">
            <i className="bi bi-shield-exclamation" /> state rule bypassed
          </span>
        )}
      </div>

      <div className="email-box" style={{ marginBottom: "1rem" }}>
        <div className="email-box-header">
          <div style={{ flex: 1, minWidth: 0 }}>
            <div className="email-subject">
              {rec.subject || "(No Subject)"}
            </div>
            <div className="email-meta">
              {rec.sender && (
                <span><i className="bi bi-envelope" /> <strong>From:</strong> {rec.sender}</span>
              )}
              {rec.received_at && (
                <span><i className="bi bi-clock" /> {rec.received_at.replace("T", " ").slice(0, 19)}</span>
              )}
              {rec.category && (
                <span className="tag tone-primary">{catTerm.label}</span>
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

        {showEmail && rec.body && (
          <div className="email-body">
            {rec.body}
          </div>
        )}
        {showEmail && !rec.body && (
          <div className="muted" style={{ fontSize: ".76rem", fontStyle: "italic", padding: ".4rem" }}>
            No email body recorded.
          </div>
        )}

        {rec.attachments && rec.attachments.length > 0 && (
          <div className="email-attachments">
            <span className="muted" style={{ fontWeight: 600 }}>
              <i className="bi bi-paperclip" /> Attachments ({rec.attachments.length}):
            </span>
            {rec.attachments.map((att) => (
              <span key={att.attachment_id} className="tag tone-muted" title={att.detected_type ?? "Attachment"}>
                {att.filename} {att.detected_type ? `(${att.detected_type})` : ""}
              </span>
            ))}
          </div>
        )}
        {rec.attachments && rec.attachments.length === 0 && (
          <div className="email-attachments">
            <span className="tag tone-warning" style={{ fontSize: ".7rem" }}>
              <i className="bi bi-paperclip" /> No attachments found
            </span>
          </div>
        )}
      </div>

      <div className="card-panel">
        <h2>Seven compared fields</h2>
        <div className="table-wrap">
          <table className="cmp">
            <thead>
              <tr>
                <th style={{ width: "13rem" }}>Field</th>
                <th>SI <span className="muted">(authoritative)</span></th>
                <th>BL <span className="muted">(draft)</span></th>
                <th style={{ width: "10rem" }}>Verdict</th>
              </tr>
            </thead>
            <tbody>
              {comparisons.map((c) => (
                <Row key={c.field} c={c} label={fieldLabel(c.field)}
                     verdictTerm={termOf(vocab?.verdicts, c.verdict)}
                     onOpen={(role) => setTarget({
                       emailId: rec.email_id, role, field: fieldLabel(c.field),
                       value: role === "SI" ? c.si_value : c.bl_value,
                       evidence: role === "SI" ? c.si_evidence : c.bl_evidence,
                       attachmentId: attachmentFor(role)?.attachment_id,
                       filename: attachmentFor(role)?.filename,
                     })} />
              ))}
            </tbody>
          </table>
        </div>
        {comparisons.length === 0 && (
          <p className="muted">
            No comparison was performed.
            {rec.review_reason && <> Reason: <strong>{termOf(vocab?.review_reasons, rec.review_reason).label}</strong>.</>}
          </p>
        )}
        <p className="muted" style={{ fontSize: ".74rem", marginBottom: 0 }}>
          <i className="bi bi-info-circle" /> Click any value to open the document it
          was read from, with the line highlighted.
        </p>
      </div>

      <DecisionBar record={rec} onDone={setRec} reviewer={getReviewer()} />

      {rec.decisions.length > 0 && (
        <div className="card-panel">
          <h2>Decisions on this record</h2>
          <table className="data">
            <thead><tr><th>What</th><th>Field</th><th>Who</th><th>When</th><th>Note</th></tr></thead>
            <tbody>
              {rec.decisions.map((d) => (
                <tr key={d.decision_id}>
                  <td>{termOf(vocab?.decision_types, d.decision_type).label}</td>
                  <td className="mono">{d.field ?? <span className="muted">record</span>}</td>
                  <td>{d.reviewer}</td>
                  <td className="muted">{d.decided_at?.replace("T", " ").slice(0, 16)}</td>
                  <td className="muted">{d.note || "—"}</td>
                </tr>
              ))}
            </tbody>
          </table>
        </div>
      )}

      <div className="card-panel">
        <button className="btn" onClick={() => setShowTrace((v) => !v)}>
          <i className={`bi bi-chevron-${showTrace ? "down" : "right"}`} />
          Stage trace ({rec.events.length})
        </button>
        {showTrace && (
          <table className="data" style={{ marginTop: ".6rem" }}>
            <thead><tr><th>#</th><th>Stage</th><th>Outcome</th><th>Detail</th></tr></thead>
            <tbody>
              {rec.events.map((e) => (
                <tr key={e.seq}>
                  <td className="muted">{e.seq}</td>
                  <td>{e.stage}</td>
                  <td>{e.outcome}</td>
                  <td className="muted mono" style={{ fontSize: ".74rem" }}>
                    {(e.detail ?? "").slice(0, 160)}
                  </td>
                </tr>
              ))}
            </tbody>
          </table>
        )}
      </div>

      {target && <EvidenceDrawer target={target} onClose={() => setTarget(null)} />}
    </>
  );
}

function Row({ c, label, verdictTerm, onOpen }: {
  c: Comparison; label: string;
  verdictTerm: ReturnType<typeof termOf>;
  onOpen: (role: "SI" | "BL") => void;
}) {
  return (
    <tr className={`v-${c.verdict}`}>
      <td><strong>{label}</strong></td>
      <td><Value v={c.si_value} ev={c.si_evidence} onOpen={() => onOpen("SI")} /></td>
      <td><Value v={c.bl_value} ev={c.bl_evidence} onOpen={() => onOpen("BL")} /></td>
      <td>
        <Badge term={verdictTerm} />
        {c.decided_by_human && (
          <div className="note"><i className="bi bi-person-check" /> set by a reviewer</div>
        )}
        {c.detail && (
          <div className="note" title={c.detail}>{c.detail.slice(0, 44)}</div>
        )}
      </td>
    </tr>
  );
}

/** A value, plus an honest note about where it came from if it was not simply read. */
function Value({ v, ev, onOpen }: {
  v: string | null; ev: Comparison["si_evidence"]; onOpen: () => void;
}) {
  if (!v) {
    return (
      <span className="val empty" title="No value was extracted on this side">
        — not found
      </span>
    );
  }
  return (
    <>
      <button className="val" onClick={onOpen}
              title="Open the source document at this line">
        {v.length > 64 ? `${v.slice(0, 64)}…` : v}
      </button>
      {ev?.resolved_from && (
        <div className="note inferred">
          <i className="bi bi-signpost-split" />
          said <span className="mono">{ev.reference_text}</span> — taken from {ev.resolved_from}
        </div>
      )}
      {ev?.method === "ocr" && (
        <div className="note inferred"><i className="bi bi-eye" /> OCR pre-fill — verify</div>
      )}
      {ev?.locator && !ev.resolved_from && (
        <div className="note"><i className="bi bi-file-earmark-text" /> {ev.locator}</div>
      )}
    </>
  );
}
