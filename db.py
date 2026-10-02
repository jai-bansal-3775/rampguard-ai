import sqlite3, json, os, uuid, datetime as dt, random
import pandas as pd
DB = os.environ.get("RG_DB", "data/rampguard.db")
os.makedirs(os.path.dirname(DB), exist_ok=True)
os.makedirs("data/evidence", exist_ok=True)
NEXT = {"Detected": ["Under Review", "Dismissed"], "Under Review": ["Acknowledged", "Dismissed"],
        "Acknowledged": ["Investigating", "Resolved", "Dismissed"],
        "Investigating": ["Resolved", "Dismissed"], "Resolved": [], "Dismissed": []}

def now(): return dt.datetime.utcnow().isoformat(timespec="seconds")
def conn():
    c = sqlite3.connect(DB); c.row_factory = sqlite3.Row; return c
def df(sql, args=()):
    with conn() as c: return pd.read_sql_query(sql, c, params=args)
def run(sql, args=()):
    with conn() as c: c.execute(sql, args)

def init():
    with conn() as c:
        c.executescript("""
        CREATE TABLE IF NOT EXISTS turnarounds(id INTEGER PRIMARY KEY, aircraft_id TEXT, stand_id TEXT,
            phase TEXT, gse TEXT, clearance_m REAL);
        CREATE TABLE IF NOT EXISTS events(event_id TEXT PRIMARY KEY, ts TEXT, aircraft_id TEXT, stand_id TEXT,
            turnaround_phase TEXT, event_type TEXT, severity TEXT, confidence REAL, evidence TEXT,
            rule_triggered TEXT, status TEXT, notes TEXT, resolution TEXT, ai_summary TEXT,
            recommended_checks TEXT, gse_type TEXT);
        CREATE TABLE IF NOT EXISTS history(id INTEGER PRIMARY KEY, event_id TEXT, ts TEXT, status TEXT,
            actor TEXT, note TEXT);""")
        if c.execute("SELECT COUNT(*) FROM turnarounds").fetchone()[0] == 0: seed(c)

def seed(c):
    for r in [("UA146", "C22", "Baggage Handling", "Baggage Vehicle", 3.0), ("SQ322", "A05", "Boarding", "Passenger Stairs", 2.5),
              ("AI101", "B14", "Fueling", "Fuel Truck", 5.0), ("EK510", "C07", "Turnaround Completion", "Tug", 3.0),
              ("LH760", "A11", "Catering", "Catering Truck", 3.0)]:
        c.execute("INSERT INTO turnarounds(aircraft_id,stand_id,phase,gse,clearance_m) VALUES(?,?,?,?,?)", r)
    random.seed(7); base = dt.datetime.utcnow()
    spec = [("C22", "Baggage Handling", "SE-002 Unsafe GSE proximity", "Baggage Vehicle", "High")] * 7 + [
        ("A05", "Boarding", "SE-004 Restricted-area personnel", "Passenger Stairs", "High"),
        ("B14", "Fueling", "SE-001 Safety-zone intrusion", "Fuel Truck", "Critical"),
        ("C07", "Turnaround Completion", "SE-003 Obstruction", "Tug", "Medium"),
        ("A11", "Catering", "SE-002 Unsafe GSE proximity", "Catering Truck", "High")]
    for i, (st_, ph, et, gse, sev) in enumerate(spec):
        ts = base - dt.timedelta(days=30 - i * 2, minutes=random.randint(0, 600)); eid = "EV-" + uuid.uuid4().hex[:6].upper()
        status = "Resolved" if i % 3 else "Dismissed"
        c.execute("INSERT INTO events VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
                  (eid, ts.isoformat(timespec="seconds"), "HIST", st_, ph, et, sev, 0.85, "{}", "historical seed", status,
                   "", "Verified and cleared" if status == "Resolved" else "False positive", "Historical event.", "[]", gse))
        c.execute("INSERT INTO history(event_id,ts,status,actor,note) VALUES(?,?,?,?,?)", (eid, ts.isoformat(timespec="seconds"), "Detected", "system", ""))
        c.execute("INSERT INTO history(event_id,ts,status,actor,note) VALUES(?,?,?,?,?)",
                  (eid, (ts + dt.timedelta(minutes=random.randint(3, 15))).isoformat(timespec="seconds"), "Acknowledged", "seed", ""))

def create_event(ctx, f, evidence, summary, checks):
    eid = "EV-" + uuid.uuid4().hex[:6].upper(); t = now()
    run("INSERT INTO events VALUES(?,?,?,?,?,?,?,?,?,?,?,?,?,?,?,?)",
        (eid, t, ctx["aircraft_id"], ctx["stand_id"], ctx["phase"], f["event_type"], f["severity"], f["confidence"],
         json.dumps(evidence), f["rule"], "Detected", "", "", summary, json.dumps(checks), ctx.get("gse", "")))
    run("INSERT INTO history(event_id,ts,status,actor,note) VALUES(?,?,?,?,?)", (eid, t, "Detected", "RampGuard", f["rule"]))
    return eid

def transition(eid, new, actor, note="", resolution=""):
    cur = df("SELECT status,severity FROM events WHERE event_id=?", (eid,)).iloc[0]
    if new not in NEXT[cur.status]: raise ValueError(f"{cur.status} -> {new} not allowed")
    if new == "Resolved" and not resolution.strip(): raise ValueError("Resolution is required (BR-006)")
    if new == "Dismissed" and cur.severity in ("High", "Critical") and not note.strip():
        raise ValueError("High/Critical events need a dismissal note (BR-005)")
    run("UPDATE events SET status=?, resolution=CASE WHEN ?!='' THEN ? ELSE resolution END WHERE event_id=?", (new, resolution, resolution or new, eid))
    if note: run("UPDATE events SET notes=COALESCE(notes,'')||? WHERE event_id=?", (f"[{now()} {actor}] {note}\n", eid))
    run("INSERT INTO history(event_id,ts,status,actor,note) VALUES(?,?,?,?,?)", (eid, now(), new, actor, note))
