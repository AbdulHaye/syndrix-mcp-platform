"use client";

import { useState } from "react";
import Topbar from "@/components/Topbar";
import ToolCard from "@/components/ToolCard";
import EmptyState from "@/components/EmptyState";
import ResultRenderer from "@/components/results/ResultRenderer";
import { SkeletonCard, SkeletonTable } from "@/components/Skeleton";
import { useToast } from "@/lib/toast";
import { invokeTool } from "@/lib/api";
import type { ToolInvokeResult } from "@/types";

type ActiveTool = "contact" | "search" | "note" | "message" | "pipeline" | "email" | null;

export default function CrmPage() {
  const toast = useToast();
  const [active, setActive] = useState<ActiveTool>(null);
  const [invocation, setInvocation] = useState<ToolInvokeResult | null>(null);
  const [loading, setLoading] = useState(false);

  // Form fields
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

  async function run(tool: string, args: Record<string, unknown>) {
    setLoading(true);
    setInvocation(null);
    try {
      const res = await invokeTool({ tool, args });
      setInvocation(res);
      if (res.success) {
        toast.success("Tool completed successfully");
      } else {
        toast.error(res.error ?? "Tool returned an error");
      }
    } catch (err: unknown) {
      const msg = err instanceof Error ? err.message : "Tool call failed";
      toast.error(msg);
    } finally {
      setLoading(false);
    }
  }

  const TOOLS = [
    { key: "contact" as ActiveTool, icon: "bi-person-lines-fill", bg: "#198754", title: "Contact Lookup", desc: "Retrieve a Podio contact by ID." },
    { key: "search" as ActiveTool, icon: "bi-search", bg: "#20c997", title: "Lead Search", desc: "Search Podio leads by keyword." },
    { key: "note" as ActiveTool, icon: "bi-journal-plus", bg: "#0dcaf0", title: "Create Note", desc: "Add a note to a Podio contact." },
    { key: "message" as ActiveTool, icon: "bi-chat-dots", bg: "#fd7e14", title: "Send Message", desc: "Send SMS or email via GHL." },
    { key: "pipeline" as ActiveTool, icon: "bi-diagram-3", bg: "#6f42c1", title: "Pipeline Stages", desc: "View all GHL pipeline stages." },
    { key: "email" as ActiveTool, icon: "bi-envelope", bg: "#dc3545", title: "Send Email", desc: "Send email via SMTP." },
  ];

  const skeletonByTool: Record<string, React.ReactNode> = {
    contact: <SkeletonCard />,
    search: <SkeletonTable rows={5} cols={4} />,
    note: <SkeletonCard />,
    message: <SkeletonCard />,
    pipeline: <SkeletonCard />,
    email: <SkeletonCard />,
  };

  return (
    <>
      <Topbar title="CRM Tools" subtitle="Podio · GoHighLevel · Email" />
      <div className="page-body">
        <div className="row g-3 mb-4">
          {TOOLS.map((t) => (
            <div key={String(t.key)} className="col-sm-6 col-lg-4">
              <ToolCard
                icon={t.icon}
                iconBg={t.bg}
                title={t.title}
                description={t.desc}
                onClick={() => openTool(t.key)}
                badge={active === t.key ? "active" : undefined}
                badgeColor="primary"
              />
            </div>
          ))}
        </div>

        {active === "contact" && (
          <Panel title="Contact Lookup" icon="bi-person-lines-fill" onClose={() => setActive(null)}>
            <div className="mb-3">
              <label className="form-label small fw-semibold">Podio Item ID</label>
              <input className="form-control" value={contactId} onChange={(e) => setContactId(e.target.value)} placeholder="e.g. 123456789" />
            </div>
            <RunButton loading={loading} onClick={() => run("crm.contact.get", { contact_id: contactId })} disabled={!contactId} />
            <ResultArea loading={loading} invocation={invocation} skeleton={skeletonByTool.contact} />
          </Panel>
        )}

        {active === "search" && (
          <Panel title="Lead Search" icon="bi-search" onClose={() => setActive(null)}>
            <div className="mb-3">
              <label className="form-label small fw-semibold">Search Query</label>
              <input className="form-control" value={searchQuery} onChange={(e) => setSearchQuery(e.target.value)} placeholder="Company name, email…" />
            </div>
            <RunButton loading={loading} onClick={() => run("crm.lead.search", { query: searchQuery, limit: 10 })} disabled={!searchQuery} />
            <ResultArea loading={loading} invocation={invocation} skeleton={skeletonByTool.search} />
          </Panel>
        )}

        {active === "note" && (
          <Panel title="Create Note" icon="bi-journal-plus" onClose={() => setActive(null)}>
            <div className="mb-3">
              <label className="form-label small fw-semibold">Client ID</label>
              <input className="form-control" value={noteClientId} onChange={(e) => setNoteClientId(e.target.value)} placeholder="Podio item ID" />
            </div>
            <div className="mb-3">
              <label className="form-label small fw-semibold">Note</label>
              <textarea className="form-control" rows={4} value={noteText} onChange={(e) => setNoteText(e.target.value)} placeholder="Write your note…" />
            </div>
            <RunButton loading={loading} onClick={() => run("crm.note.create", { client_id: noteClientId, note: noteText })} disabled={!noteClientId || !noteText} />
            <ResultArea loading={loading} invocation={invocation} skeleton={skeletonByTool.note} />
          </Panel>
        )}

        {active === "message" && (
          <Panel title="Send Message" icon="bi-chat-dots" onClose={() => setActive(null)}>
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
            <ResultArea loading={loading} invocation={invocation} skeleton={skeletonByTool.message} />
          </Panel>
        )}

        {active === "pipeline" && (
          <Panel title="Pipeline Stages" icon="bi-diagram-3" onClose={() => setActive(null)}>
            <p className="text-muted small">Fetches all pipeline stages from GoHighLevel.</p>
            <RunButton loading={loading} onClick={() => run("crm.pipeline.stages", {})} />
            <ResultArea loading={loading} invocation={invocation} skeleton={skeletonByTool.pipeline} />
          </Panel>
        )}

        {active === "email" && (
          <Panel title="Send Email" icon="bi-envelope" onClose={() => setActive(null)}>
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
            <ResultArea loading={loading} invocation={invocation} skeleton={skeletonByTool.email} />
          </Panel>
        )}

        {!active && (
          <div className="card border">
            <EmptyState icon="bi-cursor" title="Select a tool above to get started" description="Each card opens an input panel." />
          </div>
        )}
      </div>
    </>
  );
}

