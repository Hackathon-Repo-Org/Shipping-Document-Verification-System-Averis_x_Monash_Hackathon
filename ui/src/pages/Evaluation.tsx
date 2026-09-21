/**
 * EVALUATION — the four axes, and which provider produced which number.
 *
 * The provider comparison is served from a committed file rather than recomputed:
 * it took two full runs and a paid API to produce, and a dashboard must not be able
 * to trigger that by being refreshed.
 */
import { useEffect, useState } from "react";
import { ApiError, api, type Evaluation as Ev } from "../lib/api";

interface ProviderTable {
  title?: string; note?: string; caveat?: string;
  left?: string; right?: string;
  axes?: Array<[string, string, string]>;
  edge_cases?: string;
}

export function Evaluation() {
  const [e, setE] = useState<Ev | null>(null);
  const [err, setErr] = useState<string | null>(null);

  useEffect(() => {
    api.evaluation().then(setE).catch((x: ApiError) => setErr(x.message));
  }, []);

  if (err) {
    return <div className="banner err"><i className="bi bi-exclamation-triangle" /><span>{err}</span></div>;
  }
  if (!e) return <div className="muted"><span className="spinner" /> Loading&hellip;</div>;

  const p = (e.providers ?? {}) as ProviderTable;
  const axes = p.axes ?? [];

  return (
    <>
      <h1 style={{ fontSize: "1.1rem", marginTop: 0 }}>Evaluation</h1>

      {e.latest && (
        <div className="card-panel">
          <h2>Latest scored run</h2>
          <div className="kpi-row">
            {([["Stage 1 macro-F1", "macro_f1"],
               ["Stage 3 defect-F1", "defect_f1"],
               ["End-to-end", "end_to_end"],
               ["Final score", "final_score"]] as const).map(([label, key]) => (
              <div className="kpi-card" key={key}>
                <div className="kpi-label">{label}</div>
                <div className="kpi-value">{fmtPercent(e.latest?.[key])}</div>
              </div>
            ))}
          </div>
        </div>
      )}

      {axes.length > 0 && (
        <div className="card-panel">
          <h2>{p.title ?? "Provider comparison"}</h2>
          {p.note && <p className="muted" style={{ fontSize: ".8rem", marginTop: 0 }}>{p.note}</p>}
          <div className="table-wrap">
            <table className="data">
              <thead>
                <tr>
                  <th>Axis</th>
                  <th>{p.left ?? "local"}</th>
                  <th>{p.right ?? "hosted"}</th>
                </tr>
              </thead>
              <tbody>
                {axes.map(([axis, l, r]) => (
                  <tr key={axis}>
                    <td>{axis}</td>
                    <td><strong>{fmtPercent(l)}</strong></td>
                    <td>{fmtPercent(r)}</td>
                  </tr>
                ))}
              </tbody>
            </table>
          </div>
          {p.edge_cases && (
            <p className="muted" style={{ fontSize: ".8rem" }}>{p.edge_cases}</p>
          )}
          {p.caveat && (
            <div className="banner warn" style={{ marginTop: ".8rem" }}>
              <i className="bi bi-info-circle" /><span>{p.caveat}</span>
            </div>
          )}
        </div>
      )}

      {axes.length === 0 && (
        <div className="card-panel muted">
          No provider comparison is published with this build.
        </div>
      )}
    </>
  );
}

function fmtPercent(v: unknown): string {
  if (v === null || v === undefined || v === "") return "—";
  if (typeof v === "number") {
    if (v >= 0 && v <= 1) {
      const pct = v * 100;
      return pct === 100 || pct === 0 ? `${pct}%` : `${pct.toFixed(2)}%`;
    }
    return String(v);
  }
  if (typeof v === "string") {
    const s = v.trim();
    // Fraction check like "46/46"
    const frac = s.match(/^(\d+)\s*\/\s*(\d+)$/);
    if (frac) {
      const n = parseInt(frac[1], 10);
      const d = parseInt(frac[2], 10);
      if (d > 0) {
        const pct = (n / d) * 100;
        const pStr = pct === 100 || pct === 0 ? `${pct}%` : `${pct.toFixed(1)}%`;
        return `${pStr} (${s})`;
      }
    }
    // Only parse decimals with a dot (e.g. "0.9526", "1.0000") to avoid converting integer counts like "23", "5", "1", "0"
    if (s.includes(".")) {
      const num = Number(s);
      if (!isNaN(num) && num >= 0 && num <= 1) {
        const pct = num * 100;
        return pct === 100 || pct === 0 ? `${pct}%` : `${pct.toFixed(2)}%`;
      }
    }
    return s;
  }
  return String(v);
}
