"""Gemini service. No fabricated output: failures raise AIError and the UI shows a degraded state."""
import os, json
from rules import CFG
def _load_env():
    """Read GEMINI_API_KEY etc. from a local .env file (git-ignored). Real environment variables take priority."""
    p = os.path.join(os.path.dirname(os.path.abspath(__file__)), ".env")
    if os.path.exists(p):
        for line in open(p, encoding="utf-8-sig"):
            line = line.strip()
            if line and not line.startswith("#") and "=" in line:
                k, v = line.split("=", 1)
                if not os.environ.get(k.strip()): os.environ[k.strip()] = v.strip().strip("\"'")
_load_env()
MODEL = os.environ.get("GEMINI_MODEL", CFG["default_model"])
PERCEPTION_MODEL = os.environ.get("GEMINI_PERCEPTION_MODEL", MODEL)
class AIError(Exception): pass
_C = None

def _client():
    global _C
    if _C is None:
        from google import genai
        _C = genai.Client(api_key=os.environ["GEMINI_API_KEY"]) if os.environ.get("GEMINI_API_KEY") else genai.Client()
    return _C

def _norm(raw):
    """Gemini sometimes wraps its JSON in a list or returns the list of moments directly."""
    if isinstance(raw, list):
        return raw[0] if len(raw) == 1 and isinstance(raw[0], dict) else {"moments": raw, "frames": raw}
    return raw

def _t(v, dur):
    """Seconds as a number, or as 'MM:SS' / 'HH:MM:SS' text; clamped to the clip length."""
    try:
        if isinstance(v, str) and ":" in v:
            v = sum(float(p) * 60 ** i for i, p in enumerate(reversed(v.split(":"))))
        return max(0.0, min(dur, float(v)))
    except (TypeError, ValueError):
        return None

def _gen(contents, as_json=True, model=None):
    from google.genai import types
    try:
        r = _client().models.generate_content(model=model or MODEL, contents=contents, config=types.GenerateContentConfig(
            response_mime_type="application/json" if as_json else None, temperature=0.1))
        return _norm(json.loads(r.text)) if as_json else r.text
    except Exception as e:
        raise AIError(str(e)[:200])

def _num(x, lo=0.0, hi=1.0):
    return max(lo, min(hi, float(x))) if isinstance(x, (int, float)) else None

def _box(b):
    if isinstance(b, list) and len(b) == 4 and all(isinstance(v, (int, float)) for v in b):
        y0, x0, y1, x1 = [max(0, min(1000, v)) for v in b]
        if y1 > y0 and x1 > x0: return [y0, x0, y1, x1]
    return None

def _iou(a, b):
    if not (a and b): return 0.0
    ay0, ax0, ay1, ax1 = a; by0, bx0, by1, bx1 = b
    iw, ih = max(0, min(ax1, bx1) - max(ax0, bx0)), max(0, min(ay1, by1) - max(ay0, by0))
    inter = iw * ih
    u = (ax1 - ax0) * (ay1 - ay0) + (bx1 - bx0) * (by1 - by0) - inter
    return inter / u if u else 0.0

def _objs(lst):
    return [{"type": o["type"], "kind": o.get("kind"), "label": str(o.get("label", o["type"]))[:60], "activity": o.get("activity", "unknown"),
             "box": _box(o.get("box_2d")), "confidence": _num(o.get("confidence")) or 0.0} for o in lst if o.get("type") in CFG["object_types"]]

def _poly(p):
    try: return [[max(0, min(1000, float(x))), max(0, min(1000, float(y)))] for x, y in p][:12]
    except (TypeError, ValueError): return []

