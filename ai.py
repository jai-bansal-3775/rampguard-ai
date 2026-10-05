"""Gemini service. No fabricated output: failures raise AIError and the UI shows a degraded state."""
import os, json
from rules import CFG
MODEL = os.environ.get("GEMINI_MODEL", CFG["default_model"])
class AIError(Exception): pass
_C = None

def _client():
    global _C
    if _C is None:
        from google import genai
        _C = genai.Client(api_key=os.environ["GEMINI_API_KEY"]) if os.environ.get("GEMINI_API_KEY") else genai.Client()
    return _C

def _gen(contents, as_json=True):
    from google.genai import types
    try:
        r = _client().models.generate_content(model=MODEL, contents=contents, config=types.GenerateContentConfig(
            response_mime_type="application/json" if as_json else None, temperature=0.1))
        return json.loads(r.text) if as_json else r.text
    except Exception as e:
        raise AIError(str(e)[:200])

def _num(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, float(x))) if isinstance(x, (int, float)) else None

def _box(b):
    if isinstance(b, list) and len(b) == 4 and all(isinstance(v, (int, float)) for v in b):
        y0, x0, y1, x1 = [max(0, min(1000, v)) for v in b]
        if y1 > y0 and x1 > x0: return [y0, x0, y1, x1]
    return None

def _poly(p):
    try: return [[max(0, min(1000, float(x))), max(0, min(1000, float(y)))] for x, y in p][:12]
    except (TypeError, ValueError): return []

def analyze(frames):
    """frames: list of (bytes, mime). Returns phase estimate, ground-plane zone proposals and per-frame objects with boxes."""
    from google.genai import types
    g, z = CFG["gate"], CFG["zones"]
    zdesc = "; ".join(f'"{n}" = {z["descriptions"][n]}' for n in z["names"])
    prompt = (f"You are a ramp-safety vision assistant. Observe only: never judge safety, identify people or infer intent. "
              f"The {len(frames)} image(s) are consecutive frames from camera {g['camera']} at one aircraft stand. "
              f"All coordinates are integers 0-1000 (x right, y down). Return JSON: "
              f'{{"phase": one of {CFG["phases"]} or "Unknown", "phase_confidence": 0-1, "phase_evidence": "visible cues for the phase", "scene_summary": str, '
              f'"zones": {{name: polygon [[x,y],...] on the GROUND plane as seen in the first frame (3-8 points); empty list if the aircraft is not visible}}, '
              f'"frames": [{{"objects": [{{"type": one of {CFG["object_types"]}, "kind": one of {CFG["gse_kinds"]} or null, "label": str, '
              f'"activity": "servicing|moving|stationary|unknown", "box_2d": [ymin,xmin,ymax,xmax], "confidence": 0-1}}]}}]}}. '
              f"Zone names and meaning: {zdesc}. Exactly {len(frames)} entries in frames. Use Unknown/null/empty when unsure.")
    raw = _gen([types.Part.from_bytes(data=b, mime_type=m) for b, m in frames] + [prompt])
    try:
        out = []
        for f in raw["frames"][:len(frames)]:
            out.append({"objects": [{"type": o["type"], "kind": o.get("kind"), "label": str(o.get("label", o["type"]))[:60],
                                     "activity": o.get("activity", "unknown"), "box": _box(o.get("box_2d")), "confidence": _num(o.get("confidence")) or 0.0}
                                    for o in f.get("objects", []) if o.get("type") in CFG["object_types"]]})
        ph = raw.get("phase") if raw.get("phase") in CFG["phases"] else "Unknown"
        zones = {n: _poly((raw.get("zones") or {}).get(n, [])) for n in z["names"]}
        return {"phase": ph, "phase_confidence": _num(raw.get("phase_confidence")) or 0.0, "phase_evidence": str(raw.get("phase_evidence", ""))[:300],
                "scene_summary": str(raw.get("scene_summary", ""))[:400], "zones": zones, "frames": out}
    except (KeyError, TypeError, ValueError) as e:
        raise AIError(f"Invalid model output: {e}")

def verify(img, f, phase):
    """Second, skeptical pass: does the annotated evidence really support the proposed alert?"""
    from google.genai import types
    j = _gen([types.Part.from_bytes(data=img, mime_type="image/png"),
              f"You are a skeptical ramp-safety reviewer. The red box marks the object behind a proposed alert; shaded areas are configured zones. "
              f"Phase: {phase}. Proposed alert: {f['reason']}. Normal ramp activity (equipment servicing the aircraft at its expected position, crew doing assigned work) is NOT an alert. "
              'Return JSON {"supported": true only if the image clearly shows the condition and it is not routine, "confidence": 0-1, "reason": one sentence}. Do not identify people or infer intent.'])
    return {"supported": bool(j.get("supported")), "confidence": _num(j.get("confidence")), "reason": str(j.get("reason", ""))[:250]}

def explain(ctx, f):
    try:
        j = _gen(f"Ground-safety decision support. Context: {json.dumps(ctx)}. Finding: {f['reason']} (severity {f['severity']}, confidence {f['confidence']:.0%}). "
                 'Return JSON {"summary": 2 sentences grounded only in this finding, "uncertainty": 1 sentence on what is uncertain or missing}. '
                 "Do not give commands, clearances or compliance statements.")
        return str(j["summary"]), str(j["uncertainty"])
    except (AIError, KeyError, TypeError):
        return f["reason"], "AI explanation unavailable (degraded mode); rule-based reason shown."

def copilot(event, question, similar):
    keep = ("event_id", "gate", "phase", "event_type", "severity", "confidence", "status", "summary", "uncertainty", "notes")
    return _gen(f"You are RampGuard Safety Copilot, decision support only; the human supervisor decides. Event: {json.dumps({k: event[k] for k in keep}, default=str)}. "
                f"Similar past events: {similar}. Use only this evidence, be concise, phrase advice as checks for a human, never as commands or clearances. Question: {question}", as_json=False)