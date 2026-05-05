"""HTTP API server — FastAPI wrapper for invest agent.

Usage:
    python -m src.api
    # Then: POST http://localhost:7711/invest {"text": "scan"}

Install: pip install fastapi uvicorn
"""

import json
import sys
from src.init import load_env, init_db, ensure_build_up

load_env()
conn, repo = init_db()
ensure_build_up(repo)

try:
    from fastapi import FastAPI
    from fastapi.responses import JSONResponse
    import uvicorn
except ImportError:
    print("fastapi/uvicorn not installed. Run: pip install fastapi uvicorn")
    print("Falling back to simple stdin/stdout mode...")
    _fallback_stdin()
    sys.exit(0)

app = FastAPI(title="Invest Agent", version="2.0.0")

from src.orchestrator import handle


@app.post("/invest")
async def invest(payload: dict):
    text = payload.get("text", "").strip()
    if not text:
        return JSONResponse({"status": "error", "message": "No text provided"}, status_code=400)
    result = handle(text, repo)
    return JSONResponse(result)


@app.get("/health")
async def health():
    return {"status": "ok", "version": "2.0.0"}


def _fallback_stdin():
    """Read commands from stdin line-by-line as fallback."""
    from src.orchestrator import handle
    print("Invest Agent ready. Type commands (scan/status/pe...). Ctrl+D to exit.")
    for line in sys.stdin:
        text = line.strip()
        if not text:
            continue
        result = handle(text, repo)
        print(json.dumps(result, ensure_ascii=False, default=str))


def main():
    uvicorn.run(app, host="127.0.0.1", port=7711)


if __name__ == "__main__":
    main()