def detect_frame(img, mime="image/jpeg"):
    """Fast single-frame pass for live analysis: phase, objects with boxes and ground-plane zones."""
    from google.genai import types
    prompt = ("You are a ramp-safety vision assistant looking at ONE live frame from a fixed ramp camera. Observe only; never identify people or infer intent. "
              "All coordinates are integers 0-1000 (x right, y down), tight around the visible object. Return JSON: "
              f'{{"phase": one of {CFG["phases"]} or "Unknown", "phase_confidence": 0-1, '
              f'"zones": {{name: ground-plane polygon [[x,y],...] 3-8 points, empty if the aircraft is not visible}}, '
              f'"objects": [{{"type": one of {CFG["object_types"]}, "kind": one of {CFG["gse_kinds"]} or null, "label": str, '
              f'"activity": "servicing|moving|stationary|unknown", "box_2d": [ymin,xmin,ymax,xmax], "confidence": 0-1}}]}}. '
              "For kind: a belt or conveyor loader that loads bags into the hold is kind baggage; a catering truck is catering; a fuel bowser or hydrant truck is fuel; a pushback or towing tractor is tug; a passenger stair is stairs; a ground power unit is ground_power. "
              "Equipment parked at or servicing the aircraft during its normal job has activity servicing or stationary; use moving only if it is clearly driving. "
              f"Include the aircraft as type aircraft with its box. Report as type obstruction any {CFG['obstruction_examples']}, and as type hazard {CFG['hazard_guidance']}. Do not skip small or thin items on the ground.")
    raw = _gen([types.Part.from_bytes(data=img, mime_type=mime), prompt], model=PERCEPTION_MODEL)
    z = CFG["zones"]
    d = raw if isinstance(raw, dict) else {}
    return {"phase": d.get("phase") if d.get("phase") in CFG["phases"] else "Unknown",
            "phase_confidence": _num(d.get("phase_confidence")) or 0.0,
            "objects": _objs(d.get("objects", [])),
            "zones": {n: _poly((d.get("zones") or {}).get(n, [])) for n in z["names"]}}

def analyze(frames, times=None):
    """frames: list of (bytes, mime). Returns phase estimate, ground-plane zone proposals and per-frame objects with boxes."""
    from google.genai import types
    g, z = CFG["gate"], CFG["zones"]
    zdesc = "; ".join(f'"{n}" = {z["descriptions"][n]}' for n in z["names"])
    tl = (" Frame i is at " + ", ".join(f"{i}: {t:.1f}s" for i, t in enumerate(times)) + ". Set activity to moving only if the object's position clearly changes between neighbouring frames; look closely at every frame, including small, thin or low-contrast items on the ground.") if times else ""
    prompt = (f"You are a ramp-safety vision assistant. Observe only: never judge safety, identify people or infer intent. "
              f"The {len(frames)} image(s) are consecutive frames from camera {g['camera']} at one aircraft stand.{tl} "
              f"All coordinates are integers 0-1000 (x right, y down), tight around the visible object. Return JSON: "
              f'{{"phase": one of {CFG["phases"]} or "Unknown", "phase_confidence": 0-1, "phase_evidence": "visible cues for the phase", "scene_summary": str, '
              f'"zones": {{name: polygon [[x,y],...] on the GROUND plane as seen in the first frame (3-8 points); empty list if the aircraft is not visible}}, '
              f'"frames": [{{"index": frame number from 0, "objects": [{{"type": one of {CFG["object_types"]}, "kind": one of {CFG["gse_kinds"]} or null, "label": str, '
              f'"activity": "servicing|moving|stationary|unknown", "box_2d": [ymin,xmin,ymax,xmax], "confidence": 0-1}}]}}]}}. '
              f"Report as type obstruction any {CFG['obstruction_examples']}, each with its box. Report as type hazard, with a specific descriptive label, {CFG['hazard_guidance']}. Every frame must include the aircraft itself as an object of type aircraft with its box. Zone names and meaning: {zdesc}. Exactly {len(frames)} entries in frames. Use Unknown/null/empty when unsure.")
    raw = _gen([types.Part.from_bytes(data=b, mime_type=m) for b, m in frames] + [prompt], model=PERCEPTION_MODEL)
    try:
        out = [{"objects": []} for _ in frames]
        for pos, f in enumerate(raw["frames"][:len(frames)]):
            k = f.get("index", pos)
            out[k if isinstance(k, int) and 0 <= k < len(frames) else pos] = {"objects": _objs(f.get("objects", []))}
        ph = raw.get("phase") if raw.get("phase") in CFG["phases"] else "Unknown"
        zones = {n: _poly((raw.get("zones") or {}).get(n, [])) for n in z["names"]}
        return {"phase": ph, "phase_confidence": _num(raw.get("phase_confidence")) or 0.0, "phase_evidence": str(raw.get("phase_evidence", ""))[:300],
                "scene_summary": str(raw.get("scene_summary", ""))[:400], "zones": zones, "frames": out}
    except (KeyError, TypeError, ValueError) as e:
        raise AIError(f"Invalid model output: {e}")

