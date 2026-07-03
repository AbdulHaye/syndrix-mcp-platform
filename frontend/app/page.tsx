import { cookies } from "next/headers";
import Link from "next/link";
import "./landing.css";

export const dynamic = "force-dynamic";

function SyndrixLogo({ size = 40 }: { size?: number }) {
  return (
    <svg width={size} height={size} viewBox="0 0 40 40" fill="none" xmlns="http://www.w3.org/2000/svg">
      <rect width="40" height="40" rx="11" fill="#6366f1" />
      <circle cx="20" cy="13" r="3.5" fill="white" />
      <circle cx="12" cy="27" r="2.8" fill="rgba(255,255,255,0.85)" />
      <circle cx="28" cy="27" r="2.8" fill="rgba(255,255,255,0.85)" />
      <line x1="20" y1="13" x2="12" y2="27" stroke="rgba(255,255,255,0.5)" strokeWidth="1.8" />
      <line x1="20" y1="13" x2="28" y2="27" stroke="rgba(255,255,255,0.5)" strokeWidth="1.8" />
      <line x1="12" y1="27" x2="28" y2="27" stroke="rgba(255,255,255,0.5)" strokeWidth="1.8" />
    </svg>
  );
}

const CAPABILITIES = [
  {
    team: "BD & Sales",
    icon: "bi-people-fill",
    color: "#10b981",
    bg: "rgba(16,185,129,0.1)",
    border: "rgba(16,185,129,0.2)",
    description: "Accelerate your sales pipeline with AI-powered CRM tools, contact enrichment, and automated outreach.",
    tools: ["Contact Lookup", "Lead Search", "Pipeline Stages", "Send Message", "Email Campaigns"],
  },
  {
    team: "Software Dev",
    icon: "bi-code-slash",
    color: "#6366f1",
    bg: "rgba(99,102,241,0.1)",
    border: "rgba(99,102,241,0.2)",
    description: "Ship faster with AI-assisted spec generation, automated bug triage, and deep GitHub integration.",
    tools: ["Repo Search", "Create Ticket", "PR List", "Spec Generator", "Bug Triage"],
  },
  {
    team: "Management",
    icon: "bi-bar-chart-fill",
    color: "#f59e0b",
    bg: "rgba(245,158,11,0.1)",
    border: "rgba(245,158,11,0.2)",
    description: "Stay on top of team performance with AI-generated daily reports and real-time client health scores.",
    tools: ["Daily Report", "Client Health Score", "Team Activity", "KPI Dashboard"],
  },
];

const HOW_IT_WORKS = [
  {
    step: "01",
    title: "Authenticate",
    desc: "Your team signs in with a secure bearer token. Role-based access ensures each member sees only what they need.",
    icon: "bi-shield-lock-fill",
    color: "#6366f1",
  },
  {
    step: "02",
    title: "Invoke Tools",
    desc: "Select any tool from your role-specific panel. Syndrix routes the request through the MCP server to the right adapter.",
    icon: "bi-lightning-fill",
    color: "#10b981",
  },
  {
    step: "03",
    title: "Get Results",
    desc: "AI-enriched responses arrive in seconds — from live CRM data and GitHub repos to locally generated specs.",
    icon: "bi-stars",
    color: "#f59e0b",
  },
];


const FEATURES = [
  { icon: "bi-shield-check", title: "Role-Based Access",   desc: "BD, Dev, Mgmt, and Admin roles. Every tool call is RBAC-enforced at the server level." },
  { icon: "bi-search",        title: "Semantic Search",     desc: "pgvector-powered RAG lets your team search internal documents by meaning, not just keywords." },
  { icon: "bi-journal-text",  title: "Full Audit Trail",    desc: "Every tool invocation is logged with team, latency, and outcome — full observability built in." },
  { icon: "bi-arrow-repeat",  title: "Background Jobs",     desc: "Celery workers sync CRM contacts, rebuild vector indices, and process documents asynchronously." },
  { icon: "bi-plug-fill",     title: "5 Live Adapters",     desc: "Podio, GoHighLevel, Slack, GitHub, and SMTP — all wired with real HTTP calls, not mocks." },
  { icon: "bi-cpu",           title: "Local LLM",           desc: "All AI inference runs on your machine via Ollama. Your data never leaves your infrastructure." },
];

