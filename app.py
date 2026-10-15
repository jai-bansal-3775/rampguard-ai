import sys as _sys, asyncio as _asyncio
if _sys.platform == "win32":
    _asyncio.set_event_loop_policy(_asyncio.WindowsSelectorEventLoopPolicy())
import json, uuid, os
import queue as _lq, threading as _lt
import streamlit as st, pandas as pd, plotly.express as px
import db, rules, ai, geo, storage as stor
from rules import CFG, T

_IN_CLOUD = bool(os.environ.get("K_SERVICE"))  # True when running on Cloud Run

@st.cache_resource
def _live_comms():
    """Persistent live-analysis channels — survive Streamlit reruns."""
    return _lq.Queue(maxsize=1), _lt.Event(), _lq.Queue(maxsize=5)

_live_result_q, _live_busy, _live_error_q = _live_comms()


def _live_worker_fn(png):
    """Background AI worker. Always clears _live_busy; deposits result on success."""
    try:
        d = ai.detect_frame(png)
        if d is not None:
            try:
                _live_result_q.put_nowait({"d": d, "png": png})
            except _lq.Full:
                pass
    except Exception as _e:
        try:
            _live_error_q.put_nowait(f"Detection error: {str(_e)[:120]}")
        except Exception:
            pass
    finally:
        _live_busy.clear()


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
st.sidebar.caption(f"Vision: `{ai.MODEL}` · Reasoning: `{ai.REASONING_MODEL}` · Rules: {CFG['rule_version']}")
st.sidebar.caption(f"✅ Vertex AI · {os.environ.get('GOOGLE_CLOUD_PROJECT', '❌ project not set')} · {os.environ.get('GOOGLE_CLOUD_LOCATION', 'us-central1')}")
st.sidebar.markdown("---")
st.sidebar.caption("**Theme:** Manufacturing · Intelligent Operations & Industrial Efficiency")
st.sidebar.caption("🏭 Turnaround = production cycle · Phase = process step · Safety rule = quality gate")
st.sidebar.markdown("**Google Cloud stack:**")
st.sidebar.caption("☁️ Vertex AI · Gemini 2.5 Flash · Google ADK · Cloud Run · Cloud Storage · Google Search Grounding")
if st.sidebar.button("Load Demo Scenario", help="Replaces any existing demo events with 8 synthetic records (DM- prefix). Real AI-generated events are not affected.", use_container_width=True):
    db.seed_demo()
    st.sidebar.success("Demo scenario loaded! (8 synthetic events, tagged DEMO)")
    st.rerun()
if st.sidebar.button("Clear Demo Data", help="Removes all synthetic demo events (DM- prefix). Real AI events are kept.", use_container_width=True):
    db.run("DELETE FROM history WHERE event_id LIKE 'DM-%'")
    db.run("DELETE FROM events WHERE event_id LIKE 'DM-%'")
    st.sidebar.success("Demo events removed.")
    st.rerun()
st.markdown(f'''<div class="hero">
<h1>🛫 RampGuard AI — Intelligent Ramp Safety</h1>
<p>Manufacturing Theme: Aircraft Turnaround as a Production Cycle · Gate {CFG["gate"]["id"]}</p>
<p style="font-size:.78rem;opacity:.82;margin-top:6px">
✈️ Turnaround = production cycle &nbsp;·&nbsp;
🔄 Phase = process step &nbsp;·&nbsp;
🛡️ Safety rule = quality gate &nbsp;·&nbsp;
🤖 7-Agent ADK Pipeline &nbsp;·&nbsp;
🔍 Google Search Grounded &nbsp;·&nbsp;
☁️ Vertex AI · Cloud Run · Cloud Storage
</p>
</div>''', unsafe_allow_html=True)

ev = db.df("SELECT * FROM events")  # always loaded fresh on every run
_open_ev = ev[ev.status.isin(["Presented", "Acknowledged", "Escalated"])]
_hc_count = int(ev.severity.isin(["High", "Critical"]).sum())
_m = st.columns(5)
_m[0].metric("Open Alerts", len(_open_ev))
_m[1].metric("High / Critical", _hc_count)
_m[2].metric("Duplicates Suppressed", int((ev.status == "Suppressed").sum()))
_m[3].metric("Est. Cost Savings", f"${_hc_count * 4200:,}", help="@ $4,200/incident (IATA ramp safety benchmark)")
_m[4].metric("AI Stack", "Gemini 2.5 Flash · ADK")
t_con, t_an, t_pipe, t_ops = st.tabs(["🚨 Alert Console", "🔍 Analyze & Replay", "🤖 7-Agent Pipeline", "📊 Operations & KPIs"])

