"""Google ADK multi-agent orchestration for RampGuard.

A SequentialAgent chains seven specialist agents over one already-analysed clip:
Perception -> Context -> Risk -> Skeptic -> Grounding -> Trend -> Briefing.

Each agent reuses the existing deterministic/Gemini code (ai.py, rules.py, db.py)
and records a trace step, so the reasoning chain is visible without re-running perception.
"""
import asyncio, uuid
from typing import AsyncGenerator
from google.adk.agents import BaseAgent, SequentialAgent
from google.adk.agents.invocation_context import InvocationContext
from google.adk.events import Event
from google.genai import types
import ai, rules
from rules import CFG, T

APP = "rampguard"
_BOARDS = {}  # invocation key -> shared blackboard (keeps heavy data out of session state)


def _event(name, text):
    return Event(author=name, content=types.Content(role="model", parts=[types.Part(text=text)]))


class _Stage(BaseAgent):
    """Base for a RampGuard specialist: reads/writes the shared blackboard and emits one trace step."""
    board_key: str

    def step(self, bb):  # returns a human-readable summary string
        raise NotImplementedError

    async def _run_async_impl(self, ctx: InvocationContext) -> AsyncGenerator[Event, None]:
        bb = _BOARDS[self.board_key]
        text = self.step(bb)
        bb["trace"].append({"agent": self.name, "text": text})
        yield _event(self.name, text)


class PerceptionAgent(_Stage):
    def step(self, bb):
        n = len(bb["frames"])
        counts = {}
        for fr in bb["frames"]:
            for o in fr["objects"]:
                counts[o["type"]] = counts.get(o["type"], 0) + 1
        bb["perception"] = counts
        summary = ", ".join(f"{v} {k}" for k, v in counts.items()) or "nothing"
        return f"Observed {n} frame(s) from camera {CFG['gate']['camera']}. Detected: {summary}. (Gemini vision perception)"


class ContextAgent(_Stage):
    def step(self, bb):
        ph, pc = bb["phase"], bb["phase_conf"]
        if ph == "Unknown" or pc < T["phase_confidence"]:
            bb["phase"] = "Unknown"
            return f"Phase could not be confirmed ({pc:.0%}). Phase-specific rules will be held back (safety first)."
        exp = CFG["expected_gse"].get(ph, [])
        return f"Turnaround phase: {ph} ({pc:.0%}). Expected equipment here: {', '.join(exp) or 'none'}. Anything else near the aircraft is treated as unexpected."


class RiskAgent(_Stage):
    def step(self, bb):
        found = rules.evaluate(bb["frames"], bb["phase"], bb["zones"])
        bb["candidates"] = found
        elig = [f for f in found if f["eligible"]]
        if not elig:
            return "Deterministic rule guardrails ran: no rule triggered for this scene and phase."
        lines = [f"{f['rule']['id']} {f['rule']['name']} ({f['severity']}) on {f['object']['label']}" for f in elig]
        return "Deterministic rule guardrails raised: " + "; ".join(lines) + "."


class SkepticAgent(_Stage):
    def step(self, bb):
        held = [f for f in bb["candidates"] if not f["eligible"]]
        confirmed = [f for f in bb["candidates"] if f["eligible"]]
        bb["confirmed"] = confirmed
        if not bb["candidates"]:
            return "Nothing to challenge."
        msg = f"Challenged {len(bb['candidates'])} candidate(s): {len(confirmed)} passed persistence/confidence guardrails, {len(held)} held."
        if held:
            msg += " Held: " + "; ".join(f"{f['rule']['id']} ({f['held_reason']})" for f in held)
        return msg


class GroundingAgent(_Stage):
    """Uses Vertex AI Google Search grounding to retrieve real IATA/ICAO safety guidance for the triggered rule."""
    def step(self, bb):
        confirmed = bb.get("confirmed", [])
        if not confirmed:
            bb["grounding"] = ""
            return "No confirmed findings — grounding search skipped."
        sev_rank = {s: i for i, s in enumerate(CFG["severities"])}
        top = max(confirmed, key=lambda f: sev_rank[f["severity"]])
        rule_name = top["rule"]["name"]
        phase = bb["phase"]
        query = (
            f"IATA AHM 630 or ICAO Doc 9137 Part 8 safety requirements for '{rule_name}' "
            f"during aircraft ground handling '{phase}' phase. "
            f"What are the specific clearance distances, personnel restrictions, and procedural requirements?"
        )
        try:
            from google.genai import types as _gt
            cfg = _gt.GenerateContentConfig(
                tools=[_gt.Tool(google_search_retrieval=_gt.GoogleSearchRetrieval())],
                temperature=0.1,
            )
            r = ai._client().models.generate_content(
                model=ai.MODEL,
                contents=query,
                config=cfg
            )
            text = (r.text or "").strip()
            if text:
                bb["grounding"] = text[:600]
                return f"Search-grounded safety reference: {text[:400]}"
        except Exception as e:
            bb["grounding"] = ""
            return f"Google Search grounding unavailable ({str(e)[:80]}); continuing without external reference."
        bb["grounding"] = ""
        return "Search returned no usable result; continuing without external reference."


