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
    d = df("SELECT event_id FROM events WHERE gate=? AND rule_id=? AND status!='Suppressed' AND ts>=?", (ctx["gate"], f["rule"]["id"], cut))
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