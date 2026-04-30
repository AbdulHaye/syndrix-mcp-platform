import ContactResult from "./ContactResult";
import LeadTable from "./LeadTable";
import RepoResults from "./RepoResults";
import PRTable from "./PRTable";
import TextResult from "./TextResult";
import SlackResult from "./SlackResult";
import RAGResults from "./RAGResults";
import NoteResult from "./NoteResult";
import PipelineResult from "./PipelineResult";
import type { ToolInvokeResult } from "@/types";

interface Props {
  invocation: ToolInvokeResult;
}

function GenericSuccess({ data }: { data: unknown }) {
  return (
    <pre className="result-panel">{JSON.stringify(data, null, 2)}</pre>
  );
}

export default function ResultRenderer({ invocation }: Props) {
  if (!invocation.success) {
    return (
      <div className="alert alert-danger small mb-0">
        <i className="bi bi-x-circle me-2" />
        {invocation.error ?? "Tool call failed"}
      </div>
    );
  }

  const res = invocation.result as Record<string, unknown>;

  switch (invocation.tool) {
    case "crm.contact.get":
      return <ContactResult data={res} />;

    case "crm.lead.search":
      return <LeadTable data={res as Parameters<typeof LeadTable>[0]["data"]} />;

    case "crm.note.create":
      return <NoteResult data={res} />;

    case "crm.pipeline.stages":
      return <PipelineResult data={res as Parameters<typeof PipelineResult>[0]["data"]} />;

    case "crm.message.send":
    case "crm.email.send": {
      const ok = res.success !== false;
      return ok ? (
        <div className="alert alert-success small mb-0">
          <i className="bi bi-check-circle me-2" />
          {invocation.tool === "crm.email.send"
            ? `Email sent to ${String(res.to ?? "")}`
            : "Message sent successfully"}
        </div>
      ) : (
        <div className="alert alert-danger small mb-0">
          <i className="bi bi-exclamation-triangle me-2" />
          {String(res.error ?? "Failed")}
        </div>
      );
    }

    case "repo.search":
      return <RepoResults data={res as Parameters<typeof RepoResults>[0]["data"]} />;

    case "ticket.create": {
      const ok = res.success !== false;
      return ok ? (
        <div className="alert alert-success small mb-0">
          <i className="bi bi-check-circle me-2" />
          Issue <strong>#{String(res.issue_number)}</strong> created —{" "}
          <a href={String(res.html_url)} target="_blank" rel="noreferrer">
            {String(res.title)}
          </a>
        </div>
      ) : (
        <div className="alert alert-danger small mb-0">{String(res.error ?? "Failed")}</div>
      );
    }

    case "ticket.pr_list":
      return <PRTable data={res as Parameters<typeof PRTable>[0]["data"]} />;

    case "spec.generate":
      return <TextResult label="Technical Specification" field="specification" data={res} />;

    case "bug.triage":
      return <TextResult label="Triage Analysis" field="triage_analysis" data={res} />;

    case "slack.message.send":
    case "slack.channel.history":
      return <SlackResult data={res as Parameters<typeof SlackResult>[0]["data"]} />;

    case "rag.search":
      return <RAGResults data={res as Parameters<typeof RAGResults>[0]["data"]} />;

    default:
      return <GenericSuccess data={res} />;
  }
}