with t_an:
    st.write(f"Gate **{CFG['gate']['id']}** · camera `{CFG['gate']['camera']}`")
    with st.expander("🆚 What makes RampGuard different — Phase-Aware AI vs. Standard CV", expanded=False):
        st.markdown("""| Capability | Standard Computer Vision | **RampGuard AI** |
|---|---|---|
| Rule evaluation | ❌ Same rule regardless of scenario | ✅ Phase-aware: same object = different risk level |
| AI verification | ❌ Single-pass detection | ✅ Dual-pass: Gemini detects → skeptical verifier |
| Multi-agent reasoning | ❌ Single model | ✅ 7-agent Google ADK pipeline with Google Search grounding |
| Proactive intelligence | ❌ Reactive alerts only | ✅ Turnaround brief generated before next phase |
| Duplicate handling | ❌ Repeat alerts flood console | ✅ Time-windowed suppression + persistence check |
| Safety Copilot | ❌ Static checklist | ✅ Conversational AI copilot per event |""")
    with st.expander("🔴 Live Analysis — real-time frame-by-frame detection", expanded=False):
        st.caption("Scans the webcam feed every 3 seconds with Gemini Vision and raises alerts in real time.")
        if _IN_CLOUD:
            st.info("☁️ **Running on Cloud Run** — physical webcam is not available in cloud containers. Use the video upload below to analyze recorded ramp footage.", icon="☁️")
        else:
            lc1, lc2 = st.columns(2)
            live_zsrc = lc1.selectbox("Zones", ["Derived from detected aircraft", "Saved fixed-camera zones"], key="live_zsrc")
            cam_idx = lc2.number_input("Camera index", 0, 5, 0, key="live_cam", help="0 = default webcam")
            b1, b2 = st.columns(2)
            if b1.button("▶ Start live detection", type="primary", key="live_go", disabled=S.get("live_running", False)):
                S.live_running = True; S.live_seen = set(); S.live_log = []; S.live_recent_evidence = []
                S.live_last_d = None; S.live_last_ai_png = None; S.live_last_ai_time = 0
                for _q in (_live_result_q, _live_error_q):
                    while not _q.empty():
                        try: _q.get_nowait()
                        except Exception: break
                _live_busy.clear()
                st.rerun()
            if b2.button("⏹ Stop live detection", key="live_stop", disabled=not S.get("live_running", False)):
                S.live_running = False
                _live_busy.clear()
                st.rerun()
        frame_ph = st.empty()   # live video (always the webcam feed)
        alert_ph = st.empty()   # evidence snapshots — appears below video when alerts fire
        status_ph = st.empty()  # status bar
        log_ph = st.empty()     # alert text log
        if S.get("live_running"):
            import cv2, time
            _live_zsrc = S.get("live_zsrc", "Derived from detected aircraft")
            _cam_idx = S.get("live_cam", 0)
            cap = S.get("live_cap")
            if cap is None or not cap.isOpened():
                cap = cv2.VideoCapture(int(_cam_idx)); S.live_cap = cap
            if not cap.isOpened():
                S.live_running = False
                status_ph.error(f"Could not open camera {int(_cam_idx)}. Try another index or upload a video clip.")
            else:
                mode_label = "● LIVE"
                _AI_INTERVAL = 3.0   # background AI call frequency
                _RERUN_SECS = 10.0   # rerun every 10 s so Stop button is responsive
                _ann = " · 🔄 analyzing…" if _live_busy.is_set() else ""
                status_ph.info(f"{mode_label} · live{_ann}  (press Stop to end)")
                # Restore existing evidence + alerts from this session on rerun
                if S.get("live_recent_evidence"):
                    with alert_ph.container():
                        st.caption("Alert evidence")
                        _ec = st.columns(min(len(S.live_recent_evidence), 3))
                        for _i, _ev in enumerate(S.live_recent_evidence):
                            _ec[_i].image(_ev["img"], caption=_ev["caption"], use_container_width=True)
                if S.get("live_log"):
                    with log_ph.container():
                        for _ll in S.live_log: st.error(_ll)
                # ── Continuous display loop — AI in background, zero blocking calls ──
                _last_png = None
                _last_fr = None
                t_start = time.time()
                while time.time() - t_start < _RERUN_SECS:
                    _ok, _fr = cap.read()
                    if not _ok:
                        break
                    _last_fr = _fr
                    _last_png = cv2.imencode(".jpg", _fr, [cv2.IMWRITE_JPEG_QUALITY, 70])[1].tobytes()
                    # Non-blocking: pick up finished AI result
                    try:
                        _item = _live_result_q.get_nowait()
                        S.live_last_d = _item["d"]
                        S.live_last_ai_png = _item["png"]
                        # Alert processing — NO blocking calls (save_evidence is now async)
                        _d2 = _item["d"]; _ai_png = _item["png"]
                        _obs = _d2["objects"]
                        _ph2 = _d2["phase"] if _d2["phase_confidence"] >= T["phase_confidence"] else "Unknown"
                        _zns = geo.derive_zones(_obs, CFG["derived_zones"]) if _live_zsrc.startswith("Derived") else geo.load_zones()
                        _alert_added = False
                        for _f in (rules.evaluate([{"objects": _obs}], _ph2, _zns) if _ph2 != "Unknown" else []):
                            if not _f["eligible"]: continue
                            _key = (_f["rule"]["id"], _f["object"]["label"])
                            if _key in S.live_seen: continue
                            S.live_seen.add(_key)
                            _eimg = geo.annotate(_ai_png, _obs, _zns, [_f["object"]["label"]])
                            _epath = stor.save_evidence(_eimg, f"{uuid.uuid4().hex[:8]}.png")
                            _mtag = ai.MODEL
                            _ctx = dict(gate=CFG["gate"]["id"], camera=CFG["gate"]["camera"] + " (LIVE)",
                                        phase=_ph2, phase_conf=_d2["phase_confidence"])
                            _summ = f"{_f['rule']['name']}: {_f['object']['label']} detected during {_ph2} phase."
                            _unc = "Live single-frame detection; verify in person before escalating."
                            _eid, _, _ = db.create_event(
                                _ctx, _f,
                                {"image": _epath, "object": _f["object"], "scene": "Live stream detection",
                                 "phase_evidence": f"Auto-detected: {_ph2}",
                                 "verifier": {"supported": True, "confidence": _f["confidence"],
                                              "reason": "Live detection"},
                                 "t": None, "video": None, "live": True},
                                _summ, _unc, _mtag)
                            _cap_txt = f"🔴 {_f['rule']['id']} · {_f['object']['label']} · {_f['severity']}"
                            S.live_log.insert(0, f"{_cap_txt} · event {_eid}")
                            _ev_list = S.get("live_recent_evidence", [])
                            _ev_list.insert(0, {"img": _epath, "caption": _cap_txt})
                            S.live_recent_evidence = _ev_list[:3]
                            _alert_added = True
                        if _alert_added:
                            # Update evidence panel below video
                            with alert_ph.container():
                                st.caption("Alert evidence")
                                _ec = st.columns(min(len(S.live_recent_evidence), 3))
                                for _i, _ev in enumerate(S.live_recent_evidence):
                                    _ec[_i].image(_ev["img"], caption=_ev["caption"], use_container_width=True)
                            # Update alert log
                            with log_ph.container():
                                for _ll in S.live_log: st.error(_ll)
                    except _lq.Empty:
                        pass
                    # Show Live session errors in status bar
                    try:
                        _lerr = _live_error_q.get_nowait()
                        status_ph.warning(f"{mode_label} · {_lerr}  (using standard fallback)")
                    except _lq.Empty:
                        pass
                    # Launch background AI if idle and interval elapsed
                    if (not _live_busy.is_set() and
                            time.time() - S.get("live_last_ai_time", 0) >= _AI_INTERVAL):
                        _live_busy.set()
                        S.live_last_ai_time = time.time()
                        _lt.Thread(target=_live_worker_fn,
                                   args=(_last_png,),
                                   daemon=True).start()
                    # Display live feed — always shows webcam, never evidence images
                    _d = S.get("live_last_d")
                    if _d:
                        _objs = _d["objects"]
                        _ph = _d["phase"] if _d["phase_confidence"] >= T["phase_confidence"] else "Unknown"
                        _zones = geo.derive_zones(_objs, CFG["derived_zones"]) if _live_zsrc.startswith("Derived") else geo.load_zones()
                        _lbl = "🔄 analyzing…" if _live_busy.is_set() else (_ph if _ph != "Unknown" else "phase unknown")
                        frame_ph.image(geo.annotate(_last_png, _objs, _zones),
                                       caption=f"{mode_label} · {_lbl}")
                    else:
                        frame_ph.image(cv2.cvtColor(_fr, cv2.COLOR_BGR2RGB),
                                       channels="RGB", caption=f"{mode_label} · warming up…")
                    time.sleep(0.05)
                st.rerun()
        else:
            if S.get("live_cap") is not None:
                try: S.live_cap.release()
                except Exception: pass
                S.live_cap = None
            if S.get("live_recent_evidence"):
                with alert_ph.container():
                    st.caption("Alert evidence")
                    _ec = st.columns(min(len(S.live_recent_evidence), 3))
                    for _i, _ev in enumerate(S.live_recent_evidence):
                        _ec[_i].image(_ev["img"], caption=_ev["caption"], use_container_width=True)
            if S.get("live_log"):
                with log_ph.container():
                    for _ll in S.live_log: st.error(_ll)
                status_ph.success(f"Live detection stopped. {len(S['live_log'])} alert(s) sent to the Alert console.")
    st.markdown("**Or analyze recorded evidence:**")
    c1, c2 = st.columns(2)
    mode = c1.radio("Turnaround phase", ["Auto-detect from image (Gemini)", "Manual override"], horizontal=True)
    zsrc = c2.radio("Safety zones", ["Auto (Gemini proposes per image)", "Saved fixed-camera zones"], horizontal=True)
    phase_ov = st.selectbox("Phase", CFG["phases"]) if mode.startswith("Manual") else None
    up = st.file_uploader("Ramp image(s). Upload 2+ consecutive frames to confirm persistence.", type=["jpg", "jpeg", "png"], accept_multiple_files=True)
    if up: st.image([u.getvalue() for u in up], width=220)
    vid = st.file_uploader("Video clip (optional — overrides images above)", type=["mp4", "mov", "avi"], key="vid")
    if vid: st.video(vid)
    if st.button("Analyze", type="primary", disabled=not (up or vid)):
        frames = [(u.getvalue(), u.type) for u in up]
        try:
            with st.status("Running AI pipeline…", expanded=True) as _status:
                _status.update(label="Step 1/5 — Gemini Vision: extracting frames and detecting objects…")
                vpath = None
                if vid:
                    if len(vid.getvalue()) > CFG["video"]["max_mb"] * 1e6: raise ai.AIError(f"Video larger than {CFG['video']['max_mb']} MB: trim or compress it.")
                    os.makedirs("data/video", exist_ok=True); safe_name = os.path.basename(vid.name); vpath = f"data/video/{uuid.uuid4().hex[:8]}_{safe_name}"; open(vpath, "wb").write(vid.getvalue())
                    dur = geo.duration(vpath)
                    if not dur: raise ai.AIError("Could not read the video file (try MP4 / H.264).")
                    if stor.enabled():
                        _status.update(label="Step 1/5 — Uploading to Cloud Storage…")
                        _gcs_uri = stor.upload_video(vpath)
                        _status.update(label="Step 1/5 — Gemini Vision: analyzing video via Cloud Storage…")
                        try:
                            a = ai.analyze_video_gcs(_gcs_uri, "video/mp4", dur)
                        except ai.AIError:
                            _status.update(label="Step 1/5 — Cloud Storage path failed, falling back to frame analysis…")
                            a = ai.analyze_clip(vpath, dur)
                    else:
                        a = ai.analyze_clip(vpath, dur)
                    imgs = [geo.frame_at(vpath, t) for t in a["times"]]
                else:
                    fr = [(u.getvalue(), u.type) for u in up]; a = ai.analyze(fr); imgs = [b for b, _ in fr]; a["times"] = [None] * len(imgs)
                ok = [i for i, im in enumerate(imgs) if im]
                if not ok: raise ai.AIError("No usable frames were extracted.")
                a["frames"], a["times"], imgs = [a["frames"][i] for i in ok], [a["times"][i] for i in ok], [imgs[i] for i in ok]
                _status.update(label=f"Step 2/5 — Phase & Zone Detection: {len(a['frames'])} frame(s) analyzed, identifying turnaround phase…")
                ph, pc = (phase_ov, 1.0) if phase_ov else (a["phase"], a["phase_confidence"])
                res = {"analysis": a, "phase": ph, "pc": pc, "items": []}
                if ph == "Unknown" or pc < T["phase_confidence"]:
                    res["note"] = "Phase could not be determined confidently. Phase-specific rules were not applied (safety first); choose Manual override if you know the phase."
                    ph = "Unknown"
                # Zone selection: Gemini proposal → derive from aircraft → saved fixed-camera zones
                zones = geo.load_zones() if zsrc.startswith("Saved") else a["zones"]
                if not any(len(p) >= 3 for p in zones.values()):
                    all_objs = [o for _fr in a["frames"] for o in _fr["objects"]]
                    zones = geo.derive_zones(all_objs, CFG["derived_zones"])
                if not any(len(p) >= 3 for p in zones.values()):
                    zones = geo.load_zones()
                if not any(len(p) >= 3 for p in zones.values()):
                    res["zone_note"] = "No safety zone available: zone-based rules could not be evaluated. Use Zone setup or a clearer image."
                moments = []
                for _i, (_img, _t) in enumerate(zip(imgs, a["times"])):
                    _mp = stor.save_evidence(geo.annotate(_img, a["frames"][_i]["objects"], zones), f"{uuid.uuid4().hex[:8]}_m{_i}.png")
                    moments.append((_t, _mp, f"{len(a['frames'][_i]['objects'])} detection(s)"))
                res["moments"] = moments
                _status.update(label=f"Step 3/5 — Safety Rules Engine: evaluating deterministic guardrails for phase '{ph}'…")
                ctx = dict(gate=CFG["gate"]["id"], camera=CFG["gate"]["camera"], phase=ph, phase_conf=pc)
                all_findings = rules.evaluate(a["frames"], ph, zones)
                for f in all_findings:
                    if not f["eligible"]: res["items"].append(("held", f["rule"]["name"], f["held_reason"]))
                eligible = [f for f in all_findings if f["eligible"]]

                def _assess(f):
                    fi = f["frame"]; img = geo.annotate(imgs[fi], a["frames"][fi]["objects"], zones, [f["object"]["label"]])
                    v = {"supported": True, "confidence": None, "reason": "Verifier disabled in config"}
                    if T["verify"]:
                        try: v = ai.verify(img, f, ph)
                        except ai.AIError as e: v = {"supported": True, "confidence": None, "reason": f"Verifier unavailable ({e}); alert kept for safety"}
                    summ = unc = None
                    if v["supported"]:
                        try: summ, unc = ai.explain(ctx, f)
                        except (ai.AIError, KeyError, TypeError): summ, unc = f["reason"], "AI explanation unavailable (degraded mode)."
                    return f, img, v, summ, unc

                _status.update(label=f"Step 4/5 — AI Verification & Explanation: cross-checking {len(eligible)} finding(s) with skeptical verifier…")
                from concurrent.futures import ThreadPoolExecutor
                with ThreadPoolExecutor(6) as _ex:
                    assessed = list(_ex.map(_assess, eligible))
                for f, img, v, summ, unc in assessed:
                    if not v["supported"]: res["items"].append(("held", f["rule"]["name"], "verifier rejected: " + v["reason"])); continue
                    path = stor.save_evidence(img, f"{uuid.uuid4().hex[:8]}.png")
                    eid, stt, dup = db.create_event(ctx, f, {"image": path, "object": f["object"], "scene": a["scene_summary"], "phase_evidence": a["phase_evidence"], "verifier": v}, summ, unc, ai.MODEL)
                    res["items"].append((stt, eid, f"{f['rule']['id']} {f['severity']}" + (f" · duplicate of {dup}" if dup else "")))
                S.last = res  # core alerts saved — optional enrichment below extends the same dict
                _status.update(label="Step 5/5 — Multi-Agent ADK Pipeline: running 7-agent pipeline with Google Search grounding…")
                try:
                    from concurrent.futures import ThreadPoolExecutor as _TPool
                    import concurrent.futures as _cf
                    _zones_ag = a["zones"] if any(len(p) >= 3 for p in a["zones"].values()) else geo.load_zones()
                    def _run_ag():
                        import agents as ag
                        return ag.run_pipeline(a["frames"], ph, pc, _zones_ag, a["scene_summary"])
                    _ag_pool = _TPool(max_workers=1)
                    _ag_future = _ag_pool.submit(_run_ag)
                    _ag_pool.shutdown(wait=False)  # don't block on thread cleanup
                    try:
                        _ag_trace, _ag_confirmed, _ag_briefing = _ag_future.result(timeout=25)
                        res["agent_result"] = {"trace": _ag_trace, "confirmed": _ag_confirmed, "briefing": _ag_briefing}
                    except _cf.TimeoutError:
                        res["agent_result"] = {"error": "Pipeline timed out (>25 s); alerts are unaffected"}
                    except Exception as _ag_inner:
                        res["agent_result"] = {"error": str(_ag_inner)}
                except Exception as _ag_err:
                    res["agent_result"] = {"error": str(_ag_err)}
                _findings_text = "; ".join(item[2] for item in res["items"] if item[0] not in ("held",)) or "No violations detected"
                try:
                    _brief, _risk = ai.turnaround_brief(CFG["gate"]["id"], ph, a["scene_summary"], _findings_text)
                    res["turnaround_brief"] = _brief
                    res["risk_level"] = _risk
                except Exception:
                    res["turnaround_brief"] = ""
                    res["risk_level"] = "Normal"
                _status.update(label="✅ AI pipeline complete — all 5 steps finished", state="complete")
        except ai.AIError as e:
            S.last = {"error": str(e)}
        except Exception as _outer_err:
            if not S.get("last") or "analysis" not in S.get("last", {}):
                S.last = {"error": f"Analysis error: {_outer_err}"}
        st.rerun()  # reload so the console shows the new events immediately
    L = S.get("last")
    if L and "error" in L: st.error(f"AI analysis unavailable (degraded mode, nothing was fabricated): {L['error']}")
    elif L:
        a = L["analysis"]
        st.info(f"**Detected phase:** {L['phase']} ({L['pc']:.0%}) · {a['phase_evidence']}")
        st.caption(a["scene_summary"])
        _risk_display = {"Normal": st.success, "Elevated": st.info, "High": st.warning, "Critical": st.error}
        if L.get("turnaround_brief"):
            _rl = L.get("risk_level", "Normal")
            _risk_display.get(_rl, st.info)(
                f"**🤖 Turnaround Intelligence Brief** · Operational Risk: **{_rl}**\n\n{L['turnaround_brief']}"
            )
        if L.get("agent_result") and "error" not in L.get("agent_result", {}):
            _ar = L["agent_result"]
            _agent_names = [s["agent"] for s in _ar.get("trace", [])]
            if _agent_names:
                st.caption("🤖 7-Agent ADK Pipeline (Google Search grounded): " + " → ".join(f"✅ {n}" for n in _agent_names))
            with st.expander("🤖 Multi-Agent ADK Pipeline · Full reasoning trace", expanded=False):
                for _step in _ar.get("trace", []):
                    st.write(f"**{_step['agent']}:** {_step['text']}")
                if _ar.get("briefing"):
                    st.success(f"**Agent Briefing:** {_ar['briefing']}")
        elif L.get("agent_result", {}).get("error"):
            st.caption(f"ℹ️ Agent pipeline unavailable: {L['agent_result']['error']}")
        for k in ("note", "zone_note"):
            if L.get(k): st.warning(L[k])
        with st.expander(f"All analysed moments ({len(L['moments'])}): green = detections, shaded = zones", expanded=True):
            for i in range(0, len(L["moments"]), 3):
                for col, (t, mp, txt) in zip(st.columns(3), L["moments"][i:i + 3]):
                    if stor.img_ok(mp): col.image(mp, caption=(f"{int(t) // 60}:{int(t) % 60:02d} · " if t is not None else "") + txt)
        for k, n, d in L["items"]:
            (st.error if k == "Presented" else st.warning if k in ("Suppressed", "held") else st.info)(f"{'Alert' if k=='Presented' else k}: {n} · {d}")
        if not L["items"]: st.success("No rule triggered. Routine activity for this phase was not flagged.")
        with st.expander("Raw observations"): st.json(a["frames"])

    st.divider()
    st.subheader("Phase Replay — Compare Risk Across Phases")
    st.caption("Compare how the same scene scores under two different turnaround phases.")
    if not S.get("last") or "error" in S.get("last", {}):
        st.info("Analyse an image or video first (above), then use Phase Replay here.")
    else:
        L = S["last"]; a = L["analysis"]
        c1, c2 = st.columns(2)
        pa = c1.selectbox("Phase A", CFG["phases"], key="rpa")
        pb = c2.selectbox("Phase B", CFG["phases"], index=min(1, len(CFG["phases"]) - 1), key="rpb")
        if st.button("Compare phases", type="primary", key="replay_go"):
            zones = a["zones"] if any(len(p) >= 3 for p in a["zones"].values()) else geo.load_zones()
            fa = [f for f in rules.evaluate(a["frames"], pa, zones) if f["eligible"]]
            fb = [f for f in rules.evaluate(a["frames"], pb, zones) if f["eligible"]]
            try: narrative = ai.phase_contrast(a["scene_summary"], pa, fa, pb, fb)
            except ai.AIError as e: narrative = f"AI unavailable: {e}"
            S["replay"] = {"pa": pa, "pb": pb, "fa": fa, "fb": fb, "narrative": narrative}
        if S.get("replay"):
            r = S["replay"]
            st.info(r["narrative"])
            c1, c2 = st.columns(2)
            with c1:
                st.subheader(r["pa"])
                for f in r["fa"]: st.error(f"{f['rule']['id']} {f['rule']['name']} · {f['severity']} · {f['object']['label']}")
                if not r["fa"]: st.success("No rules triggered.")
            with c2:
                st.subheader(r["pb"])
                for f in r["fb"]: st.error(f"{f['rule']['id']} {f['rule']['name']} · {f['severity']} · {f['object']['label']}")
                if not r["fb"]: st.success("No rules triggered.")

