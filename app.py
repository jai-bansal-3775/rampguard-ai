import json, uuid, os
import streamlit as st, pandas as pd, plotly.express as px
import db, rules, ai, geo
from rules import CFG, T

st.set_page_config("RampGuard AI", "🛫", layout="wide")
db.init()
st.markdown("""<style>
.block-container{padding-top:2rem;max-width:1250px}
.hero{background:linear-gradient(90deg,#0b3a66,#1565c0);color:#fff;padding:18px 24px;border-radius:12px;margin-bottom:12px}
.hero h1{margin:0;font-size:1.9rem}.hero p{margin:2px 0 0;opacity:.85}
.badge{display:inline-block;padding:2px 10px;border-radius:12px;font-weight:700;font-size:.8rem;color:#111}
.Critical{background:#ff6b6b}.High{background:#ffa94d}.Medium{background:#ffe066}.Low{background:#74c0fc}
.meta{color:#9aa4b2;font-size:.85rem}
button:focus-visible{outline:3px solid #ffd43b!important}
</style>""", unsafe_allow_html=True)
ICON = {"Critical": "🟥", "High": "🟧", "Medium": "🟨", "Low": "🟦"}
badge = lambda s: f'<span class="badge {s}">{ICON[s]} {s.upper()}</span>'
OPEN = ["Presented", "Acknowledged", "Escalated"]
S = st.session_state
user = st.sidebar.text_input("Supervisor", "supervisor.1")
st.sidebar.warning("Decision support only. It does not replace local procedures, trained judgment, or authorized operational control.")
st.sidebar.caption(f"Model: {ai.MODEL} · Rules: {CFG['rule_version']}")
st.sidebar.caption("✅ Gemini API key loaded" if os.environ.get("GEMINI_API_KEY") else "❌ No Gemini API key found. Create a .env file (see .env.example)")
st.markdown(f'<div class="hero"><h1>🛫 RampGuard AI</h1><p>Context-aware ground-handling safety console · Gate {CFG["gate"]["id"]}</p></div>', unsafe_allow_html=True)

ev = db.df("SELECT * FROM events")  # always loaded fresh on every run
t_con, t_an, t_zone, t_stat, t_cfg = st.tabs(["Alert console", "Analyze evidence", "Zone setup", "Analytics", "Configuration"])

