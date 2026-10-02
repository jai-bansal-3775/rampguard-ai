import json, uuid, os
import streamlit as st, pandas as pd, plotly.express as px
import db, rules, ai

st.set_page_config("RampGuard AI", "🛫", layout="wide")
db.init()
OPEN = ["Detected", "Under Review", "Acknowledged", "Investigating"]
user = st.sidebar.text_input("Supervisor", "supervisor.1")
st.sidebar.caption("Decision support only. Final decisions stay with qualified personnel.")
st.sidebar.caption(f"AI: {'API key' if os.environ.get('GEMINI_API_KEY') else 'Vertex/ADC'} · {ai.MODEL}")
st.title("🛫 RampGuard AI")
st.caption("Context-aware ground-handling safety intelligence")
t_dash, t_an, t_ev, t_stats = st.tabs(["Operations", "Analyze evidence", "Events & Copilot", "Analytics"])
ta, ev = db.df("SELECT * FROM turnarounds"), db.df("SELECT * FROM events")

with t_dash:
    op = ev[ev.status.isin(OPEN)]
    c = st.columns(4)
    c[0].metric("Active turnarounds", len(ta)); c[1].metric("Open events", len(op))
    c[2].metric("High / Critical open", int(op.severity.isin(["High", "Critical"]).sum()))
    c[3].metric("Total events", len(ev))
    ta["open_events"] = ta.stand_id.map(op.groupby("stand_id").size()).fillna(0).astype(int)
    st.dataframe(ta.rename(columns={"clearance_m": "clearance (m)"}), hide_index=True, use_container_width=True)
    if len(op):
        st.subheader("High-priority panel")
        st.dataframe(op[op.severity.isin(["High", "Critical"])][["event_id", "ts", "aircraft_id", "stand_id", "turnaround_phase", "event_type", "severity", "status"]],
                     hide_index=True, use_container_width=True)
    st.plotly_chart(px.bar(ev.groupby("severity").size().reset_index(name="count"), x="severity", y="count",
                           category_orders={"severity": rules.SEV}, title="Events by severity"), use_container_width=True)

with t_an:
    lab = {f"{r.aircraft_id} · {r.stand_id} · {r.phase}": r for r in ta.itertuples()}
    r = lab[st.selectbox("Aircraft / stand", list(lab))]
    phase = st.selectbox("Current turnaround phase", rules.PHASES, index=rules.PHASES.index(r.phase))
    clr = st.number_input("Configured clearance (m)", value=float(r.clearance_m), step=0.5)
    ctx = dict(aircraft_id=r.aircraft_id, stand_id=r.stand_id, phase=phase, gse=r.gse, clearance_m=clr)
    scen = st.selectbox("Fallback / demo scenario (used if no image or Gemini fails)", list(ai.PRESETS))
    up = st.file_uploader("Ramp image", type=["jpg", "jpeg", "png"])
    if up: st.image(up, width=420)
    if st.button("Analyze", type="primary"):
        data = up.getvalue() if up else None
        with st.spinner("Gemini analyzing…"):
            obs, src = ai.analyze(data, up.type if up else None, ctx, scen)
        (st.success if src == "gemini" else st.warning)(f"Observation source: {src}")
        st.json(obs)
        path = ""
        if data:
            path = f"data/evidence/{uuid.uuid4().hex[:8]}_{up.name}"; open(path, "wb").write(data)
        found = rules.evaluate(obs, ctx)
        if not found: st.info("No rule triggered for this scene and context.")
        for f in found:
            x = ai.explain(ctx, f)
            eid = db.create_event(ctx, f, {"image": path, "source": src, "object": f["object"], "scene": obs["scene_summary"]}, x["summary"], x["checks"])
            st.error(f"{eid} · {f['event_type']} · {f['severity']} — see Events tab")

