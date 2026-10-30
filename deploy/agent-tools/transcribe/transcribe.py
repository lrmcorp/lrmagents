"""transcribe_audio: turn a voice note (e.g. from WhatsApp) into text.

Uses the speech-recognition provider chosen in Config -> Models, the same path
the console's microphone button uses. Installed into LRMagent by its image.
"""

import os

from agent.tools.base_tool import BaseTool, ToolResult
from common.log import logger

# Voice notes arrive on the shared, read-only WhatsApp media volume.
_ALLOWED_ROOTS = ("/media/whatsapp/",)
_MAX_BYTES = 25 * 1024 * 1024


class TranscribeAudio(BaseTool):
    name: str = "transcribe_audio"
    description: str = (
        "Transcribe a voice note or audio file to text. Use it when a message says the user sent "
        "a voice note and gives its file path; then answer what they said."
    )
    params: dict = {
        "type": "object",
        "properties": {"path": {"type": "string", "description": "Path of the audio file"}},
        "required": ["path"],
    }

    def execute(self, args: dict) -> ToolResult:
        path = os.path.realpath(str(args.get("path") or ""))
        if not any(path.startswith(root) for root in _ALLOWED_ROOTS):
            return ToolResult.fail("Only voice notes received over WhatsApp (/media/whatsapp/...) can be transcribed.")
        if not os.path.isfile(path):
            return ToolResult.fail("That audio file no longer exists.")
        if os.path.getsize(path) > _MAX_BYTES:
            return ToolResult.fail("The audio file is too large to transcribe.")
        try:
            from bridge.bridge import Bridge
            from bridge.reply import ReplyType
            reply = Bridge().fetch_voice_to_text(path)
        except Exception as e:
            logger.warning(f"[transcribe_audio] failed: {e}")
            return ToolResult.fail("Speech recognition is not set up. Configure it under Config -> Models -> Speech Recognition.")
        if reply is None or reply.type != ReplyType.TEXT or not str(reply.content or "").strip():
            detail = str(getattr(reply, "content", "") or "")[:200]
            return ToolResult.fail(f"Could not transcribe the voice note. {detail}".strip())
        return ToolResult.success(str(reply.content).strip())
