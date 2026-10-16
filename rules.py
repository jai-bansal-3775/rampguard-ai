"""Deterministic guardrails. Rules, zones, thresholds and phases come from config.json; geometry decides zone membership."""
import json, os
import geo
CFG = json.load(open(os.path.join(os.path.dirname(os.path.abspath(__file__)), "config.json")))
T = CFG["thresholds"]

def shift(s, n):
    S = CFG["severities"]; return S[max(0, min(len(S) - 1, S.index(s) + n))]

def routine(o, phase):  # expected equipment doing its job is normal ramp activity
    return o["type"] == "gse" and o.get("kind") in CFG["expected_gse"].get(phase, []) and o.get("activity") in ("servicing", "stationary")

def in_zone(o, zones, name):
    """True if any part of the object's bounding box is inside the named zone polygon."""
    if not o.get("box"): return False
    poly = zones.get(name) or []
    if len(poly) < 3: return False
    y0, x0, y1, x1 = o["box"]
    cx, cy = (x0 + x1) / 2, (y0 + y1) / 2
    # Check 9 representative points: 4 corners, 4 edge midpoints, centre
    for pt in [(x0,y0),(x1,y0),(x1,y1),(x0,y1),(cx,y0),(cx,y1),(x0,cy),(x1,cy),(cx,cy)]:
        if geo.inside(pt, poly): return True
    return False

def candidates(obs, phase, zones):
    out = []
    for o in obs["objects"]:
        if o["confidence"] < T["min_confidence"]: continue
        for r in CFG["rules"]:
            if r["object"] != o["type"] or not (r["phases"] == "*" or phase in r["phases"]): continue
            if r.get("zone") and not in_zone(o, zones, r["zone"]): continue
            # Zone-based rules: presence inside the zone always warrants review.
            # Operators dismiss expected routine equipment via the workflow.
            # Non-zone rules (e.g. SE-003 obstruction): still apply routine filter.
            if r.get("zone") or not routine(o, phase):
                out.append((r, o))
    return out

def near(a, b, dist):
    if not a.get("box") or not b.get("box"): return False
    fa, fb = geo.footprint(a["box"]), geo.footprint(b["box"])
    return ((fa[0] - fb[0]) ** 2 + (fa[1] - fb[1]) ** 2) ** 0.5 < dist

def track(items, dist=150):
    """Group one rule's (frame, object) hits into per-object tracks by footprint proximity; each track is one finding."""
    tracks = []  # each: {frame: [objects]}, last object
    for i, o in items:
        for t in tracks:
            if i not in t[0] and near(o, t[1], dist): t[0][i] = [o]; t[1] = o; break
        else: tracks.append([{i: [o]}, o])
    return [t[0] for t in tracks]

def evaluate(frames, phase, zones):
    hits = {}
    for i, obs in enumerate(frames):
        for r, o in candidates(obs, phase, zones):
            # Key on rule+type+kind so same physical object tracked across frames even if label text varies
            h = hits.setdefault((r["id"], o["type"], o.get("kind", "")), {"r": r, "o": o, "n": 0, "f": i}); h["n"] += 1
            if o["confidence"] > h["o"]["confidence"]: h["o"], h["f"] = o, i
    out = []
    for h in hits.values():
        r, o, n, c = h["r"], h["o"], h["n"], h["o"]["confidence"]
        sev = r["severity"].get(phase, r["severity"]["default"]); extra = ""
        if r.get("escalate_zone") and in_zone(o, zones, r["escalate_zone"]): sev, extra = shift(sev, 1), f"; also inside {r['escalate_zone']} zone (escalated)"
        if c < T["low_confidence"]: sev = shift(sev, -1)
        single = len(frames) == 1
        if single and CFG["severities"].index(sev) > CFG["severities"].index(T["single_frame_max_severity"]):
            sev, extra = T["single_frame_max_severity"], extra + "; capped (single frame, confirm with 2+ frames)"
        ok = n >= T["persistence_frames"] if not single else c >= T["single_frame_min_confidence"]
        why = "" if ok else (f"seen in {n}/{len(frames)} frames (needs {T['persistence_frames']})" if not single else f"single-frame confidence {c:.0%} below {T['single_frame_min_confidence']:.0%}")
        zn = f" inside {r['zone']} zone" if r.get("zone") else ""
        out.append(dict(rule=r, object=o, severity=sev, confidence=c, eligible=ok, held_reason=why, frame=h["f"],
                        reason=f"{r['name']}: {o['label']}{zn} during {phase}; seen in {n}/{len(frames)} frame(s){extra}"))
    return out