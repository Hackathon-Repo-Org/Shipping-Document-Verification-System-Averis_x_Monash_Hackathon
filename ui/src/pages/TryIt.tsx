/**
 * TRY IT YOURSELF — paste an email and two documents, see what the system makes of
 * them.
 *
 * This exists because a dashboard full of green ticks proves nothing to someone who
 * did not produce the data. The fastest way to believe a verification system is to
 * hand it something you wrote and watch it find the thing you hid.
 *
 * It runs the REAL engine — same normaliser, same comparators, same state machine.
 * Only ingest and extract are skipped, because the text arrived as text.
 *
 * No passcode: gating this behind a code a visitor does not have defeats the point.
 * Safe to leave open because it stores nothing, needs no API key, and is size-capped.
 */
import { useState } from "react";
import { ApiError, API_BASE, type Comparison, type RecordDetail } from "../lib/api";
import { Badge, termOf, useVocab } from "../lib/vocab";

interface Sample {
  subject: string; body: string; si_text: string; bl_text: string;
  expect: string[];
}

export function TryIt() {
  const vocab = useVocab();
  const [subject, setSubject] = useState("");
  const [body, setBody] = useState("");
  const [si, setSi] = useState("");
  const [bl, setBl] = useState("");
  const [result, setResult] = useState<(RecordDetail & { unknown_labels?: string[] }) | null>(null);
  const [expect, setExpect] = useState<string[] | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [busy, setBusy] = useState(false);
  const [approvedLabels, setApprovedLabels] = useState<Record<string, {label: string, field: string}>>({});

  async function loadSample(path = "/api/try/sample") {
    setErr(null);
    try {
      const r = await fetch(`${API_BASE}${path}`);
      if (!r.ok) throw new Error(String(r.status));
      const s: Sample = await r.json();
      setSubject(s.subject); setBody(s.body); setSi(s.si_text); setBl(s.bl_text);
      setExpect(s.expect); setResult(null); setApprovedLabels({});
    } catch {
      setErr("Could not load the example. Is the API reachable?");
    }
  }

  async function run(useLabels = false) {
    setBusy(true); setErr(null); setResult(null);
    try {
      const extra_labels = useLabels ? Object.values(approvedLabels).map(l => ({
        label: l.label, proposed_field: l.field
      })) : [];
      
      const r = await fetch(`${API_BASE}/api/try`, {
        method: "POST",
        headers: { "Content-Type": "application/json" },
        body: JSON.stringify({ subject, body, si_text: si, bl_text: bl, extra_labels }),
      });
      const j = await r.json();
      if (!r.ok) throw new ApiError(r.status, j?.detail ?? "Request failed");
      setResult(j);
      if (!useLabels) setApprovedLabels({});
    } catch (e) {
      setErr(e instanceof ApiError ? e.message
        : `Could not reach the API at ${API_BASE}.`);
    } finally {
      setBusy(false);
    }
  }

  function clearAll() {
    setSubject(""); setBody(""); setSi(""); setBl("");
    setResult(null); setExpect(null); setErr(null);
  }

  const order = vocab?.fields.map((f) => f.value) ?? [];
  const comparisons = [...(result?.comparisons ?? [])].sort(
    (a, b) => (order.indexOf(a.field) + 1 || 99) - (order.indexOf(b.field) + 1 || 99));

  return (
    <>
      <h1 style={{ fontSize: "1.1rem", marginTop: 0 }}>Try it yourself</h1>

      <div className="banner info">
        <i className="bi bi-lightbulb" />
        <span>
          Paste a shipping instruction and a draft bill of lading and see what the
          system finds. This runs the <strong>real engine</strong> — the same
          normaliser, comparators and state machine as the 520-record batch.
          Nothing you paste is stored, and no API key is used.
        </span>
      </div>

      <div className="card-panel">
        <div className="filters" style={{ flexWrap: "wrap" }}>
          <button className="btn" onClick={() => loadSample()}>
            <i className="bi bi-file-earmark-text" /> Example 1 (Standard)
          </button>
          <button className="btn" onClick={() => loadSample("/api/try/sample2")}>
            <i className="bi bi-tags" /> Example 2 (Short forms)
          </button>
          <button className="btn primary" onClick={() => run(false)}
                  disabled={busy || !(subject || body || si || bl)}>
            {busy ? <span className="spinner" /> : <i className="bi bi-play-fill" />}
            Run it
          </button>
          <button className="btn" onClick={clearAll} disabled={busy}>Clear</button>
          <span className="muted" style={{ fontSize: ".76rem", width: "100%", marginTop: ".25rem" }}>
            Tip: try loading Example 2 to test the AI label mapping feature!
          </span>
        </div>

        <div style={{ display: "grid", gap: ".7rem" }}>
          <label style={{ display: "grid", gap: ".25rem" }}>
            <span className="kpi-label">Email subject</span>
            <input value={subject} onChange={(e) => setSubject(e.target.value)}
                   placeholder="DRAFT BL CHECK - booking ref - port - shipper" />
          </label>
          <label style={{ display: "grid", gap: ".25rem" }}>
            <span className="kpi-label">
              Email body — this decides the CATEGORY
            </span>
            <textarea value={body} onChange={(e) => setBody(e.target.value)} rows={4}
                      placeholder="Please compare the attached SI and draft BL and confirm the discrepancies." />
          </label>

          <div style={{ display: "grid", gap: ".7rem", gridTemplateColumns: "1fr 1fr" }}
               className="try-split">
            <label style={{ display: "grid", gap: ".25rem", minWidth: 0 }}>
              <span className="kpi-label">Shipping instruction (authoritative)</span>
              <textarea value={si} onChange={(e) => setSi(e.target.value)} rows={12}
                        className="mono" style={{ fontSize: ".76rem" }}
                        placeholder={"Shipper: ...\nConsignee: ...\nPort of Loading: ...\nPort of Discharge: ...\nNo. of Containers: 2 x 40'HC\nGROSS WEIGHT: 22,450.50 KGS"} />
            </label>
            <label style={{ display: "grid", gap: ".25rem", minWidth: 0 }}>
              <span className="kpi-label">Draft bill of lading</span>
              <textarea value={bl} onChange={(e) => setBl(e.target.value)} rows={12}
                        className="mono" style={{ fontSize: ".76rem" }}
                        placeholder={"Shipper: ...\nConsignee: ...\nPOL: ...\nPort of Discharge: ...\nContainer Count: 2 x 40'HC\nGross Weight (KG): 22.450,50 KGS"} />
            </label>
          </div>
        </div>
      </div>

      {err && <div className="banner err"><i className="bi bi-exclamation-triangle" /><span>{err}</span></div>}

      {expect && !result && (
        <div className="card-panel">
          <h2>What this example should show</h2>
          <ul className="muted" style={{ margin: 0, paddingLeft: "1.1rem", fontSize: ".82rem" }}>
            {expect.map((x, i) => <li key={i} style={{ marginBottom: ".3rem" }}>{x}</li>)}
          </ul>
        </div>
      )}

      {result && (
        <div className="card-panel">
          <div style={{ display: "flex", gap: ".5rem", alignItems: "center",
                        flexWrap: "wrap", marginBottom: ".6rem" }}>
            <Badge term={termOf(vocab?.categories, result.category)} />
            <Badge term={termOf(vocab?.statuses, result.status)} />
            {result.review_reason && (
              <span className="tag tone-warning">
                {termOf(vocab?.review_reasons, result.review_reason).label}
              </span>
            )}
            {result.decided_by && (
              <span className="tag tone-muted" title="Which kind of logic settled the category">
                <i className="bi bi-cpu" /> decided by {result.decided_by}
              </span>
            )}
          </div>

          {comparisons.length > 0 ? (
            <div className="table-wrap">
              <table className="cmp">
                <thead>
                  <tr><th style={{ width: "12rem" }}>Field</th><th>SI</th><th>BL</th>
                      <th style={{ width: "10rem" }}>Verdict</th></tr>
                </thead>
                <tbody>
                  {comparisons.map((c) => <TryRow key={c.field} c={c}
                    label={vocab?.fields.find((f) => f.value === c.field)?.label ?? c.field}
                    term={termOf(vocab?.verdicts, c.verdict)} />)}
                </tbody>
              </table>
            </div>
          ) : (
            <p className="muted">
              No comparison was performed — the system did not read this as a request
              to compare two documents, or one of them was missing.
            </p>
          )}

          {(result.label_proposals?.length ?? 0) > 0 ? (
            <div className="card-panel" style={{ marginTop: ".8rem", border: "1px solid var(--accent-orange)" }}>
              <h3 style={{ marginTop: 0, display: "flex", gap: ".5rem", alignItems: "center" }}>
                <i className="bi bi-tags" style={{ color: "var(--accent-orange)" }} />
                Unknown Labels ({result.label_proposals!.length} found)
              </h3>
              <p className="muted" style={{ fontSize: ".82rem", marginBottom: "1rem" }}>
                The model has proposed field mappings for these labels. Approve them to temporarily apply them as deterministic rules for this run.
              </p>
              
              <div style={{ display: "grid", gap: ".8rem", marginBottom: "1rem" }}>
                {result.label_proposals!.map(p => (
                  <div key={p.normalised} style={{ padding: ".8rem", background: "var(--bg-lighter)", borderRadius: "4px" }}>
                    <div style={{ display: "flex", justifyContent: "space-between", marginBottom: ".5rem" }}>
                      <div style={{ display: "flex", alignItems: "center", gap: ".5rem" }}>
                        <strong>{p.label}</strong> → {p.proposed_field ? (
                          <Badge term={termOf(vocab?.fields, p.proposed_field)} />
                        ) : (
                          <select 
                            className="input-sm"
                            onChange={(e) => {
                              const field = e.target.value;
                              if (field) {
                                setApprovedLabels(prev => ({
                                  ...prev, 
                                  [p.normalised]: { label: p.label, field }
                                }));
                              }
                            }}
                            value={approvedLabels[p.normalised]?.field || ""}
                          >
                            <option value="">-- select field --</option>
                            {vocab?.fields.map(f => (
                              <option key={f.value} value={f.value}>{f.label}</option>
                            ))}
                          </select>
                        )}
                      </div>
                      <div style={{ display: "flex", gap: ".3rem" }}>
                        <button 
                          className={`btn ${approvedLabels[p.normalised] ? "primary" : ""}`}
                          style={{ padding: ".2rem .5rem", fontSize: ".75rem" }}
                          disabled={!p.proposed_field && !approvedLabels[p.normalised]?.field}
                          onClick={() => {
                            if (p.proposed_field) {
                              setApprovedLabels(prev => ({
                                ...prev, 
                                [p.normalised]: { label: p.label, field: p.proposed_field! }
                              }));
                            }
                          }}
                        >
                          <i className="bi bi-check2" /> Approve
                        </button>
                        <button 
                          className="btn"
                          style={{ padding: ".2rem .5rem", fontSize: ".75rem" }}
                          onClick={() => {
                            const next = { ...approvedLabels };
                            delete next[p.normalised];
                            setApprovedLabels(next);
                          }}
                        >
                          <i className="bi bi-x" /> Reject
                        </button>
                      </div>
                    </div>
                    <div className="mono muted" style={{ fontSize: ".75rem", whiteSpace: "pre-wrap" }}>
                      {p.context}
                    </div>
                  </div>
                ))}
              </div>
              
              <button 
                className="btn primary" 
                disabled={Object.keys(approvedLabels).length === 0 || busy}
                onClick={() => run(true)}
              >
                {busy ? <span className="spinner" /> : <i className="bi bi-arrow-repeat" />}
                Re-run with {Object.keys(approvedLabels).length} approved labels
              </button>
            </div>
          ) : (result.unknown_labels?.length ?? 0) > 0 && (
            <div className="banner warn" style={{ marginTop: ".8rem" }}>
              <i className="bi bi-tags" />
              <span>
                Labels it did not recognise: <span className="mono">
                {result.unknown_labels!.join(", ")}</span>.
              </span>
            </div>
          )}

          <details style={{ marginTop: ".8rem" }}>
            <summary className="muted" style={{ cursor: "pointer", fontSize: ".8rem" }}>
              Stage trace ({result.events.length})
            </summary>
            <table className="data" style={{ marginTop: ".5rem" }}>
              <tbody>
                {result.events.map((e) => (
                  <tr key={e.seq}>
                    <td className="muted" style={{ width: "2rem" }}>{e.seq}</td>
                    <td style={{ width: "7rem" }}>{e.stage}</td>
                    <td style={{ width: "6rem" }}>{e.outcome}</td>
                    <td className="muted mono" style={{ fontSize: ".74rem" }}>{e.detail}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </details>
        </div>
      )}
    </>
  );
}

function TryRow({ c, label, term }: {
  c: Comparison; label: string; term: ReturnType<typeof termOf>;
}) {
  const note = (ev: Comparison["si_evidence"]) =>
    ev?.resolved_from
      ? <div className="note inferred">
          <i className="bi bi-signpost-split" />
          said <span className="mono">{ev.reference_text}</span> — taken from {ev.resolved_from}
        </div>
      : null;
  return (
    <tr className={`v-${c.verdict}`}>
      <td><strong>{label}</strong></td>
      <td>{c.si_value ?? <span className="muted">— not found</span>}{note(c.si_evidence)}</td>
      <td>{c.bl_value ?? <span className="muted">— not found</span>}{note(c.bl_evidence)}</td>
      <td>
        <Badge term={term} />
        {c.detail && <div className="note">{c.detail.slice(0, 44)}</div>}
      </td>
    </tr>
  );
}
