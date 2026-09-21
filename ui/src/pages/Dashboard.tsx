import { useEffect, useState } from "react";
import { Link } from "react-router-dom";
import { ApiError, api, getReviewer, type Stats } from "../lib/api";
import { Badge, useVocab } from "../lib/vocab";

export function Dashboard() {
  const vocab = useVocab();
  const [s, setS] = useState<Stats | null>(null);
  const [err, setErr] = useState<string | null>(null);

  const load = () => api.stats().then(setS).catch((e: ApiError) => setErr(e.message));
  useEffect(() => { load(); }, []);

  if (err) return <div className="banner err"><i className="bi bi-exclamation-triangle" /><span>{err}</span></div>;
  if (!s) return <div className="muted"><span className="spinner" /> Loading&hellip;</div>;

  const total = Object.values(s.by_status).reduce((a, b) => a + b, 0);

  return (
    <>
      <h1 style={{ fontSize: "1.1rem", marginTop: 0 }}>Dashboard</h1>

      {msg && <div className="banner info"><i className="bi bi-check2-circle" /><span>{msg}</span></div>}

      <div className="kpi-row">
        <Kpi label="Records" value={total} sub={`${s.awaiting_documents} awaiting documents`} />
        <Kpi label="Confirmed defects" value={s.by_status.MISMATCH ?? 0}
             sub="documents that disagree" tone="danger" />
        <Kpi label="Needs review" value={s.by_status.NEEDS_REVIEW ?? 0}
             sub="a human has to look" tone="warning" />
        <Kpi label="Decided by AI" value={`${Math.round(s.ai_share * 100)}%`}
             sub={`${s.decided_by.llm ?? 0} model · ${s.decided_by.rule ?? 0} rules`} />
        <Kpi label="Cache hit rate"
             value={s.cache_hit_rate === null ? "—" : `${Math.round(s.cache_hit_rate * 100)}%`}
             sub={`${s.llm_calls} model calls this run`} />
        <Kpi label="Pending proposals" value={s.pending_proposals}
             sub="labels awaiting a human" />
      </div>

      <div className="card-panel">
        <h2>By status</h2>
        <div className="filters">
          {vocab?.statuses.map((t) => (
            <Link key={t.value} to={`/inbox?status=${t.value}`} className="btn">
              <Badge term={t} /> <strong>{s.by_status[t.value] ?? 0}</strong>
            </Link>
          ))}
        </div>
        <h2 style={{ marginTop: "1rem" }}>By category</h2>
        <div className="filters">
          {vocab?.categories.map((t) => (
            <Link key={t.value} to={`/inbox?category=${t.value}`} className="btn">
              <Badge term={t} /> <strong>{s.by_category[t.value] ?? 0}</strong>
            </Link>
          ))}
        </div>
      </div>

      <div className="card-panel">
        <h2>This run</h2>
        <table className="data">
          <tbody>
            <Row k="Model" v={`${s.provider ?? "—"} · ${s.model ?? "—"}`} />
            <Row k="Started" v={s.started_at?.replace("T", " ").slice(0, 19) ?? "—"} />
            <Row k="Code version" v={s.code_version ?? "—"} mono />
            <Row k="submission.json" v={s.submission_sha256 ?? "—"} mono />
            <Row k="Learned vocabulary" v={s.learned_labels_sha256 || "none"} mono />
            <Row k="Active human decisions" v={String(s.active_decisions)} />
          </tbody>
        </table>
        <p className="muted" style={{ fontSize: ".74rem" }}>
          These hashes are what make a run reproducible: the same code, config and
          vocabulary produce the same submission, byte for byte, on any platform.
        </p>
      </div>
    </>
  );
}

function Kpi({ label, value, sub, tone }: {
  label: string; value: number | string; sub?: string; tone?: string;
}) {
  return (
    <div className="kpi-card">
      <div className="kpi-label">{label}</div>
      <div className="kpi-value" style={tone ? { color: `var(--${tone})` } : undefined}>
        {value}
      </div>
      {sub && <div className="kpi-subtext">{sub}</div>}
    </div>
  );
}

function Row({ k, v, mono }: { k: string; v: string; mono?: boolean }) {
  return (
    <tr>
      <td className="muted" style={{ width: "13rem" }}>{k}</td>
      <td className={mono ? "mono" : undefined} style={{ wordBreak: "break-all" }}>{v}</td>
    </tr>
  );
}
