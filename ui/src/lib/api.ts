/**
 * The only place that talks to the backend.
 *
 * THE BASE URL IS BUILD-TIME CONFIG. Static Web Apps serves a built bundle — there
 * is no server to read an environment variable at request time — so the backend
 * origin is baked in by Vite from VITE_API_BASE_URL. No hostname appears anywhere
 * else in ui/, and `assertConfigured()` fails loudly rather than silently calling a
 * relative path that would 404 against the static host.
 *
 * THE PASSCODE IS A HEADER, NEVER A COOKIE. The frontend and the API are different
 * origins, so a cookie would be third-party and is blocked by Safari and by
 * incognito — which is exactly how a judge opens a public link. A custom header
 * makes every write a PREFLIGHTED request; the API must allow the header and the
 * OPTIONS method or reads will work and writes will fail.
 */

export const API_BASE: string =
  (import.meta.env.VITE_API_BASE_URL as string | undefined)?.replace(/\/$/, "") ?? "";

export const PASSCODE_HEADER = "X-Demo-Passcode";
const PASSCODE_KEY = "shipdoc.passcode";
const REVIEWER_KEY = "shipdoc.reviewer";

export function getPasscode(): string {
  try { return localStorage.getItem(PASSCODE_KEY) ?? ""; } catch { return ""; }
}
export function setPasscode(v: string) {
  try { localStorage.setItem(PASSCODE_KEY, v); } catch { /* private mode */ }
}
export function getReviewer(): string {
  try { return localStorage.getItem(REVIEWER_KEY) ?? ""; } catch { return ""; }
}
export function setReviewer(v: string) {
  try { localStorage.setItem(REVIEWER_KEY, v); } catch { /* private mode */ }
}

/** Thrown with a message a human can act on — never a stack trace. */
export class ApiError extends Error {
  constructor(public status: number, message: string) { super(message); }
}

function assertConfigured() {
  if (!API_BASE) {
    throw new ApiError(0,
      "This build has no API address. VITE_API_BASE_URL was not set when the " +
      "frontend was built — see ui/.env.example.");
  }
}

async function request<T>(path: string, init?: RequestInit): Promise<T> {
  assertConfigured();
  let res: Response;
  try {
    res = await fetch(`${API_BASE}${path}`, init);
  } catch {
    // A network-level failure is indistinguishable from CORS in the browser, so the
    // message has to name both rather than guess.
    throw new ApiError(0,
      `Could not reach the API at ${API_BASE}. It may be starting up (cold start), ` +
      `offline, or not allowing this site's origin.`);
  }
  if (!res.ok) {
    let detail = `${res.status} ${res.statusText}`;
    try {
      const body = await res.json();
      if (body?.detail) detail = String(body.detail);
    } catch { /* non-JSON error body */ }
    throw new ApiError(res.status, detail);
  }
  if (res.status === 204) return undefined as T;
  return (await res.json()) as T;
}

function writeInit(body: unknown): RequestInit {
  return {
    method: "POST",
    headers: {
      "Content-Type": "application/json",
      [PASSCODE_HEADER]: getPasscode(),
    },
    body: JSON.stringify(body),
  };
}

export const api = {
  health: () => request<Health>("/api/health"),
  vocabulary: () => request<Vocabulary>("/api/vocabulary"),
  stats: () => request<Stats>("/api/stats"),
  runs: () => request<Run[]>("/api/runs"),
  evaluation: () => request<Evaluation>("/api/evaluation"),

  records: (params: Record<string, string | number | boolean | undefined>) => {
    const qs = new URLSearchParams();
    for (const [k, v] of Object.entries(params)) {
      if (v !== undefined && v !== "" && v !== null) qs.set(k, String(v));
    }
    return request<Page<RecordRow>>(`/api/records?${qs}`);
  },
  record: (emailId: string) => request<RecordDetail>(`/api/records/${emailId}`),
  sourceText: (emailId: string, role: string) =>
    request<{ lines: string[] }>(`/api/records/${emailId}/text/${role}`),
  sourceUrl: (emailId: string, attachmentId: string) =>
    `${API_BASE}/api/records/${emailId}/source/${attachmentId}`,

  decide: (emailId: string, body: DecisionBody) =>
    request<RecordDetail>(`/api/records/${emailId}/decisions`, writeInit(body)),

  proposals: (status = "pending") =>
    request<Proposal[]>(`/api/proposals?status=${status}`),
  decideProposal: (id: number, decision: "approved" | "rejected",
                   reviewer: string, note = "") =>
    request<unknown>(`/api/proposals/${id}`, writeInit({ decision, reviewer, note })),

};

