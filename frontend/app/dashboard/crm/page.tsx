"use client";

import { useState } from "react";
import Topbar from "@/components/Topbar";
import ToolCard from "@/components/ToolCard";
import ResultRenderer from "@/components/results/ResultRenderer";
import { SkeletonCard, SkeletonTable } from "@/components/Skeleton";
import { useToast } from "@/lib/toast";
import { invokeTool } from "@/lib/api";
import type { ToolInvokeResult } from "@/types";

type ActiveTool = "contact" | "search" | "note" | "message" | "pipeline" | "email";

const TOOLS: { key: ActiveTool; icon: string; iconBg: string; color: string; bg: string; title: string; desc: string }[] = [
  { key: "contact",  icon: "bi-person-lines-fill", iconBg: "#198754", color: "#198754", bg: "rgba(25,135,84,0.1)",   title: "Contact Lookup",  desc: "Retrieve a Podio contact by ID." },
  { key: "search",   icon: "bi-search",            iconBg: "#20c997", color: "#20c997", bg: "rgba(32,201,151,0.1)",  title: "Lead Search",     desc: "Search Podio leads by keyword." },
  { key: "note",     icon: "bi-journal-plus",       iconBg: "#0dcaf0", color: "#0dcaf0", bg: "rgba(13,202,240,0.1)", title: "Create Note",     desc: "Add a note to a Podio contact." },
  { key: "message",  icon: "bi-chat-dots",          iconBg: "#fd7e14", color: "#fd7e14", bg: "rgba(253,126,20,0.1)", title: "Send Message",    desc: "Send SMS or email via GHL." },
  { key: "pipeline", icon: "bi-diagram-3",          iconBg: "#6f42c1", color: "#6f42c1", bg: "rgba(111,66,193,0.1)", title: "Pipeline Stages", desc: "View all GHL pipeline stages." },
  { key: "email",    icon: "bi-envelope",           iconBg: "#dc3545", color: "#dc3545", bg: "rgba(220,53,69,0.1)",  title: "Send Email",      desc: "Send email via SMTP." },
];