def _focus(frames, times):
    """Small, single-purpose pass over a few frames: people, ground obstructions and other hazards. Returns one object list per frame."""
    from google.genai import types
    tl = ", ".join(f"{i}: {t:.1f}s" for i, t in enumerate(times))
    prompt = (f"These {len(frames)} images are consecutive frames from a fixed ramp camera (frame times {tl}). Observe only; never identify people or infer intent. "
              "For EVERY frame list each: (1) person: every ground-crew person, including small, distant or partly hidden ones, one entry each; "
              f"(2) obstruction: {CFG['obstruction_examples']} (a traffic cone lying on its side or out of place counts; so does any hose, cable or line on the ground); "
              f"(3) hazard: {CFG['hazard_guidance']}. Do not skip small, thin or low-contrast items, and give the specific label you see. "
              'Return JSON {"frames": [{"index": frame number from 0, "objects": [{"type": "person|obstruction|hazard", "label": str, "activity": "servicing|moving|stationary|unknown", '
              '"box_2d": [ymin,xmin,ymax,xmax] integers 0-1000 tight around the item, "confidence": 0-1}]}]}.')
    raw = _gen([types.Part.from_bytes(data=b, mime_type=m) for b, m in frames] + [prompt], model=PERCEPTION_MODEL)
    out = [[] for _ in frames]
    for pos, f in enumerate(raw.get("frames", [])[:len(frames)]):
        k = f.get("index", pos)
        out[k if isinstance(k, int) and 0 <= k < len(frames) else pos] = _objs([o for o in f.get("objects", []) if isinstance(o, dict) and o.get("type") in ("person", "obstruction", "hazard")])
    return out

def analyze_clip(path, duration):
    """Sample frames ourselves and send them as images, so every box lines up with the exact frame we later draw on."""
    import io, geo
    from PIL import Image
    v = CFG["video"]
    n = max(2, min(v["max_moments"], round(duration * v["fps"])))
    fr, ts = [], []
    for t in (duration * (i + 0.5) / n for i in range(n)):
        png = geo.frame_at(path, t)
        if png:
            b = io.BytesIO(); Image.open(io.BytesIO(png)).convert("RGB").save(b, "JPEG", quality=85); fr.append((b.getvalue(), "image/jpeg")); ts.append(t)
    if len(fr) < 2: raise AIError("Could not read frames from the video.")
    a = analyze(fr, ts); a["times"] = ts
    from concurrent.futures import ThreadPoolExecutor
    n = 5
    def job(s):
        try: return s, _focus(fr[s:s + n], ts[s:s + n])
        except AIError: return s, None  # keep the general pass for this chunk
    with ThreadPoolExecutor(4) as ex:
        for s, res in ex.map(job, range(0, len(fr), n)):
            if res is None: continue
            for k, objs in enumerate(res):
                base = a["frames"][s + k]["objects"]
                add = [o for o in objs if not any(o["type"] == b["type"] and _iou(o.get("box"), b.get("box")) > 0.5 for b in base)]
                a["frames"][s + k]["objects"] = base + add
    return a

