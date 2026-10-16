import sqlite3, json, os, uuid, datetime as dt
import pandas as pd
from rules import CFG, T
DB = os.environ.get("RG_DB", "data/rampguard.db")
os.makedirs(os.path.dirname(DB), exist_ok=True); os.makedirs("data/evidence", exist_ok=True)
NEXT = CFG["lifecycle"]
COLS = "event_id ts gate camera phase phase_conf event_type rule_id severity confidence evidence status summary uncertainty checks notes resolution model_version rule_version duplicate_of".split()

def now(): return dt.datetime.utcnow().isoformat(timespec="seconds")
def conn():
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row; return c
def df(sql, a=()):
    with conn() as c: return pd.read_sql_query(sql, c, params=a)
def run(sql, a=()):
    with conn() as c: c.execute(sql, a)
def init():
    run(f"CREATE TABLE IF NOT EXISTS events({', '.join(COLS)}, PRIMARY KEY(event_id))" if False else
        f"CREATE TABLE IF NOT EXISTS events({', '.join(c + (' TEXT PRIMARY KEY' if c == 'event_id' else '') for c in COLS)})")
    run("CREATE TABLE IF NOT EXISTS history(id INTEGER PRIMARY KEY, event_id TEXT, ts TEXT, status TEXT, actor TEXT, note TEXT)")
def log(eid, status, actor, note=""):
    run("INSERT INTO history(event_id,ts,status,actor,note) VALUES(?,?,?,?,?)", (eid, now(), status, actor, note))

def create_event(ctx, f, evidence, summary, uncertainty, model):
    cut = (dt.datetime.utcnow() - dt.timedelta(minutes=T["suppress_minutes"])).isoformat(timespec="seconds")
    lab = json.dumps(f["object"]["label"])[1:-1]
    lab_safe = lab.replace("\\", "\\\\").replace("%", "\\%").replace("_", "\\_")
    d = df("SELECT event_id FROM events WHERE gate=? AND rule_id=? AND status!='Suppressed' AND ts>=? AND evidence LIKE ? ESCAPE '\\'", (ctx["gate"], f["rule"]["id"], cut, f'%"label": "{lab_safe}"%'))
    dup = d.event_id[0] if len(d) else ""
    eid, r = "EV-" + uuid.uuid4().hex[:6].upper(), f["rule"]
    row = dict(event_id=eid, ts=now(), gate=ctx["gate"], camera=ctx["camera"], phase=ctx["phase"], phase_conf=ctx["phase_conf"],
               event_type=f"{r['id']} {r['name']}", rule_id=r["id"], severity=f["severity"], confidence=f["confidence"], evidence=json.dumps(evidence),
               status="Suppressed" if dup else "Presented", summary=summary, uncertainty=uncertainty,
               checks=json.dumps(CFG["recommendations"][r["id"]]), notes="", resolution="", model_version=model, rule_version=CFG["rule_version"], duplicate_of=dup)
    run(f"INSERT INTO events({','.join(COLS)}) VALUES({','.join('?' * len(COLS))})", [row[c] for c in COLS])
    log(eid, "Detected", "system", f["reason"]); log(eid, "Validated", "system", "guardrails passed")
    log(eid, row["status"], "system", f"duplicate of {dup} within {T['suppress_minutes']} min" if dup else "")
    return eid, row["status"], dup

def transition(eid, new, actor, note="", reason=""):
    cur = df("SELECT status FROM events WHERE event_id=?", (eid,)).status[0]
    if new not in NEXT[cur]: raise ValueError(f"{cur} -> {new} not allowed")
    if new == "Dismissed" and not reason: raise ValueError("Select a dismissal reason")
    if new == "Resolved" and not note.strip(): raise ValueError("Add a note describing how it was resolved")
    run("UPDATE events SET status=?, resolution=CASE WHEN ?!='' THEN ? ELSE resolution END, notes=notes||? WHERE event_id=?",
        (new, reason, reason, f"[{now()} {actor}] {note}\n" if note else "", eid))
    log(eid, new, actor, (reason + " " + note).strip())

