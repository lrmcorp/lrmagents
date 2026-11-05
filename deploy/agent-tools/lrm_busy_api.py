"""/api/lrm/busy: agent runs in progress (the deploy waits for 0 before restarting).
Console login or the internal API token. Installed as channel/web/api/lrm_busy.py."""
import json

import web

from channel.web.core._common import _require_auth


class LrmBusyHandler:
    def GET(self):
        from channel.web.api.lrm_activity import _internal_caller
        if not _internal_caller():
            _require_auth()
        from agent import lrm_busy
        web.header("Content-Type", "application/json; charset=utf-8")
        return json.dumps(lrm_busy.state())
