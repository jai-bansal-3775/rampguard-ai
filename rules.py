"""Deterministic safety rules (BR-003/004). Gemini observes; rules decide."""
SEV = ["Low", "Medium", "High", "Critical"]
PHASES = ["Aircraft Arrival", "Baggage Handling", "Catering", "Fueling", "Boarding",
          "Turnaround Completion", "Pushback Preparation"]

def _shift(s, n):
    return SEV[max(0, min(3, SEV.index(s) + n))]

def evaluate(obs, ctx):
    """obs: validated observation dict. ctx: {phase, clearance_m, ...}. Returns list of findings."""
    out, clr, ph = [], float(ctx["clearance_m"]), ctx["phase"]
    for o in obs.get("objects", []):
        c, t, d = o["confidence"], o["type"], o.get("distance_m")
        if c < 0.5:
            continue  # too uncertain to raise an event

        def add(code, name, sev, rule, why):
            if c < 0.65:
                sev = _shift(sev, -1)  # low confidence lowers review priority
            out.append(dict(event_type=f"{code} {name}", severity=sev, confidence=c,
                            rule=rule, object=o, reason=why))

        zone = o.get("in_restricted_zone", False)
        if t == "gse" and d is not None and d < clr:
            sev = "Critical" if (ph == "Fueling" or d < clr / 2) else "High"
            add("SE-002", "Unsafe GSE proximity", sev,
                f"GSE distance {d} m < configured clearance {clr} m during {ph}",
                f"{o['label']} appears {d} m from the aircraft, inside the {clr} m clearance, while phase is {ph}.")
        if t == "person" and zone:
            if ph == "Fueling":
                add("SE-001", "Safety-zone intrusion", "Critical", f"Person in restricted zone during {ph}",
                    f"{o['label']} appears inside the restricted zone during fueling.")
            else:
                add("SE-004", "Restricted-area personnel", "High" if ph == "Boarding" else "Medium",
                    f"Person in restricted zone during {ph}",
                    f"{o['label']} appears in a restricted area while phase is {ph}.")
        if t == "obstruction" and ph in ("Turnaround Completion", "Pushback Preparation"):
            add("SE-003", "Obstruction", "High" if ph == "Pushback Preparation" else "Medium",
                f"Obstruction present during {ph}",
                f"{o['label']} appears to block an operational path during {ph}.")
    return out