with t_an:
    st.write(f"Gate **{CFG['gate']['id']}** · camera `{CFG['gate']['camera']}`")
    c1, c2 = st.columns(2)
    mode = c1.radio("Turnaround phase", ["Auto-detect (Gemini)", "Manual override"], horizontal=True)
    zsrc = c2.selectbox("Safety zones", ["Derived from detected aircraft (recommended)", "Saved fixed-camera zones", "Gemini-proposed polygons"])
    phase_ov = st.selectbox("Phase", CFG["phases"]) if mode.startswith("Manual") else None
    up = st.file_uploader(f"Ramp images, or one short video (max {CFG['video']['max_mb']} MB). 2+ frames or a video confirm persistence.",
                          type=["jpg", "jpeg", "png", "mp4", "mov", "webm"], accept_multiple_files=True)
    vid = next((u for u in up if u.type.startswith("video")), None); imgs_up = [u for u in up if not u.type.startswith("video")]
    if vid: st.video(vid.getvalue())
    elif up: st.image([u.getvalue() for u in up], width=220)
    if st.button("Analyze", type="primary", disabled=not up):
        try:
            with st.spinner("Gemini analyzing…"):
                vpath = None
                if vid:
                    if len(vid.getvalue()) > CFG["video"]["max_mb"] * 1e6: raise ai.AIError(f"Video larger than {CFG['video']['max_mb']} MB: trim or compress it.")
                    os.makedirs("data/video", exist_ok=True); vpath = f"data/video/{uuid.uuid4().hex[:8]}_{vid.name}"; open(vpath, "wb").write(vid.getvalue())
                    dur = geo.duration(vpath)
                    if not dur: raise ai.AIError("Could not read the video file (try MP4 / H.264).")
                    a = ai.analyze_video(vid.getvalue(), vid.type, dur); imgs = [geo.frame_at(vpath, t) for t in a["times"]]
                else:
                    fr = [(u.getvalue(), u.type) for u in imgs_up]; a = ai.analyze(fr); imgs = [b for b, _ in fr]; a["times"] = [None] * len(imgs)
            ok = [i for i, im in enumerate(imgs) if im]
            if not ok: raise ai.AIError("No usable frames were extracted.")
            a["frames"], a["times"], imgs = [a["frames"][i] for i in ok], [a["times"][i] for i in ok], [imgs[i] for i in ok]
            ph, pc = (phase_ov, 1.0) if phase_ov else (a["phase"], a["phase_confidence"])
            res = {"analysis": a, "phase": ph, "pc": pc, "items": []}
            if ph == "Unknown" or pc < T["phase_confidence"]:
                res["note"] = "Phase could not be determined confidently. Phase-specific rules were not applied (safety first); choose Manual override if you know the phase."
                ph = "Unknown"
            zones = [geo.derive_zones(f["objects"], CFG["derived_zones"]) for f in a["frames"]] if zsrc.startswith("Derived") else geo.load_zones() if zsrc.startswith("Saved") else a["zones"]
            zl = zones if isinstance(zones, list) else [zones] * len(imgs)
            if not any(len(p) >= 3 for z in zl for p in z.values()): res["zone_note"] = "No safety zone available (aircraft not detected or no saved zones): zone-based rules could not be evaluated."
            rid = uuid.uuid4().hex[:8]; res["moments"] = []
            for i, im in enumerate(imgs):
                mp = f"data/evidence/{rid}_m{i}.png"; open(mp, "wb").write(geo.annotate(im, a["frames"][i]["objects"], zl[i]))
                cnt = {}; [cnt.__setitem__(o["type"], cnt.get(o["type"], 0) + 1) for o in a["frames"][i]["objects"]]
                res["moments"].append((a["times"][i], mp, ", ".join(f"{v} {k}" for k, v in cnt.items()) or "nothing detected"))
            res["preview"] = res["moments"][0][1]
            ctx = dict(gate=CFG["gate"]["id"], camera=CFG["gate"]["camera"], phase=ph, phase_conf=pc)
            for f in rules.evaluate(a["frames"], ph, zones):
                if not f["eligible"]: res["items"].append(("held", f["rule"]["name"], f["held_reason"])); continue
                fi, t = f["frame"], a["times"][f["frame"]]
                if t is not None: f["reason"] += f" at {int(t) // 60}:{int(t) % 60:02d} of the clip"
                img = geo.annotate(imgs[fi], a["frames"][fi]["objects"], zl[fi], [o["label"] for o in f["objects"]])
                v = {"supported": True, "confidence": None, "reason": "Verifier disabled in config"}
                if T["verify"]:
                    try: v = ai.verify(img, f, ph)
                    except ai.AIError as e: v = {"supported": True, "confidence": None, "reason": f"Verifier unavailable ({e}); alert kept for safety"}
                if not v["supported"]: res["items"].append(("held", f["rule"]["name"], "verifier rejected: " + v["reason"])); continue
                path = f"data/evidence/{uuid.uuid4().hex[:8]}.png"; open(path, "wb").write(img)
                summ, unc = ai.explain(ctx, f)
                eid, stt, dup = db.create_event(ctx, f, {"image": path, "object": f["object"], "scene": a["scene_summary"], "phase_evidence": a["phase_evidence"], "verifier": v, "t": t, "video": vpath}, summ, unc, ai.MODEL)
                res["items"].append((stt, eid, f"{f['rule']['id']} {f['severity']}" + (f" · duplicate of {dup}" if dup else "")))
            S.last = res
        except ai.AIError as e:
            S.last = {"error": str(e)}
        st.rerun()  # reload so the console shows the new events immediately
    L = S.get("last")
    if L and "error" in L: st.error(f"AI analysis unavailable (degraded mode, nothing was fabricated): {L['error']}")
    elif L:
        a = L["analysis"]
        st.info(f"**Detected phase:** {L['phase']} ({L['pc']:.0%}) · {a['phase_evidence']}")
        st.caption(a["scene_summary"] + (f" · {len(a['frames'])} analysed moments" if a["times"][0] is not None else ""))
        for k in ("note", "zone_note"):
            if L.get(k): st.warning(L[k])
        with st.expander(f"All analysed moments ({len(L['moments'])}): green = detections, shaded = zones", expanded=True):
            for i in range(0, len(L["moments"]), 3):
                for col, (t, mp, txt) in zip(st.columns(3), L["moments"][i:i + 3]):
                    if os.path.exists(mp): col.image(mp, caption=(f"{int(t) // 60}:{int(t) % 60:02d} · " if t is not None else "") + txt)
        for k, n, d in L["items"]:
            (st.error if k == "Presented" else st.warning if k in ("Suppressed", "held") else st.info)(f"{'Alert' if k=='Presented' else k}: {n} · {d}")
        if not L["items"]: st.success("No rule triggered for this scene and phase.")
        with st.expander("Raw observations"): st.json({"times": a["times"], "frames": a["frames"]})

