"use client";

import { useState } from "react";
import Topbar from "@/components/Topbar";
import ToolCard from "@/components/ToolCard";
import ResultRenderer from "@/components/results/ResultRenderer";
import { SkeletonCard, SkeletonTable } from "@/components/Skeleton";
import { useToast } from "@/lib/toast";
import { invokeTool } from "@/lib/api";
import type { ToolInvokeResult } from "@/types";

type ActiveTool = "repo" | "ticket" | "prs" | "spec" | "bug" | "slack_send" | "slack_history";

interface RepoOption { full_name: string; name: string; private: boolean; }

const TOOLS: { key: ActiveTool; icon: string; iconBg: string; color: string; bg: string; title: string; desc: string }[] = [
  { key: "repo",          icon: "bi-github",            iconBg: "#0d6efd", color: "#0d6efd", bg: "rgba(13,110,253,0.1)",  title: "Repo Search",     desc: "Search GitHub repositories by keyword." },
  { key: "ticket",        icon: "bi-ticket-detailed",   iconBg: "#6f42c1", color: "#6f42c1", bg: "rgba(111,66,193,0.1)", title: "Create Ticket",   desc: "Open a GitHub issue with priority." },
  { key: "prs",           icon: "bi-git",               iconBg: "#20c997", color: "#20c997", bg: "rgba(32,201,151,0.1)", title: "PR List",         desc: "List open pull requests for a repo." },
  { key: "spec",          icon: "bi-file-earmark-code", iconBg: "#fd7e14", color: "#fd7e14", bg: "rgba(253,126,20,0.1)", title: "Spec Generator",  desc: "Generate a technical spec from a description." },
  { key: "bug",           icon: "bi-bug",               iconBg: "#dc3545", color: "#dc3545", bg: "rgba(220,53,69,0.1)",  title: "Bug Triage",      desc: "Root-cause analysis on an error message." },
  { key: "slack_send",    icon: "bi-slack",             iconBg: "#4a154b", color: "#4a154b", bg: "rgba(74,21,75,0.1)",   title: "Slack Message",   desc: "Send a message to a Slack channel." },
  { key: "slack_history", icon: "bi-chat-square-text",  iconBg: "#0dcaf0", color: "#0dcaf0", bg: "rgba(13,202,240,0.1)", title: "Channel History", desc: "Retrieve recent messages from a channel." },
];