export default function CrmPage() {
  const toast = useToast();
  const [active, setActive] = useState<ActiveTool | null>(null);
  const [invocation, setInvocation] = useState<ToolInvokeResult | null>(null);
  const [loading, setLoading] = useState(false);

  const [contactId, setContactId] = useState("");
  const [searchQuery, setSearchQuery] = useState("");
  const [noteClientId, setNoteClientId] = useState("");
  const [noteText, setNoteText] = useState("");
  const [msgContactId, setMsgContactId] = useState("");
  const [msgText, setMsgText] = useState("");
  const [msgChannel, setMsgChannel] = useState("sms");
  const [emailTo, setEmailTo] = useState("");
  const [emailSubject, setEmailSubject] = useState("");
  const [emailBody, setEmailBody] = useState("");

  function openTool(tool: ActiveTool) {
    setInvocation(null);
    setLoading(false);
    setActive(tool);
  }

  function goBack() {
    setActive(null);
    setInvocation(null);
    setLoading(false);
  }

  async function run(tool: string, args: Record<string, unknown>) {
    setLoading(true);
    setInvocation(null);
    try {
      const res = await invokeTool({ tool, args });
      setInvocation(res);
      if (res.success) toast.success("Tool completed successfully");
      else toast.error(res.error ?? "Tool returned an error");
    } catch (err: unknown) {
      toast.error(err instanceof Error ? err.message : "Tool call failed");
    } finally {
      setLoading(false);
    }
  }

  const activeTool = TOOLS.find((t) => t.key === active);

  const skeletonMap: Record<ActiveTool, React.ReactNode> = {
    contact: <SkeletonCard />,
    search: <SkeletonTable rows={5} cols={4} />,
    note: <SkeletonCard />,
    message: <SkeletonCard />,
    pipeline: <SkeletonCard />,
    email: <SkeletonCard />,
  };

  /* ── Card grid view ── */
  if (!active) {
    return (
      <>
        <Topbar title="CRM Tools" subtitle="Podio · GoHighLevel · Email" />
        <div className="page-body fade-in">
          <div className="row g-3">
            {TOOLS.map((t) => (
              <div key={t.key} className="col-sm-6 col-lg-4">
                <ToolCard icon={t.icon} iconBg={t.iconBg} title={t.title} description={t.desc} onClick={() => openTool(t.key)} />
              </div>
            ))}
          </div>
        </div>
      </>
    );
  }

  /* ── Split panel view ── */
  return (
    <>
      <Topbar title="CRM Tools" subtitle="Podio · GoHighLevel · Email" />
      <div className="page-body fade-in">
        <div className="tool-split">

          {/* Left nav */}
          <nav className="tool-nav">
            <button className="tool-nav-back" onClick={goBack}>
              <i className="bi bi-arrow-left" />
              All Tools
            </button>
            {TOOLS.map((t) => (
              <button key={t.key} className={`tool-nav-item${active === t.key ? " active" : ""}`} onClick={() => openTool(t.key)}>
                <div className="tool-nav-icon" style={{ background: active === t.key ? "rgba(99,102,241,0.12)" : t.bg }}>
                  <i className={`bi ${t.icon}`} style={{ color: active === t.key ? "var(--primary)" : t.color }} />
                </div>
                <div style={{ flex: 1, minWidth: 0 }}>
                  <div style={{ fontSize: "0.845rem", fontWeight: 600, color: active === t.key ? "var(--primary)" : "var(--text)", lineHeight: 1.2 }}>{t.title}</div>
                  <div style={{ fontSize: "0.71rem", color: "var(--text-muted)", marginTop: "0.15rem", lineHeight: 1.3 }}>{t.desc}</div>
                </div>
                {active === t.key && <i className="bi bi-chevron-right" style={{ fontSize: "0.65rem", color: "var(--primary)", flexShrink: 0 }} />}
              </button>
            ))}
          </nav>

          {/* Right detail */}
          <div className="tool-detail">
            <div className="tool-detail-header">
              <div className="tool-detail-header-icon" style={{ background: activeTool!.bg }}>
                <i className={`bi ${activeTool!.icon}`} style={{ color: activeTool!.color }} />
              </div>
              <div>
                <div className="tool-detail-title">{activeTool!.title}</div>
                <div className="tool-detail-subtitle">{activeTool!.desc}</div>
              </div>
            </div>

            <div className="tool-detail-body">
              {active === "contact" && (
                <>
                  <div className="mb-3">
                    <label className="form-label small fw-semibold">Podio Item ID</label>
                    <input className="form-control" value={contactId} onChange={(e) => setContactId(e.target.value)} placeholder="e.g. 123456789" />
                  </div>
                  <RunButton loading={loading} onClick={() => run("crm.contact.get", { contact_id: contactId })} disabled={!contactId} />
                </>
              )}
              {active === "search" && (
                <>
                  <div className="mb-3">
                    <label className="form-label small fw-semibold">Search Query</label>
                    <input className="form-control" value={searchQuery} onChange={(e) => setSearchQuery(e.target.value)} placeholder="Company name, email…" />
                  </div>
                  <RunButton loading={loading} onClick={() => run("crm.lead.search", { query: searchQuery, limit: 10 })} disabled={!searchQuery} />
                </>
              )}
              {active === "note" && (
                <>
                  <div className="mb-3">
                    <label className="form-label small fw-semibold">Client ID</label>
                    <input className="form-control" value={noteClientId} onChange={(e) => setNoteClientId(e.target.value)} placeholder="Podio item ID" />
                  </div>
                  <div className="mb-3">
                    <label className="form-label small fw-semibold">Note</label>
                    <textarea className="form-control" rows={4} value={noteText} onChange={(e) => setNoteText(e.target.value)} placeholder="Write your note…" />
                  </div>
                  <RunButton loading={loading} onClick={() => run("crm.note.create", { client_id: noteClientId, note: noteText })} disabled={!noteClientId || !noteText} />
                </>
              )}
              {active === "message" && (
                <>
                  <div className="mb-3">
                    <label className="form-label small fw-semibold">GHL Contact ID</label>
                    <input className="form-control" value={msgContactId} onChange={(e) => setMsgContactId(e.target.value)} placeholder="GHL contact ID" />
                  </div>
                  <div className="mb-3">
                    <label className="form-label small fw-semibold">Channel</label>
                    <select className="form-select" value={msgChannel} onChange={(e) => setMsgChannel(e.target.value)}>
                      <option value="sms">SMS</option>
                      <option value="email">Email</option>
                    </select>
                  </div>
                  <div className="mb-3">
                    <label className="form-label small fw-semibold">Message</label>
                    <textarea className="form-control" rows={3} value={msgText} onChange={(e) => setMsgText(e.target.value)} placeholder="Message content…" />
                  </div>
                  <RunButton loading={loading} onClick={() => run("crm.message.send", { contact_id: msgContactId, message: msgText, channel: msgChannel })} disabled={!msgContactId || !msgText} />
                </>
              )}
              {active === "pipeline" && (
                <>
                  <p className="text-muted small mb-3">Fetches all pipeline stages from GoHighLevel.</p>
                  <RunButton loading={loading} onClick={() => run("crm.pipeline.stages", {})} />
                </>
              )}
              {active === "email" && (
                <>
                  <div className="mb-3">
                    <label className="form-label small fw-semibold">To</label>
                    <input type="email" className="form-control" value={emailTo} onChange={(e) => setEmailTo(e.target.value)} placeholder="recipient@example.com" />
                  </div>
                  <div className="mb-3">
                    <label className="form-label small fw-semibold">Subject</label>
                    <input className="form-control" value={emailSubject} onChange={(e) => setEmailSubject(e.target.value)} placeholder="Email subject" />
                  </div>
                  <div className="mb-3">
                    <label className="form-label small fw-semibold">Body</label>
                    <textarea className="form-control" rows={5} value={emailBody} onChange={(e) => setEmailBody(e.target.value)} placeholder="Email body…" />
                  </div>
                  <RunButton loading={loading} onClick={() => run("crm.email.send", { to: emailTo, subject: emailSubject, body: emailBody })} disabled={!emailTo || !emailSubject || !emailBody} />
                </>
              )}
              <ResultArea loading={loading} invocation={invocation} skeleton={active ? skeletonMap[active] : null} />
            </div>
          </div>

        </div>
      </div>
    </>
  );
}

function RunButton({ loading, onClick, disabled }: { loading: boolean; onClick: () => void; disabled?: boolean }) {
  return (
    <button className="btn btn-primary mb-3" style={{ background: "var(--primary)", border: "none" }} onClick={onClick} disabled={loading || disabled}>
      {loading ? <><span className="spinner-border spinner-border-sm me-2" />Running…</> : <><i className="bi bi-play-fill me-2" />Run Tool</>}
    </button>
  );
}

function ResultArea({ loading, invocation, skeleton }: { loading: boolean; invocation: ToolInvokeResult | null; skeleton: React.ReactNode }) {
  if (loading) return <div className="mt-3">{skeleton}</div>;
  if (!invocation) return null;
  return (
    <div className="mt-3">
      <div className="small fw-semibold text-muted mb-2">Result</div>
      <ResultRenderer invocation={invocation} />
    </div>
  );
}