with t_ev:
    f1, f2 = st.columns(2)
    ss = f1.multiselect("Status", db.NEXT.keys(), default=OPEN); sv = f2.multiselect("Severity", rules.SEV, default=rules.SEV)
    fe = ev[ev.status.isin(ss) & ev.severity.isin(sv)].sort_values("ts", ascending=False)
    st.dataframe(fe[["event_id", "ts", "stand_id", "turnaround_phase", "event_type", "severity", "confidence", "status"]], hide_index=True, use_container_width=True)
    if len(fe):
        e = ev[ev.event_id == st.selectbox("Open event", fe.event_id)].iloc[0]
        evd = json.loads(e.evidence or "{}")
        a, b = st.columns([1, 1])
        with a:
            st.subheader(e.event_type); st.write(f"**{e.severity}** · confidence {e.confidence:.0%} · {e.status}")
            st.write(f"Aircraft {e.aircraft_id} · Stand {e.stand_id} · Phase {e.turnaround_phase}")
            st.write(f"**Rule:** {e.rule_triggered}"); st.write(f"**Why flagged:** {e.ai_summary}")
            st.write("**Suggested checks:**"); [st.write(f"- {c}") for c in json.loads(e.recommended_checks or "[]")]
            if evd.get("image") and os.path.exists(evd["image"]): st.image(evd["image"], width=380)
            if evd.get("object"): st.caption(f"Evidence ({evd.get('source')}): {evd['object']}")
            if e.notes: st.text(e.notes)
        with b:
            st.subheader("Safety Copilot")
            sim = ev[(ev.stand_id == e.stand_id) & (ev.event_type == e.event_type) & (ev.event_id != e.event_id)]
            sim_txt = f"{len(sim)} similar events at stand {e.stand_id}: " + ", ".join(sim.event_id.head(5))
            qs = ["Why was this flagged?", "What should I check?", "Generate incident summary", "Why is this high priority?"]
            cols = st.columns(2); q = None
            for i, t in enumerate(qs):
                if cols[i % 2].button(t, key=f"q{i}"): q = t
            if st.button("Show similar events"): st.write(sim_txt); st.dataframe(sim[["event_id", "ts", "status"]], hide_index=True)
            custom = st.text_input("Ask Copilot")
            if custom: q = custom
            if q:
                with st.spinner("Thinking…"): st.markdown(ai.copilot(e, q, sim_txt))
        st.subheader("Lifecycle")
        nxt = db.NEXT[e.status]
        if nxt:
            act = st.radio("Move to", nxt, horizontal=True); note = st.text_input("Note"); res = st.text_input("Resolution (required to resolve)")
            if st.button("Apply"):
                try: db.transition(e.event_id, act, user, note, res); st.rerun()
                except ValueError as ex: st.error(str(ex))
        st.dataframe(db.df("SELECT ts,status,actor,note FROM history WHERE event_id=? ORDER BY id", (e.event_id,)), hide_index=True, use_container_width=True)

with t_stats:
    c1, c2 = st.columns(2)
    c1.plotly_chart(px.bar(ev.groupby("turnaround_phase").size().reset_index(name="count"), x="turnaround_phase", y="count", title="By phase"), use_container_width=True)
    c2.plotly_chart(px.bar(ev.groupby("stand_id").size().reset_index(name="count"), x="stand_id", y="count", title="By stand"), use_container_width=True)
    h = db.df("SELECT event_id,ts,status FROM history"); d0 = h[h.status == "Detected"].set_index("event_id").ts; a0 = h[h.status == "Acknowledged"].set_index("event_id").ts
    mins = (pd.to_datetime(a0) - pd.to_datetime(d0)).dt.total_seconds().dropna() / 60
    st.metric("Avg acknowledgement time (min)", f"{mins.mean():.1f}" if len(mins) else "n/a")
    st.subheader("Recurring-risk intelligence")
    g = ev.groupby(["stand_id", "turnaround_phase", "event_type"]).size().reset_index(name="count"); g = g[g["count"] >= 3]
    for x in g.itertuples():
        st.warning(f"{x.event_type} events have been observed {x.count} times during {x.turnaround_phase} at stand {x.stand_id}. "
                   "This is an observed historical pattern, not a determined cause.")
    if g.empty: st.info("No recurring patterns yet.")