export default function DevPage() {
  const toast = useToast();
  const [active, setActive] = useState<ActiveTool | null>(null);
  const [invocation, setInvocation] = useState<ToolInvokeResult | null>(null);
  const [loading, setLoading] = useState(false);

  const [repos, setRepos] = useState<RepoOption[]>([]);
  const [reposLoading, setReposLoading] = useState(false);

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

  async function fetchRepos() {
    setReposLoading(true);
    setRepos([]);
    try {
      const res = await invokeTool({ tool: "repo.list", args: {} });
      setRepos((res.result as { repos?: RepoOption[] })?.repos ?? []);
    } catch { /* silently fail */ } finally {
      setReposLoading(false);
    }
  }

  function openTool(tool: ActiveTool) {
    setInvocation(null);
    setLoading(false);
    setActive(tool);
    if ((tool === "ticket" || tool === "prs") && repos.length === 0) {
      setTicketRepo(""); setPrRepo("");
      fetchRepos();
    }
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
    repo: <SkeletonTable rows={4} cols={3} />,
    ticket: <SkeletonCard />,
    prs: <SkeletonTable rows={5} cols={4} />,
    spec: <SkeletonCard />,
    bug: <SkeletonCard />,
    slack_send: <SkeletonCard />,
    slack_history: <SkeletonTable rows={6} cols={2} />,
  };

  const RepoSelect = ({ value, onChange }: { value: string; onChange: (v: string) => void }) =>
    repos.length > 0 ? (
      <select className="form-select" value={value} onChange={(e) => onChange(e.target.value)}>
        <option value="">— select a repository —</option>
        {repos.map((r) => <option key={r.full_name} value={r.full_name}>{r.full_name}{r.private ? " 🔒" : ""}</option>)}
      </select>
    ) : (
      <input className="form-control" value={value} onChange={(e) => onChange(e.target.value)}
        placeholder={reposLoading ? "Loading repositories…" : "owner/repo"} disabled={reposLoading} />
    );

  /* ── Card grid view ── */
  if (!active) {
    return (
      <>
        <Topbar title="Dev Tools" subtitle="GitHub · Slack · LLM" />
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
      <Topbar title="Dev Tools" subtitle="GitHub · Slack · LLM" />
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
              {active === "repo" && (
                <>
                  <div className="mb-3">
                    <label className="form-label small fw-semibold">Search Query</label>
                    <input className="form-control" value={repoQuery} onChange={(e) => setRepoQuery(e.target.value)} placeholder="e.g. fastapi authentication" />
                  </div>
                  <RunButton loading={loading} onClick={() => run("repo.search", { query: repoQuery })} disabled={!repoQuery} />
                </>
              )}
              {active === "ticket" && (
                <>
                  <div className="mb-3">
                    <label className="form-label small fw-semibold d-flex align-items-center gap-2">
                      Repository
                      {reposLoading && <span className="spinner-border spinner-border-sm text-secondary" />}
                    </label>
                    <RepoSelect value={ticketRepo} onChange={setTicketRepo} />
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
                </>
              )}
              {active === "prs" && (
                <>
                  <div className="mb-3">
                    <label className="form-label small fw-semibold d-flex align-items-center gap-2">
                      Repository
                      {reposLoading && <span className="spinner-border spinner-border-sm text-secondary" />}
                    </label>
                    <RepoSelect value={prRepo} onChange={setPrRepo} />
                  </div>
                  <RunButton loading={loading} onClick={() => run("ticket.pr_list", { repo: prRepo })} disabled={!prRepo} />
                </>
              )}
              {active === "spec" && (
                <>
                  <div className="mb-3">
                    <label className="form-label small fw-semibold">Feature Description</label>
                    <textarea className="form-control" rows={5} value={specDesc} onChange={(e) => setSpecDesc(e.target.value)} placeholder="Describe the feature you want a spec for…" />
                  </div>
                  <RunButton loading={loading} onClick={() => run("spec.generate", { feature_description: specDesc })} disabled={!specDesc} />
                </>
              )}
              {active === "bug" && (
                <>
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
                </>
              )}
              {active === "slack_send" && (
                <>
                  <div className="mb-3">
                    <label className="form-label small fw-semibold">Channel / User ID</label>
                    <input className="form-control" value={slackChannel} onChange={(e) => setSlackChannel(e.target.value)} placeholder="#general or U0123ABCDEF" />
                  </div>
                  <div className="mb-3">
                    <label className="form-label small fw-semibold">Message</label>
                    <textarea className="form-control" rows={4} value={slackText} onChange={(e) => setSlackText(e.target.value)} placeholder="Message text…" />
                  </div>
                  <RunButton loading={loading} onClick={() => run("slack.message.send", { channel: slackChannel, text: slackText })} disabled={!slackChannel || !slackText} />
                </>
              )}
              {active === "slack_history" && (
                <>
                  <div className="mb-3">
                    <label className="form-label small fw-semibold">Channel ID</label>
                    <input className="form-control" value={slackChannel} onChange={(e) => setSlackChannel(e.target.value)} placeholder="C0123ABCDEF" />
                  </div>
                  <div className="mb-3">
                    <label className="form-label small fw-semibold">Message Limit</label>
                    <input type="number" className="form-control" value={slackHistLimit} onChange={(e) => setSlackHistLimit(Number(e.target.value))} min={1} max={200} />
                  </div>
                  <RunButton loading={loading} onClick={() => run("slack.channel.history", { channel: slackChannel, limit: slackHistLimit })} disabled={!slackChannel} />
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
