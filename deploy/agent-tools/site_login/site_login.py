"""site_login: sign the agents' browser in to one of the owner's sites with its saved login.

The agent names the site; the browser types the username and password (agent/lrm_logins.py).
The password is never returned to the model. Owner's chats and scheduled tasks only.
"""
from agent.tools.base_tool import BaseTool, ToolResult


class SiteLogin(BaseTool):
    name: str = "site_login"
    description: str = (
        "Sign the browser in to one of the owner's sites with the login the owner saved (Browser page → Keep signed "
        "in). Use it whenever a page asks you to sign in, or a site you need shows its login page: action 'sign_in' "
        "with the site's address (or the login page's), then continue with the browser tool. 'list' shows the sites "
        "with a saved login. You never see the password; never ask the owner to send passwords in chat. If it reports "
        "a one-time code or captcha, tell the owner to finish the sign-in on the Browser page."
    )
    params: dict = {
        "type": "object",
        "properties": {
            "action": {"type": "string", "enum": ["list", "sign_in"]},
            "url": {"type": "string", "description": "For 'sign_in': the site or login page address"},
        },
        "required": ["action"],
    }

    def execute(self, args: dict) -> ToolResult:
        from agent import lrm_logins
        from agent.lrm_owner import REFUSAL, owner_session
        model = getattr(self, "model", None)
        if not owner_session(str(getattr(model, "session_id", "") or ""))[0]:
            return ToolResult.fail(REFUSAL)
        action = (args.get("action") or "").strip()
        if action == "list":
            rows = lrm_logins.public()
            if not rows:
                return ToolResult.success("No saved logins yet. The owner adds them on the console's Browser page (Keep signed in).")
            return ToolResult.success("Saved logins:\n" + "\n".join(f"- {r['host']} ({r['username']})" for r in rows))
        if action != "sign_in":
            return ToolResult.fail("action must be 'list' or 'sign_in'.")
        url = str(args.get("url") or "").strip()
        from agent.tools.browser.browser_tool import BrowserTool
        service = BrowserTool._shared_service
        if service is None:
            BrowserTool()._get_service()
            service = BrowserTool._shared_service
        try:
            ok, message = service._submit(lambda: lrm_logins.sign_in(service._page, url or None))
        except Exception as e:
            return ToolResult.fail(f"Sign-in failed: {type(e).__name__}")
        return ToolResult.success(message) if ok else ToolResult.fail(message)