export default function HomePage() {
  const token = cookies().get("mcp_token")?.value;
  const isLoggedIn = !!token;

  return (
    <>
      {/* ── Navbar ── */}
      <nav className="lp-nav">
        <a href="#" className="lp-nav-brand">
          <SyndrixLogo size={34} />
          <span className="lp-nav-brand-name">Syndrix</span>
        </a>
        <ul className="lp-nav-links">
          <li><a href="#capabilities">Capabilities</a></li>
          <li><a href="#features">Features</a></li>
          <li><a href="#how-it-works">How It Works</a></li>
        </ul>
        <div className="lp-nav-actions">
          {isLoggedIn ? (
            <Link href="/dashboard" className="btn-primary-lp">
              <i className="bi bi-grid-fill" />
              Go to Dashboard
            </Link>
          ) : (
            <>
              <Link href="/login" className="btn-ghost">
                <i className="bi bi-box-arrow-in-right" />
                Sign In
              </Link>
              <Link href="/login" className="btn-primary-lp">
                Get Started
                <i className="bi bi-arrow-right" />
              </Link>
            </>
          )}
        </div>
      </nav>

      {/* ── Hero ── */}
      <section className="lp-hero">
        <div className="lp-hero-bg" />
        <div className="lp-hero-grid" />

        <div className="lp-hero-content">
          <div className="lp-badge">
            <span className="lp-badge-dot" />
            Internal AI Capability Hub
          </div>

          <h1 className="lp-hero-h1">
            One AI Hub.<br />
            <span className="gradient-text">Three Teams. Unlimited Speed.</span>
          </h1>

          <p className="lp-hero-sub">
            Syndrix is a shared MCP server that gives your BD, Engineering, and Management
            teams AI-powered tools — CRM enrichment, spec generation, semantic search —
            all running locally on your infrastructure.
          </p>

          <div className="lp-cta-row">
            <Link href={isLoggedIn ? "/dashboard" : "/login"} className="btn-hero-primary">
              <i className={`bi ${isLoggedIn ? "bi-grid-fill" : "bi-rocket-takeoff-fill"}`} />
              {isLoggedIn ? "Go to Dashboard" : "Launch Platform"}
            </Link>
            <a href="#capabilities" className="btn-hero-secondary">
              <i className="bi bi-play-circle" />
              See Capabilities
            </a>
          </div>

          {/* Terminal preview */}
          <div className="lp-terminal" style={{ margin: "4rem auto 0" }}>
            <div className="lp-terminal-bar">
              <div className="term-dot red" />
              <div className="term-dot amber" />
              <div className="term-dot green" />
              <span className="term-title">syndrix — mcp tool invocation</span>
            </div>
            <div className="lp-terminal-body">
              <div><span className="tg">POST</span> <span className="ts">/tools/invoke</span></div>
              <div><span className="tg">Authorization:</span> <span className="tv">Bearer dev-secret-token-456</span></div>
              <div style={{ marginBottom: "0.75rem" }} />
              <div><span className="tc">{"{"}</span></div>
              <div>&nbsp;&nbsp;<span className="tk">"tool"</span><span className="tc">:</span> <span className="ts">"spec.generate"</span><span className="tc">,</span></div>
              <div>&nbsp;&nbsp;<span className="tk">"args"</span><span className="tc">:</span> <span className="tc">{"{"}</span></div>
              <div>&nbsp;&nbsp;&nbsp;&nbsp;<span className="tk">"feature_description"</span><span className="tc">:</span> <span className="ts">"User auth with JWT + refresh token rotation"</span></div>
              <div>&nbsp;&nbsp;<span className="tc">{"}"}</span></div>
              <div><span className="tc">{"}"}</span></div>
              <div style={{ marginTop: "0.75rem" }}><span className="tg">← 200 OK · 1.2s</span></div>
              <div><span className="tc">{"{"}</span></div>
              <div>&nbsp;&nbsp;<span className="tk">"success"</span><span className="tc">:</span> <span className="tn">true</span><span className="tc">,</span></div>
              <div>&nbsp;&nbsp;<span className="tk">"specification"</span><span className="tc">:</span> <span className="ts">"## Overview\nJWT-based auth system with..."</span></div>
              <div><span className="tc">{"}"}</span></div>
            </div>
          </div>
        </div>
      </section>

      {/* ── Stats strip ── */}
      <div className="lp-stats">
        <div className="lp-stats-inner">
          {[
            { num: "3",   desc: "Teams Supported" },
            { num: "15+", desc: "AI-Powered Tools" },
            { num: "5",   desc: "Live Integrations" },
            { num: "100%", desc: "Local Inference" },
          ].map((s) => (
            <div key={s.desc}>
              <div className="stat-num">{s.num}</div>
              <div className="stat-desc">{s.desc}</div>
            </div>
          ))}
        </div>
      </div>

      {/* ── Capabilities ── */}
      <section className="lp-section light" id="capabilities">
        <div className="section-inner">
          <div style={{ textAlign: "center", marginBottom: "3.5rem" }}>
            <span className="section-eyebrow">Capabilities</span>
            <h2 className="section-h2 dark-text">Built for every team in your org</h2>
            <p className="section-sub" style={{ margin: "0 auto", color: "#64748b" }}>
              Each team gets role-specific AI tools tailored to their workflow — BD closes faster,
              Dev ships smarter, Management sees clearer.
            </p>
          </div>
          <div className="cap-grid">
            {CAPABILITIES.map((cap) => (
              <div key={cap.team} className="cap-card">
                <div className="cap-icon" style={{ background: cap.bg }}>
                  <i className={`bi ${cap.icon}`} style={{ color: cap.color }} />
                </div>
                <div className="cap-team" style={{ color: cap.color }}>{cap.team}</div>
                <div className="cap-title">{cap.team} Tools</div>
                <p className="cap-desc">{cap.description}</p>
                <div className="cap-tools">
                  {cap.tools.map((t) => (
                    <span
                      key={t}
                      className="cap-tool-badge"
                      style={{ color: cap.color, borderColor: cap.border, background: cap.bg }}
                    >
                      {t}
                    </span>
                  ))}
                </div>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ── Features ── */}
      <section className="lp-section dark" id="features">
        <div className="section-inner">
          <div style={{ marginBottom: "3rem" }}>
            <span className="section-eyebrow">Platform Features</span>
            <h2 className="section-h2 light-text">Enterprise-grade, zero cloud dependency</h2>
            <p className="section-sub light-sub">
              Every component is designed for security, observability, and performance — running entirely on your own infrastructure.
            </p>
          </div>
          <div className="features-grid">
            {FEATURES.map((f) => (
              <div key={f.title} className="feature-card">
                <div className="feature-icon-wrap">
                  <i className={`bi ${f.icon}`} />
                </div>
                <div className="feature-title">{f.title}</div>
                <p className="feature-desc">{f.desc}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ── How it works ── */}
      <section className="lp-section light" id="how-it-works">
        <div className="section-inner">
          <div style={{ textAlign: "center", marginBottom: "3.5rem" }}>
            <span className="section-eyebrow">How It Works</span>
            <h2 className="section-h2 dark-text">Up and running in minutes</h2>
            <p className="section-sub" style={{ margin: "0 auto", color: "#64748b" }}>
              Syndrix sits between your team and your tools — a single authenticated MCP server that routes every request to the right adapter.
            </p>
          </div>
          <div className="how-grid">
            {HOW_IT_WORKS.map((step) => (
              <div key={step.step} className="how-step">
                <div className="how-step-num">
                  <i className={`bi ${step.icon}`} style={{ color: "white", fontSize: "1.2rem" }} />
                </div>
                <div style={{ fontSize: "0.65rem", fontWeight: 700, letterSpacing: "0.1em", textTransform: "uppercase", color: step.color, marginBottom: "0.4rem" }}>
                  Step {step.step}
                </div>
                <div className="how-step-title" style={{ color: "#0f172a" }}>{step.title}</div>
                <p className="how-step-desc">{step.desc}</p>
              </div>
            ))}
          </div>
        </div>
      </section>

      {/* ── Architecture overview ── */}
      <section className="lp-section dark">
        <div className="section-inner" style={{ textAlign: "center" }}>
          <span className="section-eyebrow">Architecture</span>
          <h2 className="section-h2 light-text" style={{ marginBottom: "0.75rem" }}>One server, every team</h2>
          <p className="section-sub light-sub" style={{ margin: "0 auto 3rem" }}>
            The MCP server acts as a capability backend. Business logic stays in your agents and prompts — not locked inside the tool layer.
          </p>
          <div style={{
            display: "flex",
            alignItems: "center",
            justifyContent: "center",
            gap: "0",
            flexWrap: "wrap",
            maxWidth: 800,
            margin: "0 auto",
          }}>
            {[
              { label: "BD Team",   color: "#10b981", icon: "bi-briefcase-fill" },
              { label: "Dev Team",  color: "#6366f1", icon: "bi-code-slash" },
              { label: "Mgmt Team", color: "#f59e0b", icon: "bi-bar-chart-fill" },
            ].map((team, i) => (
              <div key={team.label} style={{ display: "flex", alignItems: "center", gap: 0 }}>
                <div style={{
                  textAlign: "center",
                  padding: "1.25rem 1.5rem",
                  background: "rgba(255,255,255,0.03)",
                  border: `1px solid ${team.color}33`,
                  borderRadius: 12,
                  minWidth: 110,
                }}>
                  <div style={{ width: 40, height: 40, borderRadius: 10, background: `${team.color}1a`, display: "flex", alignItems: "center", justifyContent: "center", margin: "0 auto 0.6rem" }}>
                    <i className={`bi ${team.icon}`} style={{ color: team.color, fontSize: "1rem" }} />
                  </div>
                  <div style={{ fontSize: "0.78rem", fontWeight: 600, color: "#cbd5e1" }}>{team.label}</div>
                </div>
                {i < 2 && <div style={{ width: 32, height: 1, background: "rgba(255,255,255,0.1)" }} />}
              </div>
            ))}
            <div style={{ width: 40, display: "flex", alignItems: "center", justifyContent: "center" }}>
              <div style={{ fontSize: "1.2rem", color: "#475569" }}>→</div>
            </div>
            <div style={{
              textAlign: "center",
              padding: "1.5rem 2rem",
              background: "rgba(99,102,241,0.08)",
              border: "1px solid rgba(99,102,241,0.25)",
              borderRadius: 14,
              boxShadow: "0 0 30px rgba(99,102,241,0.15)",
            }}>
              <SyndrixLogo size={44} />
              <div style={{ fontSize: "0.875rem", fontWeight: 700, color: "#a5b4fc", marginTop: "0.75rem" }}>Syndrix</div>
              <div style={{ fontSize: "0.7rem", color: "#475569" }}>MCP Server</div>
            </div>
            <div style={{ width: 40, display: "flex", alignItems: "center", justifyContent: "center" }}>
              <div style={{ fontSize: "1.2rem", color: "#475569" }}>→</div>
            </div>
            <div style={{ display: "flex", flexDirection: "column", gap: "0.5rem" }}>
              {[
                { label: "Podio / GHL", icon: "bi-people-fill",    color: "#10b981" },
                { label: "GitHub",      icon: "bi-github",         color: "#6366f1" },
                { label: "Slack",       icon: "bi-slack",          color: "#f59e0b" },
                { label: "Ollama LLM",  icon: "bi-cpu-fill",       color: "#8b5cf6" },
              ].map((adapter) => (
                <div key={adapter.label} style={{
                  display: "flex",
                  alignItems: "center",
                  gap: "0.5rem",
                  padding: "0.45rem 0.85rem",
                  background: "rgba(255,255,255,0.03)",
                  border: "1px solid rgba(255,255,255,0.06)",
                  borderRadius: 8,
                  fontSize: "0.75rem",
                  color: "#94a3b8",
                  fontWeight: 500,
                }}>
                  <i className={`bi ${adapter.icon}`} style={{ color: adapter.color }} />
                  {adapter.label}
                </div>
              ))}
            </div>
          </div>
        </div>
      </section>


      {/* ── Final CTA ── */}
      <section className="lp-cta-section" id="cta">
        <div className="section-inner">
          <div className="lp-badge" style={{ margin: "0 auto 1.5rem" }}>
            <span className="lp-badge-dot" />
            Ready when you are
          </div>
          <h2 className="lp-cta-h2">
            Give your team<br />
            <span className="gradient-text">an AI superpower</span>
          </h2>
          <p className="lp-cta-sub">
            Sign in with your team bearer token and start invoking tools in seconds.
            No cloud accounts. No data leaving your infrastructure.
          </p>
          <div className="lp-cta-row">
            <Link href={isLoggedIn ? "/dashboard" : "/login"} className="btn-hero-primary" style={{ fontSize: "1rem", padding: "0.9rem 2.5rem" }}>
              <i className={`bi ${isLoggedIn ? "bi-grid-fill" : "bi-rocket-takeoff-fill"}`} />
              {isLoggedIn ? "Go to Dashboard" : "Get Started Now"}
            </Link>
            <a href="#capabilities" className="btn-hero-secondary" style={{ fontSize: "1rem", padding: "0.9rem 2rem" }}>
              <i className="bi bi-book" />
              Explore Docs
            </a>
          </div>
        </div>
      </section>

      {/* ── Footer ── */}
      <footer className="lp-footer">
        <div className="lp-footer-copy" suppressHydrationWarning>© {new Date().getFullYear()} All rights reserved.</div>
      </footer>
    </>
  );
}
