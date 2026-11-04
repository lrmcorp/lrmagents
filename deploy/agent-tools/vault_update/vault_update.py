"""vault_update: how final results reach the vault (Lorium checks, the owner approves)."""
from agent.tools.base_tool import BaseTool, ToolResult


class VaultUpdate(BaseTool):
    name: str = "vault_update"
    description: str = (
        "Put information into the owner's vault (the shared knowledge notes). Agents never write knowledge/ "
        "directly: when work is finished (a report, research result, decision, contact, process, plan), 'propose' "
        "the note with its full Markdown content. Lorium (the lead agent) checks every proposal, and the owner "
        "approves it before it is saved. To update an existing note, read it first and propose the complete new "
        "version with the latest information (old facts corrected or removed), using the same path. Actions: "
        "'propose' (title or path, content, summary, optional folder and sources), 'list' (what is waiting), "
        "'status' (id), and for Lorium only: 'review' (id, decision approve/reject, optional corrected content, note)."
    )
    params: dict = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["propose", "list", "status", "review"]},
            "title": {"type": "string", "description": "Note title for a new note"},
            "path": {"type": "string", "description": "knowledge/... path of an existing note to update"},
            "folder": {"type": "string", "description": "Folder under knowledge/, e.g. '10 Wiki' or '14 References/Books'"},
            "content": {"type": "string", "description": "The complete note in Markdown"},
            "summary": {"type": "string", "description": "One line: what is new or changed"},
            "sources": {"type": "array", "items": {"type": "string"}, "description": "Where the information came from"},
            "id": {"type": "string", "description": "Update id for 'status' and 'review'"},
            "decision": {"type": "string", "enum": ["approve", "reject"]},
            "note": {"type": "string", "description": "For 'review': a short note for the owner, or the reason for rejecting"},
        },
        "required": ["action"],
    }

    def execute(self, args: dict) -> ToolResult:
        from agent import lrm_vault_flow as flow
        model = getattr(self, "model", None)
        agent_id = str(getattr(model, "agent_id", "") or "") or flow.reviewer_id()
        session = str(getattr(model, "session_id", "") or "")
        action = (args.get("action") or "").strip()
        try:
            if action == "propose":
                item = flow.propose(agent_id, session, args.get("title") or "", args.get("content") or "", args.get("summary") or "",
                                    args.get("folder") or "", args.get("path") or "", args.get("sources") or [])
                who = flow.agent_name(flow.reviewer_id())
                return ToolResult.success(
                    f"Proposed (id {item['id']}) for {item['path']}. "
                    + ("It is waiting for the owner's approval in the Vault → Updates." if item["status"] == "owner"
                       else f"{who} will check it, then the owner approves it. Nothing is in the vault until then."))
            if action == "list":
                rows = flow.pending()
                if not rows:
                    return ToolResult.success("No vault updates are waiting.")
                return ToolResult.success("\n".join(f"- {r['id']} [{'waiting for ' + flow.agent_name(flow.reviewer_id()) if r['status'] == 'review' else 'waiting for the owner'}] "
                                                    f"{r['path']} from {r['agent_name']}: {r['summary']}" for r in rows))
            if action == "status":
                row = next((r for r in flow.recent(200) if r["id"] == str(args.get("id") or "")), None)
                if not row:
                    return ToolResult.fail("No update with that id.")
                return ToolResult.success(f"{row['id']} {row['path']}: {row['status']}. " + "; ".join(row["history"]))
            if action == "review":
                item = flow.review(str(args.get("id") or ""), agent_id, args.get("decision") or "", args.get("content"), args.get("note") or "")
                return ToolResult.success("Rejected; the proposer can see why with 'status'." if item["status"] == "rejected"
                                          else "Passed to the owner for approval (they get a WhatsApp notice).")
            return ToolResult.fail("action must be propose, list, status or review.")
        except PermissionError as e:
            return ToolResult.fail(str(e))
        except Exception as e:
            return ToolResult.fail(f"Vault update: {e}")