def efficiency_kpis():
    """Returns operational KPI dict for the Operations & Efficiency dashboard."""
    ev_all = df("SELECT severity, status, resolution FROM events")
    hist = df("SELECT event_id, ts, status FROM history")
    avg_ack_min = None
    if not hist.empty:
        presented = hist[hist.status == "Presented"][["event_id", "ts"]].rename(columns={"ts": "ts_p"})
        acked = hist[hist.status == "Acknowledged"][["event_id", "ts"]].rename(columns={"ts": "ts_a"})
        merged = presented.merge(acked, on="event_id")
        if not merged.empty:
            merged["mins"] = (pd.to_datetime(merged.ts_a) - pd.to_datetime(merged.ts_p)).dt.total_seconds() / 60
            avg_ack_min = round(float(merged.mins.mean()), 1)
    rev = ev_all[ev_all.status.isin({"Acknowledged", "Escalated", "Resolved", "Closed", "Dismissed"})]
    reviewed = len(rev)
    false_positives = int(rev.resolution.str.startswith("False positive").sum()) if reviewed else 0
    precision_pct = int(round(100 * (1 - false_positives / reviewed))) if reviewed else None
    hc = int(ev_all.severity.isin(["High", "Critical"]).sum())
    return dict(avg_ack_min=avg_ack_min, precision_pct=precision_pct, high_critical=hc,
                cost_savings_usd=hc * 4200, reviewed=reviewed, false_positives=false_positives)

_DEMO_IMG_BASE = "https://storage.googleapis.com/rampguard-ai-511716-media/demo"
_DEMO_IMGS = {
    "SE-001": f"{_DEMO_IMG_BASE}/se001.jpg",
    "SE-002": f"{_DEMO_IMG_BASE}/se002.jpg",
    "SE-003": f"{_DEMO_IMG_BASE}/se003.jpg",
    "SE-004": f"{_DEMO_IMG_BASE}/se004.jpg",
}

def seed_demo():
    """Insert sample demo events. Clears existing demo events first so re-clicking is idempotent."""
    run("DELETE FROM history WHERE event_id LIKE 'DM-%'")
    run("DELETE FROM events WHERE event_id LIKE 'DM-%'")
    import random
    _demos = [
        ("SE-001","Safety-zone intrusion","Critical","Fueling","Ground crew member near fuel point"),
        ("SE-002","Unsafe GSE proximity","High","Fueling","Fuel bowser in clearance zone"),
        ("SE-002","Unsafe GSE proximity","High","Baggage Handling","Belt loader in clearance zone"),
        ("SE-003","Obstruction (FOD candidate)","Medium","Turnaround Completion","Traffic cone on taxiway"),
        ("SE-004","Restricted-area personnel","High","Boarding","Ramp agent in restricted zone"),
        ("SE-001","Safety-zone intrusion","Critical","Fueling","Technician inside fueling boundary"),
        ("SE-002","Unsafe GSE proximity","Critical","Fueling","Hydrant cart too close to fuselage"),
        ("SE-003","Obstruction (FOD candidate)","High","Pushback Preparation","Ground support cable on ramp"),
    ]
    _pick = ["Resolved", "Acknowledged", "Dismissed", "Presented", "Escalated"]
    for rule_id, rule_name, severity, phase, obj_label in _demos:
        eid = "DM-" + uuid.uuid4().hex[:6].upper()
        _st = random.choice(_pick)
        _img = _DEMO_IMGS.get(rule_id, "")
        row = dict(event_id=eid, ts=now(), gate=CFG["gate"]["id"], camera=CFG["gate"]["camera"],
                   phase=phase, phase_conf=0.9, event_type=f"{rule_id} {rule_name}",
                   rule_id=rule_id, severity=severity, confidence=0.85,
                   evidence=json.dumps({"image": _img} if _img else {}),
                   status=_st, summary=f"{rule_name}: {obj_label} during {phase}",
                   uncertainty="Demo scenario event", checks=json.dumps(CFG["recommendations"].get(rule_id, [])),
                   notes="[DEMO]", resolution="False positive - routine activity" if _st == "Dismissed" else "",
                   model_version="demo", rule_version=CFG["rule_version"], duplicate_of="")
        run(f"INSERT OR IGNORE INTO events({','.join(COLS)}) VALUES({','.join('?'*len(COLS))})", [row[c] for c in COLS])
        log(eid, "Detected", "system", "[DEMO] seeded event")
        log(eid, _st, "demo-seed", "[DEMO] initial status")