with t_pipe:
    st.write("Run the 7-agent Google ADK pipeline: Perception → Context → Risk → Skeptic → **Grounding** (Google Search) → **Trend** (event history) → Briefing. Inspect each agent's reasoning step.")
    if not S.get("last") or "error" in S.get("last", {}):
        st.info("Analyse an image or video first (Analyze & Replay tab), then come back here.")
    else:
        L = S["last"]; a = L["analysis"]
        if st.button("Run agent pipeline", type="primary", key="agents_go"):
            zones = a["zones"] if any(len(p) >= 3 for p in a["zones"].values()) else geo.load_zones()
            try:
                import agents as ag
                with st.spinner("Running ADK pipeline…"):
                    trace, confirmed, briefing = ag.run_pipeline(a["frames"], L["phase"], L["pc"], zones, a["scene_summary"])
                S["agent_trace"] = {"trace": trace, "confirmed": confirmed, "briefing": briefing}
            except Exception as e:
                S["agent_trace"] = {"error": str(e)}
        if S.get("agent_trace"):
            at = S["agent_trace"]
            if "error" in at:
                st.error(f"Pipeline error: {at['error']}")
            else:
                if at["briefing"]: st.success(f"**Briefing:** {at['briefing']}")
                for step in at["trace"]:
                    with st.expander(f"🤖 {step['agent']}"):
                        st.write(step["text"])
                if at["confirmed"]:
                    st.subheader("Confirmed findings")
                    for f in at["confirmed"]:
                        st.warning(f"{f['rule']['id']} {f['rule']['name']} · {f['severity']} · {f['object']['label']}")
    st.divider()
    st.subheader("Zone Setup")
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
    st.info("**RampGuard AI · Operational Impact** · Every High/Critical alert detected prevents an estimated **$4,200** in incident costs (IATA ramp safety benchmark). Phase-aware AI reduces false positives vs. standard CV — faster response, fewer interruptions.", icon="📊")
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
            _revd = json.loads(r.evidence or "{}")
            _img_path = _revd.get("image", "")
            c = st.columns([1.2, 0.85, 4.15, 1])
            c[0].markdown(badge(r.severity), unsafe_allow_html=True)
            if stor.img_ok(_img_path):
                c[1].image(_img_path, width=60)
            _is_demo = r.event_id.startswith("DM-")
            _demo_tag = " · <span style='background:#e9ecef;padding:1px 6px;border-radius:8px;font-size:.75rem'>DEMO</span>" if _is_demo else ""
            c[2].markdown(f"**{r.event_type}** · {r.status}{_demo_tag}<br><span class='meta'>{r.ts} · {r.phase} · confidence {r.confidence:.0%} · {r.event_id}</span>", unsafe_allow_html=True)
            if c[3].button("Open", key="o" + r.event_id): S.sel = r.event_id
    if S.get("sel") in set(ev.event_id):
        e = ev[ev.event_id == S.sel].iloc[0]; evd = json.loads(e.evidence or "{}")
        st.divider(); st.markdown(f"### {e.event_type} &nbsp; {badge(e.severity)}", unsafe_allow_html=True)
        a, b = st.columns(2)
        with a:
            if stor.img_ok(evd.get("image")): st.image(evd["image"], caption="Evidence frame")
            if evd.get("video") and os.path.exists(evd["video"]):
                st.video(evd["video"], start_time=int(evd.get("t") or 0)); st.caption(f"Original clip, starting at the alert moment ({int(evd.get('t') or 0)} s)")
            st.write(f"**When/where:** {e.ts} · Gate {e.gate} · {e.camera}")
            st.write(f"**Context:** phase {e.phase} ({e.phase_conf:.0%}). {evd.get('phase_evidence', '')}")
            st.write(f"**Why flagged:** {e.summary}"); st.write(f"**Uncertainty:** {e.uncertainty}")
            if evd.get("verifier"): st.write(f"**Verifier:** {'supported' if evd['verifier']['supported'] else 'rejected'} · {evd['verifier']['reason']}")
            st.write("**Suggested human checks:**")
            st.markdown("\n".join(f"- {c}" for c in json.loads(e.checks)))
            _src_label = "synthetic demo event" if e.event_id.startswith("DM-") else f"AI-generated · model {e.model_version}"
            st.caption(f"Confidence {e.confidence:.0%} · {_src_label} · rules {e.rule_version}" + (f" · duplicate of {e.duplicate_of}" if e.duplicate_of else ""))
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
            _chat_key = "chat_" + e.event_id
            if _chat_key not in S:
                S[_chat_key] = []
            st.caption("Quick questions:")
            _quick_q = ["Why was this flagged?", "What should I check?", "Generate incident summary", f"Why is this priority {e.severity}?"]
            _qc = st.columns(2)
            for _qi, _qt in enumerate(_quick_q):
                if _qc[_qi % 2].button(_qt, key=f"q{_qi}{e.event_id}", use_container_width=True):
                    S[_chat_key].append({"role": "user", "content": _qt})
                    try:
                        _qr = ai.copilot(e, _qt, simt)
                    except ai.AIError as _ex:
                        _qr = f"Copilot unavailable (degraded mode): {_ex}"
                    S[_chat_key].append({"role": "assistant", "content": _qr})
                    st.rerun()
            for _msg in S[_chat_key]:
                with st.chat_message(_msg["role"]):
                    st.markdown(_msg["content"])
            if _cp := st.chat_input("Ask the Safety Copilot anything about this event…", key="ci_" + e.event_id):
                S[_chat_key].append({"role": "user", "content": _cp})
                with st.chat_message("user"):
                    st.markdown(_cp)
                with st.chat_message("assistant"):
                    with st.spinner("Thinking…"):
                        try:
                            _cr = ai.copilot(e, _cp, simt)
                        except ai.AIError as _ex:
                            _cr = f"Copilot unavailable (degraded mode): {_ex}"
                    st.markdown(_cr)
                S[_chat_key].append({"role": "assistant", "content": _cr})
            st.dataframe(db.df("SELECT ts,status,actor,note FROM history WHERE event_id=? ORDER BY id", (e.event_id,)), hide_index=True, use_container_width=True)