def analyze_video(data, mime, duration):
    """Gemini watches the clip and returns timestamped moments with boxes; the app then extracts those frames."""
    from google.genai import types
    g, v = CFG["gate"], CFG["video"]
    prompt = (f"You are a ramp-safety vision assistant analysing a {duration:.0f}-second clip from camera {g['camera']} at one aircraft stand. "
              "Observe only: never judge safety, identify people or infer intent. Return JSON: "
              f'{{"phase": one of {CFG["phases"]} or "Unknown", "phase_confidence": 0-1, "phase_evidence": "visible cues for the phase", "scene_summary": str, '
              f'"moments": [{{"t": seconds from clip start, "objects": [{{"type": one of {CFG["object_types"]}, "kind": one of {CFG["gse_kinds"]} or null, "label": str, '
              f'"activity": "servicing|moving|stationary|unknown", "box_2d": [ymin,xmin,ymax,xmax], "confidence": 0-1}}]}}]}}. '
              f"Give up to {v['max_moments']} moments spread over the whole clip (about one per second, denser where something changes). Look carefully at every part of every moment and do not skip small or low-contrast items. "
              f"Report as type obstruction any {CFG['obstruction_examples']}, each with its box. Report as type hazard, with a specific descriptive label, {CFG['hazard_guidance']}; detect whatever is unsafe even if it is not listed. Include each such item in every moment where it is visible. Mark activity moving only for equipment that is visibly in motion. Every moment must include the aircraft itself as type aircraft with its box. Coordinates are integers 0-1000 (x right, y down) for the frame at time t. Use Unknown/null when unsure.")
    raw = _gen([types.Part.from_bytes(data=data, mime_type=mime), prompt])
    try:
        ms = sorted(((_t(m.get("t"), duration), m) for m in raw["moments"] if isinstance(m, dict)), key=lambda x: x[0] if x[0] is not None else -1)
        ms = [(t, m) for t, m in ms if t is not None][:v["max_moments"]]
        ph = raw.get("phase") if raw.get("phase") in CFG["phases"] else "Unknown"
        return {"phase": ph, "phase_confidence": _num(raw.get("phase_confidence")) or 0.0, "phase_evidence": str(raw.get("phase_evidence", ""))[:300],
                "scene_summary": str(raw.get("scene_summary", ""))[:400], "zones": {}, "times": [t for t, _ in ms],
                "frames": [{"objects": _objs(m.get("objects", []))} for _, m in ms]}
    except (KeyError, TypeError, ValueError) as e:
        raise AIError(f"Invalid model output: {e}")

def verify(img, f, phase):
    """Second, skeptical pass: does the annotated evidence really support the proposed alert?"""
    from google.genai import types
    j = _gen([types.Part.from_bytes(data=img, mime_type="image/png"),
              f"You are a skeptical ramp-safety reviewer. The red box marks the object behind a proposed alert; shaded areas are configured zones. "
              f"Phase: {phase}. Proposed alert: {f['reason']}. Judge only whether the image clearly shows the described condition (right object, right position relative to the shaded zones); whether the activity is routine is decided elsewhere. "
              'Return JSON {"supported": true only if the image clearly shows the condition, "confidence": 0-1, "reason": one sentence}. Do not identify people or infer intent.'])
    return {"supported": j.get("supported") is True or str(j.get("supported")).lower() == "true", "confidence": _num(j.get("confidence")), "reason": str(j.get("reason", ""))[:250]}

def explain(ctx, f):
    try:
        j = _gen(f"Ground-safety decision support. Context: {json.dumps(ctx)}. Finding: {f['reason']} (severity {f['severity']}, confidence {f['confidence']:.0%}). "
                 'Return JSON {"summary": 2 sentences grounded only in this finding, "uncertainty": 1 sentence on what is uncertain or missing}. '
                 "Do not give commands, clearances or compliance statements.")
        return str(j["summary"]), str(j["uncertainty"])
    except (AIError, KeyError, TypeError):
        return f["reason"], "AI explanation unavailable (degraded mode); rule-based reason shown."

def phase_contrast(scene, pa, fa, pb, fb):
    """One short narrative on why the same scene carries different risk under two phases."""
    def summ(fs): return "; ".join(f"{f['rule']['id']} {f['rule']['name']} ({f['severity']}) on {f['object']['label']}" for f in fs) or "no alerts"
    j = _gen(f"Ground-safety decision support. The SAME camera scene is assessed under two turnaround phases. Scene: {scene}. "
             f"Under {pa}: {summ(fa)}. Under {pb}: {summ(fb)}. "
             'Return JSON {"narrative": "2-3 sentences, plain language, explaining why the identical scene carries different risk in each phase (for example, fueling makes people and equipment near the aircraft far more dangerous). Decision support only; no commands or clearances."}.')
    return str(j.get("narrative", ""))[:500]

def copilot(event, question, similar):
    keep = ("event_id", "gate", "phase", "event_type", "severity", "confidence", "status", "summary", "uncertainty", "notes")
    return _gen(f"You are RampGuard Safety Copilot, decision support only; the human supervisor decides. Event: {json.dumps({k: event[k] for k in keep}, default=str)}. "
                f"Similar past events: {similar}. Use only this evidence, be concise, phrase advice as checks for a human, never as commands or clearances. Question: {question}", as_json=False)