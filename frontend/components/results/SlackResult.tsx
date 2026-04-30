interface SlackMessage {
  ts?: string;
  text?: string;
  user?: string;
  bot_id?: string;
  [key: string]: unknown;
}

interface Props {
  data: { messages?: SlackMessage[]; has_more?: boolean; success?: boolean; ts?: string; channel?: string };
}

function formatTs(ts?: string): string {
  if (!ts) return "";
  const ms = parseFloat(ts) * 1000;
  return new Date(ms).toLocaleTimeString([], { hour: "2-digit", minute: "2-digit" });
}

export default function SlackResult({ data }: Props) {
  // send_message response
  if (data.ts && data.channel) {
    return (
      <div className="alert alert-success small mb-0">
        <i className="bi bi-check-circle me-2" />
        Message sent to <strong>{data.channel}</strong> at {formatTs(data.ts)}
      </div>
    );
  }

  const messages = data.messages ?? [];

  if (!messages.length) {
    return (
      <div className="text-center py-4 text-muted small">
        <i className="bi bi-chat-square me-2" />
        No messages found
      </div>
    );
  }

  return (
    <div className="border rounded-3 overflow-hidden">
      <div className="px-3 py-2 border-bottom bg-light d-flex align-items-center justify-content-between">
        <span className="small fw-semibold text-muted">
          {messages.length} message{messages.length !== 1 ? "s" : ""}
        </span>
        {data.has_more && (
          <span className="badge bg-secondary-subtle text-secondary border">more available</span>
        )}
      </div>
      <div style={{ maxHeight: 400, overflowY: "auto" }}>
        {[...messages].reverse().map((msg, i) => (
          <div key={msg.ts ?? i} className="d-flex gap-2 px-3 py-2 border-bottom">
            <div
              style={{
                width: 32, height: 32, borderRadius: "8px",
                background: msg.bot_id ? "#6f42c1" : "#0d6efd",
                display: "flex", alignItems: "center", justifyContent: "center",
                color: "#fff", fontSize: "0.75rem", fontWeight: 700, flexShrink: 0,
              }}
            >
              {msg.bot_id ? <i className="bi bi-robot" /> : (msg.user ?? "?")[0]?.toUpperCase()}
            </div>
            <div className="flex-grow-1 min-width-0">
              <div className="d-flex align-items-center gap-2">
                <span className="small fw-semibold">
                  {msg.bot_id ? "Bot" : (msg.user ?? "Unknown")}
                </span>
                <span className="small text-muted">{formatTs(msg.ts)}</span>
              </div>
              <div className="small mt-1" style={{ wordBreak: "break-word" }}>
                {String(msg.text ?? "")}
              </div>
            </div>
          </div>
        ))}
      </div>
    </div>
  );
}
