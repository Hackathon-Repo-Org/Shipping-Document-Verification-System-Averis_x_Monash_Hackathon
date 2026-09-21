/**
 * Page shell: 64px header + left nav, adopted from the team's prototype.
 * See docs/design-notes.md for what was taken and what was not.
 *
 * The health banner is not decoration. Without it, an unreachable API renders as a
 * dashboard full of zeros — which looks like a legitimate "nothing to review" and is
 * the worst possible failure during a demo. It must say "unreachable", not imply
 * "empty".
 */
import { NavLink, Outlet } from "react-router-dom";
import { useEffect, useState } from "react";
import { ApiError, api, getPasscode, getReviewer, setPasscode, setReviewer,
         type Health } from "../lib/api";

const NAV = [
  { to: "/", label: "Dashboard", icon: "speedometer2", end: true },
  { to: "/inbox", label: "Inbox", icon: "inbox" },
  { to: "/queue", label: "Review queue", icon: "list-check" },
  { to: "/proposals", label: "Label proposals", icon: "tags" },
  { to: "/evaluation", label: "Evaluation", icon: "graph-up" },
  { to: "/try", label: "Try it yourself", icon: "lightbulb" },
];

export function Shell() {
  const [health, setHealth] = useState<Health | null>(null);
  const [err, setErr] = useState<string | null>(null);
  const [checking, setChecking] = useState(true);
  const [open, setOpen] = useState(false);

  useEffect(() => {
    let alive = true;
    const poll = () => {
      api.health()
        .then((h) => { if (alive) { setHealth(h); setErr(null); setChecking(false); } })
        .catch((e: ApiError) => { if (alive) { setErr(e.message); setChecking(false); } });
    };
    poll();
    const t = setInterval(poll, 20000);
    return () => { alive = false; clearInterval(t); };
  }, []);

  return (
    <>
      <header className="app-header">
        <div className="logo-area">
          <i className="bi bi-box-seam-fill" />
          <span>ShipDoc</span>
          <span className="tag tone-muted" style={{ marginLeft: ".4rem" }}>
            document verification
          </span>
        </div>
        <div style={{ display: "flex", alignItems: "center", gap: ".6rem" }}>
          <button className="btn" onClick={() => setOpen((v) => !v)}>
            <i className="bi bi-person-badge" />
            {getReviewer() || "Sign in"}
          </button>
        </div>
      </header>

      {open && <ReviewerPanel onClose={() => setOpen(false)} health={health} />}

      <div className="main-layout">
        <nav className="side">
          <div className="nav-label">Review</div>
          {NAV.map((n) => (
            <NavLink key={n.to} to={n.to} end={n.end}
                     className={({ isActive }) => `nav-btn${isActive ? " active" : ""}`}>
              <i className={`bi bi-${n.icon}`} />
              {n.label}
            </NavLink>
          ))}
        </nav>
        <main className="content">
          {checking && (
            <div className="banner info">
              <span className="spinner" />
              <span>Contacting the API&hellip; a container can take ~20s to wake.</span>
            </div>
          )}
          {err && (
            <div className="banner err">
              <i className="bi bi-plug" />
              <span><strong>API: unreachable.</strong> {err}</span>
            </div>
          )}
          {health && health.status !== "ok" && (
            <div className="banner warn">
              <i className="bi bi-exclamation-triangle" />
              <span><strong>API: degraded.</strong> {health.detail ?? "Some features are unavailable."}</span>
            </div>
          )}
          {!err && <Outlet />}
        </main>
      </div>
    </>
  );
}

/** Reviewer name + passcode. Both are stored locally; the passcode is only ever
 *  sent as a header, never as a cookie, because the two halves are different
 *  origins and a third-party cookie is blocked in incognito. */
function ReviewerPanel({ onClose, health }:
                       { onClose: () => void; health: Health | null }) {
  const [name, setName] = useState(getReviewer());
  const [code, setCode] = useState(getPasscode());
  return (
    <>
      <div className="drawer-backdrop" onClick={onClose} />
      <aside className="drawer" style={{ width: "min(420px, 100vw)" }} role="dialog">
        <header>
          <strong>Reviewer</strong>
          <button className="btn" onClick={onClose}>Close</button>
        </header>
        <div style={{ padding: "1rem", display: "grid", gap: ".8rem" }}>
          <label style={{ display: "grid", gap: ".3rem" }}>
            <span className="kpi-label">Your name</span>
            <input value={name} onChange={(e) => setName(e.target.value)}
                   placeholder="e.g. a.reviewer" />
            <span className="muted" style={{ fontSize: ".74rem" }}>
              Recorded on every decision you make. An unattributed decision is not
              auditable, so this is required before you can act.
            </span>
          </label>
          <label style={{ display: "grid", gap: ".3rem" }}>
            <span className="kpi-label">Demo passcode</span>
            <input value={code} type="password"
                   onChange={(e) => setCode(e.target.value)}
                   placeholder="on the slide" />
            <span className="muted" style={{ fontSize: ".74rem" }}>
              Reading is open to everyone. Writing needs the code.
              {health && !health.writes_enabled &&
                " This deployment is currently read-only."}
            </span>
          </label>
          <button className="btn primary"
                  onClick={() => { setReviewer(name.trim()); setPasscode(code.trim()); onClose(); }}>
            Save
          </button>
        </div>
      </aside>
    </>
  );
}