/* ---- Shapes. These mirror the OpenAPI schema the API publishes; the vocabulary
   strings themselves are FETCHED, never hardcoded. ---- */

export interface Health {
  status: string; database: boolean; records: number; runs?: number;
  writes_enabled: boolean; provider?: string; model?: string;
  learned_labels?: number; detail?: string;
}
export interface Term {
  value: string; label: string; tone?: string; icon?: string;
  help?: string; key?: string;
}
export interface Vocabulary {
  statuses: Term[]; categories: Term[]; review_reasons: Term[];
  verdicts: Term[]; decision_types: Term[];
  fields: { value: string; label: string; type: string }[];
  comparison_category: string;
}
export interface Stats {
  run_id: string | null;
  by_status: Record<string, number>;
  by_category: Record<string, number>;
  decided_by: Record<string, number>;
  ai_share: number; awaiting_documents: number;
  llm_calls: number; cache_hit_rate: number | null;
  active_decisions: number; pending_proposals: number;
  provider?: string; model?: string; started_at?: string;
  submission_sha256?: string; learned_labels_sha256?: string;
  code_version?: string;
}
export interface Run {
  run_id: string; status: string; started_at: string | null;
  record_count: number | null; degraded: boolean;
  llm_provider: string | null; llm_model: string | null;
  code_version: string; submission_sha256: string | null;
  learned_labels_sha256: string | null; decisions_sha256: string | null;
}
export interface RecordRow {
  email_id: string; category: string | null; status: string;
  review_reason: string | null; has_defect: boolean; defect_count: number;
  awaiting_documents: boolean; human_decided: boolean;
  bypassed_state_rule: boolean; decided_by: string | null;
}
export interface Comparison {
  field: string; verdict: string; leaning: string | null;
  si_value: string | null; bl_value: string | null;
  si_evidence: Evidence | null; bl_evidence: Evidence | null;
  strategy: string | null; detail: string | null;
  decided_by_human?: boolean;
}
export interface Evidence {
  file: string | null; locator: string | null; method: string | null;
  label_seen?: string | null; resolved_from?: string; reference_text?: string;
}
export interface Decision {
  decision_id: number; field: string | null; decision_type: string;
  verdict: string | null; corrected_value: string | null;
  corrected_category: string | null; note: string; reviewer: string;
  decided_at: string | null;
}
export interface TryLabelProposal {
  normalised: string;
  label: string;
  proposed_field: string | null;
  value: string;
  context: string;
  doc_ref: string;
  line_no: number;
  role: string;
}

export interface RecordDetail extends RecordRow {
  comparisons: Comparison[];
  events: { seq: number; stage: string; outcome: string; detail: string | null }[];
  decisions: Decision[];
  defect_fields: string[] | null;
  attachments: { attachment_id: number; filename: string;
                 content_type: string | null; detected_type: string | null }[];
  subject?: string | null;
  body?: string | null;
  sender?: string | null;
  received_at?: string | null;
  // Present only on the ad-hoc /api/try response.
  unknown_labels?: string[];
  label_proposals?: TryLabelProposal[];
}
export interface DecisionBody {
  decision_type: string; reviewer: string; field?: string | null;
  verdict?: string; corrected_value?: string; corrected_category?: string;
  note?: string;
}
export interface Proposal {
  proposal_id: number; label_raw: string; label_normalised: string;
  proposed_field: string | null; proposed_by: string; prompt_version: string;
  evidence_doc: string; evidence_line: number | null; evidence_context: string;
  status: string; decided_by: string | null; note: string | null;
}
export interface Evaluation {
  latest: Record<string, number | string> | null;
  providers: Record<string, unknown>;
}
export interface Page<T> {
  total: number; limit: number; offset: number; items: T[];
}