with t_ops:
    if ev.empty:
        st.info("No data yet. Run an analysis or click **Load Demo Scenario** in the sidebar to populate the dashboard.")
    else:
        kpis = db.efficiency_kpis()
        st.subheader("Operational KPIs")
        km = st.columns(4)
        km[0].metric("Avg Alert → Ack", f"{kpis['avg_ack_min']:.1f} min" if kpis['avg_ack_min'] is not None else "n/a", help="Minutes from alert creation to first acknowledgment")
        km[1].metric("Alert Precision", f"{kpis['precision_pct']}%" if kpis['precision_pct'] is not None else "n/a", f"{kpis['false_positives']} false positives / {kpis['reviewed']} reviewed")
        km[2].metric("High/Critical Caught", kpis['high_critical'])
        km[3].metric("Est. Cost Savings", f"${kpis['cost_savings_usd']:,}", "@ $4,200/incident (IATA benchmark)")
        st.divider()
        c1, c2 = st.columns(2)
        _hc = ev[ev.severity.isin(["High", "Critical"])].groupby("phase").size().reset_index(name="count")
        if not _hc.empty:
            c1.plotly_chart(px.bar(_hc.sort_values("count", ascending=True), y="phase", x="count", orientation="h", title="High/Critical Alerts by Turnaround Phase", color="count", color_continuous_scale="Reds", labels={"count": "Alerts", "phase": "Phase"}), use_container_width=True)
        else:
            c1.info("No High/Critical alerts yet.")
        _rc = ev[ev.status != "Suppressed"].groupby("rule_id").size().reset_index(name="count")
        if not _rc.empty:
            c2.plotly_chart(px.bar(_rc.sort_values("count", ascending=False), x="rule_id", y="count", title="Safety Rule Trigger Frequency", labels={"rule_id": "Rule", "count": "Times Triggered"}), use_container_width=True)
        c3, c4 = st.columns(2)
        c3.plotly_chart(px.bar(ev.groupby(["phase", "severity"]).size().reset_index(name="count"), x="phase", y="count", color="severity", title="All Alerts by Phase & Severity", category_orders={"severity": CFG["severities"]}), use_container_width=True)
        c4.plotly_chart(px.pie(ev.groupby("status").size().reset_index(name="count"), names="status", values="count", title="Alert Disposition"), use_container_width=True)
        st.divider()
        st.subheader("Recurring Risk Patterns")
        _g = ev[ev.status != "Suppressed"].groupby(["gate", "rule_id", "phase"]).size().reset_index(name="count")
        _patterns = _g[_g["count"] >= 3]
        if _patterns.empty:
            st.success("No recurring patterns detected (no rule triggered ≥3× at the same gate/phase).")
        else:
            for _x in _patterns.itertuples():
                with st.container(border=True):
                    _pc = st.columns([3, 1])
                    _pc[0].warning(f"**{_x.rule_id}** triggered **{_x.count}×** during phase **{_x.phase}** at gate {_x.gate}")
                    _rk = f"rec_{_x.rule_id}_{_x.phase.replace(' ', '_')}"
                    _rk_result = _rk + "_result"
                    if _pc[1].button("Get AI recommendation", key=_rk):
                        with st.spinner("Generating recommendation…"):
                            try:
                                _rt, _rp = ai.pattern_recommendation(_x.gate, _x.rule_id, _x.phase, _x.count)
                                S[_rk_result] = (_rt, _rp)
                            except ai.AIError:
                                S[_rk_result] = ("AI recommendation unavailable in degraded mode.", "Medium")
                    if S.get(_rk_result):
                        _rt, _rp = S[_rk_result]
                        {"High": st.error, "Medium": st.warning, "Low": st.info}.get(_rp, st.info)(f"**AI Recommendation (priority {_rp}):** {_rt}")
    st.divider()
    st.subheader("Scale Architecture: Prototype → Production")
    st.table(pd.DataFrame({
        "Component": ["Vision AI", "Agent Pipeline", "Database", "Storage", "Deployment", "Scale"],
        "This Demo": ["Vertex AI · Gemini 2.5 Flash", "7-agent ADK · Google Search grounding", "SQLite (ephemeral per-instance)", "Cloud Storage (GCS)", "Cloud Run · auto-scale", "1 gate demo"],
        "Production Scale-out": ["Vertex AI batch · multi-region", "ADK + Trend DB (BigQuery)", "Firestore / Cloud SQL", "Cloud Storage · multi-region", "Cloud Run N replicas · multi-gate", "N gates · multi-tenant"],
    }))