with t_zone:
    st.caption("Zones belong to a fixed camera. Coordinates are 0-1000 (x right, y down). Let Gemini suggest, then adjust the points and save.")
    zf = st.file_uploader("Reference frame from the fixed camera", type=["jpg", "jpeg", "png"], key="zf")
    if zf:
        if st.button("Suggest zones with Gemini"):
            try: S.zdraft = ai.analyze([(zf.getvalue(), zf.type)])["zones"]
            except ai.AIError as e: st.error(str(e))
        zd = S.get("zdraft") or geo.load_zones(); edited = {}
        for n in CFG["zones"]["names"]:
            st.write(f"**{n}**: {CFG['zones']['descriptions'][n]}")
            e = st.data_editor(pd.DataFrame(zd.get(n, []), columns=["x", "y"]).astype(float), num_rows="dynamic", key=f"z{n}{hash(str(zd.get(n)))}")
            edited[n] = e.dropna().values.tolist()
        st.image(geo.annotate(zf.getvalue(), [], edited), caption="Zone preview")
        if st.button("Save as fixed-camera zones"): geo.save_zones(edited); st.success("Saved to data/zones.json")

with t_con:
    op = ev[ev.status.isin(OPEN)]
    m = st.columns(4)
    m[0].metric("Open alerts", len(op)); m[1].metric("Critical / High", int(op.severity.isin(["Critical", "High"]).sum()))
    m[2].metric("Suppressed duplicates", int((ev.status == "Suppressed").sum()))
    h = db.df("SELECT event_id,ts,status FROM history"); d0 = h[h.status == "Presented"].groupby("event_id").ts.min(); a0 = h[h.status == "Acknowledged"].groupby("event_id").ts.min()
    mins = ((pd.to_datetime(a0) - pd.to_datetime(d0)).dt.total_seconds() / 60).dropna()
    m[3].metric("Avg ack time (min)", f"{mins.mean():.1f}" if len(mins) else "n/a")
    f1, f2 = st.columns(2)
    ss = f1.multiselect("Status", list(db.NEXT), default=OPEN); sv = f2.multiselect("Severity", CFG["severities"], default=CFG["severities"])
    q = ev[ev.status.isin(ss) & ev.severity.isin(sv)].copy()
    q["r"] = q.severity.map({s: i for i, s in enumerate(CFG["severities"])}); q = q.sort_values(["r", "ts"], ascending=[False, False])
    if q.empty: st.info("No alerts match. Analyze an image to generate events.")
    for r in q.head(15).itertuples():
        with st.container(border=True):
            c = st.columns([1.2, 5, 1])
            c[0].markdown(badge(r.severity), unsafe_allow_html=True)
            c[1].markdown(f"**{r.event_type}** · {r.status}<br><span class='meta'>{r.ts} · {r.phase} · confidence {r.confidence:.0%} · {r.event_id}</span>", unsafe_allow_html=True)
            if c[2].button("Open", key="o" + r.event_id): S.sel = r.event_id
    if S.get("sel") in set(ev.event_id):
        e = ev[ev.event_id == S.sel].iloc[0]; evd = json.loads(e.evidence or "{}")
        st.divider(); st.markdown(f"### {e.event_type} &nbsp; {badge(e.severity)}", unsafe_allow_html=True)
        a, b = st.columns(2)
        with a:
            if evd.get("image") and os.path.exists(evd["image"]): st.image(evd["image"], caption="Evidence frame")
            if evd.get("video") and os.path.exists(evd["video"]):
                st.video(evd["video"], start_time=int(evd.get("t") or 0)); st.caption(f"Original clip, starting at the alert moment ({int(evd.get('t') or 0)} s)")
            st.write(f"**When/where:** {e.ts} · Gate {e.gate} · {e.camera}")
            st.write(f"**Context:** phase {e.phase} ({e.phase_conf:.0%}). {evd.get('phase_evidence', '')}")
            st.write(f"**Why flagged:** {e.summary}"); st.write(f"**Uncertainty:** {e.uncertainty}")
            if evd.get("verifier"): st.write(f"**Verifier:** {'supported' if evd['verifier']['supported'] else 'rejected'} · {evd['verifier']['reason']}")
            st.write("**Suggested human checks:**"); [st.write(f"- {c}") for c in json.loads(e.checks)]
            st.caption(f"Confidence {e.confidence:.0%} · model {e.model_version} · rules {e.rule_version}" + (f" · duplicate of {e.duplicate_of}" if e.duplicate_of else ""))
        with b:
            nxt = db.NEXT[e.status]
            if nxt:
                st.subheader("Disposition"); note = st.text_input("Note", key="n" + e.event_id)
                rs = st.selectbox("Dismissal reason", CFG["dismiss_reasons"], key="r" + e.event_id) if "Dismissed" in nxt else ""
                cols = st.columns(len(nxt))
                for i, n in enumerate(nxt):
                    if cols[i].button(n, key=f"{e.event_id}{n}"):
                        try: db.transition(e.event_id, n, user, note, rs if n == "Dismissed" else ""); st.rerun()
                        except ValueError as ex: st.error(str(ex))
            st.subheader("Safety Copilot")
            sim = ev[(ev.rule_id == e.rule_id) & (ev.event_id != e.event_id)]; simt = f"{len(sim)} other {e.rule_id} events: " + ", ".join(sim.event_id.head(5))
            for i, t in enumerate(["Why was this flagged?", "What should I check?", "Generate incident summary", "Why is this priority " + e.severity + "?"]):
                if st.button(t, key=f"c{i}{e.event_id}"):
                    try: S["cop" + e.event_id] = ai.copilot(e, t, simt)
                    except ai.AIError as ex: S["cop" + e.event_id] = f"Copilot unavailable (degraded mode): {ex}"
            if S.get("cop" + e.event_id): st.markdown(S["cop" + e.event_id])
            st.dataframe(db.df("SELECT ts,status,actor,note FROM history WHERE event_id=? ORDER BY id", (e.event_id,)), hide_index=True, use_container_width=True)

