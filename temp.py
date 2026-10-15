"""Model tester — focuses on gemini-2.5-flash with/without thinking. Run: python temp.py"""
import os, time, json, ssl, socket

def _load_env():
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(p):
        for line in open(p, encoding="utf-8-sig"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                if not os.environ.get(k.strip()):
                    os.environ[k.strip()] = v.strip().strip("\"'")
_load_env()

from google import genai
from google.genai import types

PROJECT  = os.environ.get("GOOGLE_CLOUD_PROJECT", "rampguard-ai")
LOCATION = os.environ.get("GOOGLE_CLOUD_LOCATION", "us-central1")

# ── Step 1: Direct REST — gemini-2.5-flash ─────────────────────────────────
print("=== Step 1: Direct REST (real HTTP status for gemini-2.5-flash) ===")
try:
    import google.auth, google.auth.transport.requests
    creds, _ = google.auth.default(scopes=["https://www.googleapis.com/auth/cloud-platform"])
    creds.refresh(google.auth.transport.requests.Request())
    token = creds.token
    import urllib.request, urllib.error
    url = (f"https://{LOCATION}-aiplatform.googleapis.com/v1/projects/{PROJECT}"
           f"/locations/{LOCATION}/publishers/google/models/gemini-2.5-flash:generateContent")
    body = json.dumps({"contents": [{"role": "user", "parts": [{"text": "Reply: WORKING"}]}],
                       "generationConfig": {
    "maxOutputTokens": 256,
    "thinkingConfig": {
        "thinkingBudget": 0
    }
}}).encode()
    req = urllib.request.Request(url, data=body,
                                  headers={"Authorization": f"Bearer {token}",
                                           "Content-Type": "application/json"})
    import threading
    result = {}
    def call():
        try:
            with urllib.request.urlopen(req, timeout=60) as r:
                result["status"] = r.status
                result["body"] = json.loads(r.read())
        except urllib.error.HTTPError as e:
            result["status"] = e.code
            result["body"] = e.read().decode()[:400]
        except Exception as e:
            result["error"] = str(e)
    t0 = time.time()
    th = threading.Thread(target=call); th.start(); th.join(timeout=30)
    elapsed = time.time() - t0
    if result.get("error"):
        print(f"  ❌ {result['error']} ({elapsed:.1f}s)")
    elif result.get("status") == 200:
        print("  ✅ HTTP 200")
        print("  Response body:")
        print(json.dumps(result["body"], indent=2))
    elif result.get("status"):
        print(f"  ❌ HTTP {result['status']} ({elapsed:.1f}s) → {str(result['body'])[:300]}")
    else:
        print(f"  ⏱️  No response after 30s — model may be thinking too long")
except Exception as e:
    print(f"  ❌ Auth/setup error: {e}")

# ── Step 2: SDK — gemini-2.5-flash WITH thinking (default) ────────────────
print("\n=== Step 2: SDK — gemini-2.5-flash WITH thinking (default, slow) ===")
c = genai.Client(vertexai=True, project=PROJECT, location=LOCATION,
                 http_options=types.HttpOptions(timeout=120000))
for label, cfg in [
    ("With thinking (default)",
     types.GenerateContentConfig(temperature=0)),
    ("No thinking (budget=0)",
     types.GenerateContentConfig(temperature=0,
         thinking_config=types.ThinkingConfig(thinking_budget=0))),
    ("Low thinking (budget=512)",
     types.GenerateContentConfig(temperature=0,
         thinking_config=types.ThinkingConfig(thinking_budget=512))),
]:
    t0 = time.time()
    try:
        chunks = []
        for chunk in c.models.generate_content_stream(
            model="gemini-2.5-flash", contents="Reply: WORKING", config=cfg
        ):
            if chunk.text:
                chunks.append(chunk.text)
        elapsed = time.time() - t0
        print(f"  ✅ {label:<35} {elapsed:>5.1f}s → {''.join(chunks).strip()[:20]}")
    except Exception as e:
        elapsed = time.time() - t0
        print(f"  ❌ {label:<35} {elapsed:>5.1f}s → {str(e)[:55]}")

# ── Step 3: gemini-2.5-pro ─────────────────────────────────────────────────
print("\n=== Step 3: gemini-2.5-pro (no thinking) ===")
t0 = time.time()
try:
    chunks = []
    for chunk in c.models.generate_content_stream(
        model="gemini-2.5-pro", contents="Reply: WORKING",
        config=types.GenerateContentConfig(
    temperature=0
)
    ):
        if chunk.text: chunks.append(chunk.text)
    elapsed = time.time() - t0
    print(f"  ✅ gemini-2.5-pro  {elapsed:.1f}s → {''.join(chunks).strip()[:20]}")
except Exception as e:
    elapsed = time.time() - t0
    print(f"  ❌ gemini-2.5-pro  {elapsed:.1f}s → {str(e)[:60]}")

# ── Step 4: Gemini Live API (WebSocket) ────────────────────────────────────
print("\n=== Step 4: Gemini Live API WebSocket ===")
import asyncio, sys as _sys

async def _test_live(model_name):
    try:
        live_c = genai.Client(vertexai=True, project=PROJECT, location=LOCATION)
        live_cfg = types.LiveConnectConfig(
            response_modalities=["TEXT"],
            system_instruction="Reply with one short sentence.",
        )
        async with live_c.aio.live.connect(model=model_name, config=live_cfg) as session:
            await session.send_client_content(
                turns=types.Content(role="user", parts=[types.Part(text="Say: LIVE_WORKS")]),
                turn_complete=True,
            )
            parts = []
            async for msg in session.receive():
                sc = getattr(msg, "server_content", None)
                if sc:
                    mt = getattr(sc, "model_turn", None)
                    if mt:
                        for p in (mt.parts or []):
                            if getattr(p, "text", None): parts.append(p.text)
                    if getattr(sc, "turn_complete", False): break
            return "✅ " + "".join(parts).strip()[:50]
    except Exception as e:
        return f"❌ {type(e).__name__}: {str(e)[:200]}"

if _sys.platform == "win32":
    asyncio.set_event_loop_policy(asyncio.WindowsSelectorEventLoopPolicy())

API_KEY = os.environ.get("GEMINI_API_KEY", "")

# Test Vertex AI Live (requires project allowlisting — likely unavailable)
for _lm in ["gemini-2.0-flash-live-001", "gemini-2.5-flash"]:
    t0 = time.time()
    result = asyncio.run(_test_live(_lm))
    print(f"  Vertex AI  {_lm:<35} {time.time()-t0:>5.1f}s  {result}")

# Test AI Studio Live (requires GEMINI_API_KEY in .env)
if API_KEY:
    async def _test_live_aistudio():
        try:
            live_c = genai.Client(api_key=API_KEY)
            live_cfg = types.LiveConnectConfig(
                response_modalities=["TEXT"],
                system_instruction="Reply with one short sentence.",
            )
            async with live_c.aio.live.connect(model="gemini-2.0-flash-live-001", config=live_cfg) as session:
                await session.send_client_content(
                    turns=types.Content(role="user", parts=[types.Part(text="Say: LIVE_WORKS")]),
                    turn_complete=True,
                )
                parts = []
                async for msg in session.receive():
                    sc = getattr(msg, "server_content", None)
                    if sc:
                        mt = getattr(sc, "model_turn", None)
                        if mt:
                            for p in (mt.parts or []):
                                if getattr(p, "text", None): parts.append(p.text)
                        if getattr(sc, "turn_complete", False): break
                return "OK: " + "".join(parts).strip()[:50]
        except Exception as e:
            return f"FAIL {type(e).__name__}: {str(e)[:150]}"
    t0 = time.time()
    r = asyncio.run(_test_live_aistudio())
    print(f"  AI Studio  gemini-2.0-flash-live-001          {time.time()-t0:>5.1f}s  {r}")
else:
    print("  AI Studio  (skipped — GEMINI_API_KEY not set in .env)")

print("\n=== Done ===")
