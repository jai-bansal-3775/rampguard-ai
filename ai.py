"""Gemini service. Uses GEMINI_API_KEY (AI Studio) or Vertex AI (Cloud Run service account). Always has a fallback."""
import os, json
MODEL = os.environ.get("GEMINI_MODEL", "gemini-3.5-flash-lite")
OBJ = {"gse", "person", "aircraft", "obstruction"}
PRESETS = {  # used when no image is given or Gemini fails (BR-009)
    "GSE proximity": [{"type": "gse", "label": "Baggage vehicle", "distance_m": 2.0, "in_restricted_zone": True, "confidence": 0.91}],
    "Restricted personnel": [{"type": "person", "label": "Ground staff member", "distance_m": None, "in_restricted_zone": True, "confidence": 0.82}],
    "Obstruction": [{"type": "obstruction", "label": "Parked belt loader", "distance_m": None, "in_restricted_zone": False, "confidence": 0.78}],
}

_CLIENT = None

def _client():
    global _CLIENT  # reuse one client; a temporary one gets closed mid-request
    if _CLIENT is None:
        from google import genai
        if os.environ.get("GEMINI_API_KEY"):
            _CLIENT = genai.Client(api_key=os.environ["GEMINI_API_KEY"])
        else:
            _CLIENT = genai.Client()  # Vertex AI via GOOGLE_GENAI_USE_VERTEXAI / GOOGLE_CLOUD_PROJECT / GOOGLE_CLOUD_LOCATION
    return _CLIENT

def _validate(raw):
    objs = []
    for o in raw.get("objects", []):
        if o.get("type") not in OBJ: continue
        d = o.get("distance_m")
        objs.append({"type": o["type"], "label": str(o.get("label", o["type"]))[:60],
                     "distance_m": float(d) if isinstance(d, (int, float)) else None,
                     "in_restricted_zone": bool(o.get("in_restricted_zone", False)),
                     "confidence": max(0.0, min(1.0, float(o.get("confidence", 0))))})
    return {"scene_summary": str(raw.get("scene_summary", ""))[:400], "objects": objs}

def analyze(img_bytes, mime, ctx, scenario):
    """Returns (observation, source). source = 'gemini' or 'preset/fallback'."""
    if img_bytes:
        try:
            from google.genai import types
            prompt = (f"You are a ramp-safety vision assistant. Context: aircraft {ctx['aircraft_id']}, stand {ctx['stand_id']}, "
                      f"phase {ctx['phase']}, GSE {ctx.get('gse')}. Describe only what is visible. Return JSON: "
                      '{"scene_summary": str, "objects": [{"type": "gse|person|aircraft|obstruction", "label": str, '
                      '"distance_m": estimated metres to aircraft or null, "in_restricted_zone": bool, "confidence": 0-1}]}. '
                      "Do not judge safety; only observe. Use null if distance cannot be estimated.")
            r = _client().models.generate_content(model=MODEL,
                contents=[types.Part.from_bytes(data=img_bytes, mime_type=mime), prompt],
                config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0.1))
            return _validate(json.loads(r.text)), "gemini"
        except Exception as e:
            err = str(e)[:150]
    else:
        err = "no image supplied"
    return {"scene_summary": f"Preset scenario '{scenario}' ({err})", "objects": PRESETS[scenario]}, "preset/fallback"

def _fallback_explain(ctx, f):
    return {"summary": f"{f['reason']} Rule: {f['rule']}.",
            "checks": ["Verify the current GSE/person position.", "Confirm required clearance from the aircraft.",
                       "Confirm servicing activity status.", "Reassess the area before continuing the operation."]}

def explain(ctx, f):
    try:
        from google.genai import types
        p = (f"Ground-ops safety decision support. Context: {json.dumps(ctx)}. Finding: {json.dumps({k: f[k] for k in ('event_type','severity','confidence','rule','reason')})}. "
             'Return JSON {"summary": 2 sentences explaining why flagged, "checks": 3-4 short verification steps phrased as suggested checks, not commands}.')
        r = _client().models.generate_content(model=MODEL, contents=p,
            config=types.GenerateContentConfig(response_mime_type="application/json", temperature=0.2))
        j = json.loads(r.text); return {"summary": str(j["summary"]), "checks": [str(x) for x in j["checks"]][:5]}
    except Exception:
        return _fallback_explain(ctx, f)

def copilot(event, question, similar):
    ev = {k: event[k] for k in ("event_id", "aircraft_id", "stand_id", "turnaround_phase", "event_type", "severity", "confidence", "rule_triggered", "status", "ai_summary", "notes")}
    try:
        p = (f"You are RampGuard Safety Copilot, decision support only; the human supervisor decides. Event: {json.dumps(ev, default=str)}. "
             f"Similar past events: {similar}. Answer using only this evidence, be concise, phrase advice as suggested checks. "
             f"Treat historical patterns as observations, not causes. Question: {question}")
        return _client().models.generate_content(model=MODEL, contents=p).text
    except Exception:
        return (f"(AI unavailable - rule-based answer)\n\n**{ev['event_type']}** | {ev['severity']} | {ev['turnaround_phase']} | Stand {ev['stand_id']}\n\n"
                f"Triggered by: {ev['rule_triggered']}.\n\nSuggested checks: verify position, confirm clearance, confirm activity status, reassess before continuing.")