/* ── Shared sub-components ──────────────────────────────────────────────── */

function Panel({ title, icon, onClose, children }: { title: string; icon: string; onClose: () => void; children: React.ReactNode }) {
  return (
    <div className="card border p-4">
      <div className="d-flex align-items-center justify-content-between mb-3">
        <h6 className="fw-bold mb-0 d-flex align-items-center gap-2">
          <i className={`bi ${icon} text-primary`} />
          {title}
        </h6>
        <button className="btn btn-sm btn-outline-secondary" onClick={onClose}>
          <i className="bi bi-x" />
        </button>
      </div>
      {children}
    </div>
  );
}

function RunButton({ loading, onClick, disabled }: { loading: boolean; onClick: () => void; disabled?: boolean }) {
  return (
    <button className="btn btn-primary mb-3" onClick={onClick} disabled={loading || disabled}>
      {loading
        ? <><span className="spinner-border spinner-border-sm me-2" />Running…</>
        : <><i className="bi bi-play-fill me-2" />Run Tool</>}
    </button>
  );
}

function ResultArea({ loading, invocation, skeleton }: { loading: boolean; invocation: ToolInvokeResult | null; skeleton: React.ReactNode }) {
  if (loading) return <div className="mt-2">{skeleton}</div>;
  if (!invocation) return null;
  return (
    <div className="mt-2">
      <div className="small fw-semibold text-muted mb-2">Result</div>
      <ResultRenderer invocation={invocation} />
    </div>
  );
}
