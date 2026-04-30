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

type ActiveTool = "repo" | "ticket" | "prs" | "spec" | "bug" | "slack_send" | "slack_history" | null;

export default function DevPage() {
  const toast = useToast();
  const [active, setActive] = useState<ActiveTool>(null);
  const [invocation, setInvocation] = useState<ToolInvokeResult | null>(null);
  const [loading, setLoading] = useState(false);

  const [repoQuery, setRepoQuery] = useState("");
  const [ticketRepo, setTicketRepo] = useState("");
  const [ticketTitle, setTicketTitle] = useState("");
  const [ticketDesc, setTicketDesc] = useState("");
  const [ticketPriority, setTicketPriority] = useState("medium");
  const [prRepo, setPrRepo] = useState("");
  const [specDesc, setSpecDesc] = useState("");
  const [bugMsg, setBugMsg] = useState("");
  const [bugCtx, setBugCtx] = useState("");
  const [slackChannel, setSlackChannel] = useState("");
  const [slackText, setSlackText] = useState("");
  const [slackHistLimit, setSlackHistLimit] = useState(50);

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
    { key: "repo" as ActiveTool, icon: "bi-github", bg: "#0d6efd", title: "Repo Search", desc: "Search GitHub repositories by keyword." },
    { key: "ticket" as ActiveTool, icon: "bi-ticket-detailed", bg: "#6f42c1", title: "Create Ticket", desc: "Open a GitHub issue with priority." },
    { key: "prs" as ActiveTool, icon: "bi-git", bg: "#20c997", title: "PR List", desc: "List open pull requests for a repo." },
    { key: "spec" as ActiveTool, icon: "bi-file-earmark-code", bg: "#fd7e14", title: "Spec Generator", desc: "Generate a technical spec from a feature description." },
    { key: "bug" as ActiveTool, icon: "bi-bug", bg: "#dc3545", title: "Bug Triage", desc: "Root-cause analysis on an error message." },
    { key: "slack_send" as ActiveTool, icon: "bi-slack", bg: "#4a154b", title: "Slack Message", desc: "Send a message to a Slack channel." },
    { key: "slack_history" as ActiveTool, icon: "bi-chat-square-text", bg: "#0dcaf0", title: "Channel History", desc: "Retrieve recent messages from a Slack channel." },
  ];

  const skeletonByTool: Record<string, React.ReactNode> = {
    repo: <SkeletonTable rows={4} cols={3} />,
    ticket: <SkeletonCard />,
    prs: <SkeletonTable rows={5} cols={4} />,
    spec: <SkeletonCard />,
    bug: <SkeletonCard />,
    slack_send: <SkeletonCard />,
    slack_history: <SkeletonTable rows={6} cols={2} />,
  };

  return (
    <>
      <Topbar title="Dev Tools" subtitle="GitHub · Slack · LLM" />
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

        {active === "repo" && (
          <Panel title="Repo Search" icon="bi-github" onClose={() => setActive(null)}>
            <div className="mb-3">
              <label className="form-label small fw-semibold">Search Query</label>
              <input className="form-control" value={repoQuery} onChange={(e) => setRepoQuery(e.target.value)} placeholder="e.g. fastapi authentication" />
            </div>
            <RunButton loading={loading} onClick={() => run("repo.search", { query: repoQuery })} disabled={!repoQuery} />
            <ResultArea loading={loading} invocation={invocation} skeleton={skeletonByTool.repo} />
          </Panel>
        )}

        {active === "ticket" && (
          <Panel title="Create Ticket" icon="bi-ticket-detailed" onClose={() => setActive(null)}>
            <div className="mb-3">
              <label className="form-label small fw-semibold">Repository</label>
              <input className="form-control" value={ticketRepo} onChange={(e) => setTicketRepo(e.target.value)} placeholder="owner/repo or just repo" />
            </div>
            <div className="mb-3">
              <label className="form-label small fw-semibold">Title</label>
              <input className="form-control" value={ticketTitle} onChange={(e) => setTicketTitle(e.target.value)} placeholder="Issue title" />
            </div>
            <div className="mb-3">
              <label className="form-label small fw-semibold">Description</label>
              <textarea className="form-control" rows={4} value={ticketDesc} onChange={(e) => setTicketDesc(e.target.value)} placeholder="Describe the issue…" />
            </div>
            <div className="mb-3">
              <label className="form-label small fw-semibold">Priority</label>
              <select className="form-select" value={ticketPriority} onChange={(e) => setTicketPriority(e.target.value)}>
                {["low", "medium", "high", "critical"].map((p) => (
                  <option key={p} value={p}>{p.charAt(0).toUpperCase() + p.slice(1)}</option>
                ))}
              </select>
            </div>
            <RunButton loading={loading} onClick={() => run("ticket.create", { repo: ticketRepo, title: ticketTitle, description: ticketDesc, priority: ticketPriority })} disabled={!ticketRepo || !ticketTitle || !ticketDesc} />
            <ResultArea loading={loading} invocation={invocation} skeleton={skeletonByTool.ticket} />
          </Panel>
        )}

        {active === "prs" && (
          <Panel title="PR List" icon="bi-git" onClose={() => setActive(null)}>
            <div className="mb-3">
              <label className="form-label small fw-semibold">Repository</label>
              <input className="form-control" value={prRepo} onChange={(e) => setPrRepo(e.target.value)} placeholder="owner/repo or just repo" />
            </div>
            <RunButton loading={loading} onClick={() => run("ticket.pr_list", { repo: prRepo })} disabled={!prRepo} />
            <ResultArea loading={loading} invocation={invocation} skeleton={skeletonByTool.prs} />
          </Panel>
        )}

        {active === "spec" && (
          <Panel title="Spec Generator" icon="bi-file-earmark-code" onClose={() => setActive(null)}>
            <div className="mb-3">
              <label className="form-label small fw-semibold">Feature Description</label>
              <textarea className="form-control" rows={5} value={specDesc} onChange={(e) => setSpecDesc(e.target.value)} placeholder="Describe the feature you want a spec for…" />
            </div>
            <RunButton loading={loading} onClick={() => run("spec.generate", { feature_description: specDesc })} disabled={!specDesc} />
            <ResultArea loading={loading} invocation={invocation} skeleton={skeletonByTool.spec} />
          </Panel>
        )}

        {active === "bug" && (
          <Panel title="Bug Triage" icon="bi-bug" onClose={() => setActive(null)}>
            <div className="mb-3">
              <label className="form-label small fw-semibold">Error Message</label>
              <textarea className="form-control" rows={4} value={bugMsg} onChange={(e) => setBugMsg(e.target.value)} placeholder="Paste the error or stack trace…" />
            </div>
            <div className="mb-3">
              <label className="form-label small fw-semibold">
                Additional Context <span className="text-muted fw-normal">(optional)</span>
              </label>
              <textarea className="form-control" rows={3} value={bugCtx} onChange={(e) => setBugCtx(e.target.value)} placeholder="Relevant code, env info, what you tried…" />
            </div>
            <RunButton loading={loading} onClick={() => run("bug.triage", { error_message: bugMsg, context: bugCtx })} disabled={!bugMsg} />
            <ResultArea loading={loading} invocation={invocation} skeleton={skeletonByTool.bug} />
          </Panel>
        )}

        {active === "slack_send" && (
          <Panel title="Slack Message" icon="bi-slack" onClose={() => setActive(null)}>
            <div className="mb-3">
              <label className="form-label small fw-semibold">Channel / User ID</label>
              <input className="form-control" value={slackChannel} onChange={(e) => setSlackChannel(e.target.value)} placeholder="#general or U0123ABCDEF" />
            </div>
            <div className="mb-3">
              <label className="form-label small fw-semibold">Message</label>
              <textarea className="form-control" rows={4} value={slackText} onChange={(e) => setSlackText(e.target.value)} placeholder="Message text…" />
            </div>
            <RunButton loading={loading} onClick={() => run("slack.message.send", { channel: slackChannel, text: slackText })} disabled={!slackChannel || !slackText} />
            <ResultArea loading={loading} invocation={invocation} skeleton={skeletonByTool.slack_send} />
          </Panel>
        )}

        {active === "slack_history" && (
          <Panel title="Channel History" icon="bi-chat-square-text" onClose={() => setActive(null)}>
            <div className="mb-3">
              <label className="form-label small fw-semibold">Channel ID</label>
              <input className="form-control" value={slackChannel} onChange={(e) => setSlackChannel(e.target.value)} placeholder="C0123ABCDEF" />
            </div>
            <div className="mb-3">
              <label className="form-label small fw-semibold">Message Limit</label>
              <input type="number" className="form-control" value={slackHistLimit} onChange={(e) => setSlackHistLimit(Number(e.target.value))} min={1} max={200} />
            </div>
            <RunButton loading={loading} onClick={() => run("slack.channel.history", { channel: slackChannel, limit: slackHistLimit })} disabled={!slackChannel} />
            <ResultArea loading={loading} invocation={invocation} skeleton={skeletonByTool.slack_history} />
          </Panel>
        )}

        {!active && (
          <div className="card border">
            <EmptyState icon="bi-cursor" title="Select a tool above to get started" />
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
