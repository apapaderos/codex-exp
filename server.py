"""
NEXUS MVP server — no Azure required.

Serves the tablet UI and proxies chat requests to Ollama (free, runs locally).
Transcription is handled by the browser's built-in speech recognition (Chrome).

Run:
  python3 server.py
"""

import httpx
from collections import defaultdict
from pathlib import Path

from fastapi import FastAPI
from fastapi.responses import HTMLResponse
from fastapi.staticfiles import StaticFiles
from pydantic import BaseModel

app = FastAPI(title="NEXUS MVP")


# ── Chat ──────────────────────────────────────────────────────────────────────
# The browser sends the full transcript text + conversation history with every
# message, so the server stays stateless — no session management needed.

class ChatRequest(BaseModel):
    message:    str
    transcript: str              # full transcript accumulated in the browser
    history:    list[dict] = []  # [{role, content}, ...] previous turns


SYSTEM_PROMPT = """\
You are NEXUS, an AI assistant analysing a live conversation transcript.
Answer the user's questions using the transcript as your only source of truth.
Be concise and direct. Use bullet points when listing multiple things.
If the answer is not in the transcript, say so clearly — do not make things up.
The transcript may contain Greek and English — handle both languages naturally.\
"""


@app.post("/chat")
async def chat(req: ChatRequest):
    context = req.transcript.strip() or "(no transcript yet — recording may not have started)"
    system  = f"{SYSTEM_PROMPT}\n\nTRANSCRIPT:\n{context}"

    messages = [{"role": "system", "content": system}]
    messages.extend(req.history)
    messages.append({"role": "user", "content": req.message})

    try:
        async with httpx.AsyncClient(timeout=120) as client:
            resp = await client.post(
                "http://localhost:11434/api/chat",
                json={"model": "llama3.2", "messages": messages, "stream": False},
            )
            resp.raise_for_status()
            return {"response": resp.json()["message"]["content"]}

    except httpx.ConnectError:
        return {
            "response": (
                "⚠️ Cannot reach Ollama. "
                "Make sure Ollama is running: open a Terminal and run `ollama serve`."
            )
        }
    except Exception as exc:
        return {"response": f"⚠️ Error: {exc}"}


# ── Static files ──────────────────────────────────────────────────────────────
app.mount("/", StaticFiles(directory="static", html=True), name="static")


# ── Dev entrypoint ────────────────────────────────────────────────────────────
if __name__ == "__main__":
    import uvicorn
    print("\n✅  NEXUS is running.")
    print("   Open this address in Chrome: http://localhost:8000\n")
    uvicorn.run("server:app", host="0.0.0.0", port=8000, reload=True, log_level="warning")