class TrendAgent(_Stage):
    """Queries the event log for recurring patterns at this gate to detect systemic risk."""
    def step(self, bb):
        confirmed = bb.get("confirmed", [])
        if not confirmed:
            bb["trend"] = ""
            return "No confirmed findings — trend analysis skipped."
        import db, datetime as dt
        gate = CFG["gate"]["id"]
        cutoff = (dt.datetime.utcnow() - dt.timedelta(days=30)).isoformat(timespec="seconds")
        try:
            ev = db.df(
                "SELECT rule_id, severity, status FROM events WHERE gate=? AND ts>=? AND status!='Suppressed'",
                (gate, cutoff)
            )
            if ev.empty:
                bb["trend"] = f"No historical events at gate {gate} in the past 30 days."
                return bb["trend"]
            counts = ev.rule_id.value_counts()
            top_rule = counts.idxmax()
            top_count = int(counts.max())
            conf_rules = {f["rule"]["id"] for f in confirmed}
            msg = f"Gate {gate} event log (30 days): {len(ev)} total events. Most triggered rule: {top_rule} ({top_count}×)."
            if top_rule in conf_rules and top_count >= 3:
                msg += f" ⚠️ This alert ({top_rule}) matches the top recurring pattern — systemic risk indicator, escalation recommended."
            elif top_count >= 3:
                msg += f" Current alert is not the top pattern, but watch for systemic trends."
            else:
                msg += " No recurring patterns detected yet."
            bb["trend"] = msg
            return msg
        except Exception as e:
            bb["trend"] = ""
            return f"Trend analysis unavailable ({str(e)[:60]}); continuing without historical context."


class BriefingAgent(_Stage):
    def step(self, bb):
        confirmed = bb.get("confirmed", [])
        if not confirmed:
            return "No alert to brief. Scene is within normal parameters for this phase."
        sev_rank = {s: i for i, s in enumerate(CFG["severities"])}
        top = max(confirmed, key=lambda f: sev_rank[f["severity"]])
        ctx = dict(gate=CFG["gate"]["id"], camera=CFG["gate"]["camera"], phase=bb["phase"], phase_conf=bb["phase_conf"])
        try:
            summ, _ = ai.explain(ctx, top)
        except Exception:
            summ = top["reason"]
        bb["briefing"] = summ
        result = f"Supervisor briefing — priority {top['severity']} ({top['rule']['id']}): {summ}"
        if bb.get("trend") and "⚠️" in bb.get("trend", ""):
            result += " [Systemic pattern detected — see Trend analysis above.]"
        return result


def build_pipeline(board_key):
    return SequentialAgent(name="RampGuardCoordinator", sub_agents=[
        PerceptionAgent(name="PerceptionAgent", board_key=board_key),
        ContextAgent(name="ContextAgent", board_key=board_key),
        RiskAgent(name="RiskAgent", board_key=board_key),
        SkepticAgent(name="SkepticAgent", board_key=board_key),
        GroundingAgent(name="GroundingAgent", board_key=board_key),
        TrendAgent(name="TrendAgent", board_key=board_key),
        BriefingAgent(name="BriefingAgent", board_key=board_key),
    ])


def run_pipeline(frames, phase, phase_conf, zones, scene=""):
    """Run the ADK sequential pipeline over one analysed clip. Returns (trace, confirmed_findings, briefing)."""
    key = uuid.uuid4().hex
    _BOARDS[key] = {"frames": frames, "phase": phase, "phase_conf": phase_conf, "zones": zones,
                    "scene": scene, "trace": [], "candidates": [], "confirmed": [], "briefing": "",
                    "grounding": "", "trend": ""}
    try:
        runner = InMemoryRunner(agent=build_pipeline(key), app_name=APP)

        async def _go():
            await runner.session_service.create_session(app_name=APP, user_id="console", session_id=key)
            msg = types.Content(role="user", parts=[types.Part(text="Assess this ramp scene.")])
            async for _ in runner.run_async(user_id="console", session_id=key, new_message=msg):
                pass
        asyncio.run(_go())
        bb = _BOARDS[key]
        return bb["trace"], bb["confirmed"], bb["briefing"]
    finally:
        _BOARDS.pop(key, None)
