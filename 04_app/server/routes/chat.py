"""Agent chat — streamed over SSE."""
import json

from fastapi import APIRouter
from fastapi.responses import StreamingResponse
from pydantic import BaseModel

import agent  # vendored agent loop (server/ on sys.path)
import telemetry

router = APIRouter()


class ChatReq(BaseModel):
    conversation_id: str = "demo"
    message: str


@router.post("/chat")
def chat(req: ChatReq):
    telemetry.event("chat", {"chars": len(req.message or "")}, req.conversation_id)

    def gen():
        try:
            for ev in agent.stream_turn(req.conversation_id, req.message):
                yield f"data: {json.dumps(ev, default=str)}\n\n"
        except Exception as e:  # surface as an error event rather than dropping the stream
            yield f"data: {json.dumps({'type': 'error', 'message': str(e)})}\n\n"
        yield 'data: {"type": "done"}\n\n'
    return StreamingResponse(gen(), media_type="text/event-stream",
                             headers={"Cache-Control": "no-cache", "X-Accel-Buffering": "no"})