with t_stat:
    if ev.empty: st.info("No data yet.")
    else:
        c1, c2 = st.columns(2)
        c1.plotly_chart(px.bar(ev.groupby(["phase", "severity"]).size().reset_index(name="count"), x="phase", y="count", color="severity", title="Alerts by phase"), use_container_width=True)
        c2.plotly_chart(px.pie(ev.groupby("status").size().reset_index(name="count"), names="status", values="count", title="Disposition"), use_container_width=True)
        rev = ev[ev.status.isin(["Acknowledged", "Escalated", "Resolved", "Closed", "Dismissed"])]
        fp = int(rev.resolution.str.startswith("False positive").sum()); st.metric("Alert precision (reviewed alerts)", f"{(1 - fp / len(rev)):.0%}" if len(rev) else "n/a", f"{fp} false positives of {len(rev)} reviewed")
        g = ev[ev.status != "Suppressed"].groupby(["gate", "rule_id", "phase"]).size().reset_index(name="count")
        for x in g[g["count"] >= 3].itertuples(): st.warning(f"{x.rule_id} observed {x.count} times in phase '{x.phase}' at gate {x.gate}. Observed pattern, not a determined cause.")

with t_cfg:
    st.caption("All rules, thresholds, phases and the recommendation catalogue live in config.json (no values are hardcoded in code).")
    st.json(CFG)