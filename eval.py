"""RampGuard accuracy harness.

Runs the real detection pipeline (ai.analyze_clip + rules.evaluate) over every
labelled clip and reports recall / precision per rule against the ground truth
in data/eval/labels.json. The labels are a private answer key; the model never
sees them.

Usage:
    python eval.py                 # evaluate all labelled clips
    python eval.py --phase Fueling # force a phase instead of Gemini's detection
    python eval.py --zones saved   # use saved fixed-camera zones (default: derived)

Matching: a labelled hazard counts as CAUGHT if the pipeline raises an eligible
alert with the same rule id, a representative time within the hazard window
(+/- a tolerance), and - when the label has a "match" substring - an object
label containing it. Any eligible alert not matched to a hazard is a FALSE ALARM.
"""
import os, sys, json, argparse
import ai, rules, geo
from rules import CFG, T

LABELS = os.path.join("data", "eval", "labels.json")
CLIPS = os.path.join("data", "eval", "clips")
TOL = 2.5  # seconds of slack around a hazard window


def _alerts_for_clip(path, phase_override, zones_mode):
    dur = geo.duration(path)
    if not dur:
        raise RuntimeError(f"could not read {path}")
    a = ai.analyze_clip(path, dur)
    phase = phase_override or (a["phase"] if a["phase_confidence"] >= T["phase_confidence"] else "Unknown")
    if zones_mode == "saved":
        zl = [geo.load_zones()] * len(a["frames"])
    else:
        zl = [geo.derive_zones(f["objects"], CFG["derived_zones"]) for f in a["frames"]]
    out = []
    for f in rules.evaluate(a["frames"], phase, zl):
        if not f["eligible"]:
            continue
        t = a["times"][f["frame"]]
        out.append({"rule": f["rule"]["id"], "label": (f["object"].get("label") or "").lower(), "t": t if t is not None else 0.0})
    return phase, out


def _matches(hz, al):
    if al["rule"] != hz["rule"]:
        return False
    lo, hi = hz["t"]
    if not (lo - TOL <= al["t"] <= hi + TOL):
        return False
    m = hz.get("match", "")
    return (not m) or (m.lower() in al["label"])


def evaluate_clip(name, spec, args):
    path = os.path.join(CLIPS, name)
    phase, alerts = _alerts_for_clip(path, args.phase, args.zones)
    hazards = spec["hazards"]
    caught = [hz for hz in hazards if any(_matches(hz, al) for al in alerts)]
    missed = [hz for hz in hazards if hz not in caught]
    false_alarms = [al for al in alerts if not any(_matches(hz, al) for hz in hazards)]
    return {"phase": phase, "hazards": hazards, "alerts": alerts,
            "caught": caught, "missed": missed, "false_alarms": false_alarms}


def _rate(num, den):
    return f"{num}/{den} ({(100 * num / den):.0f}%)" if den else "n/a"


def main():
    ap = argparse.ArgumentParser()
    ap.add_argument("--phase", default=None, help="force a turnaround phase")
    ap.add_argument("--zones", choices=["derived", "saved"], default="derived")
    args = ap.parse_args()

    labels = {k: v for k, v in json.load(open(LABELS, encoding="utf-8")).items() if not k.startswith("_")}
    if not labels:
        print("No labelled clips in", LABELS); return

    per_rule = {}  # rule -> [tp, fn, fp]
    tot = [0, 0, 0]
    for name, spec in labels.items():
        if not os.path.exists(os.path.join(CLIPS, name)):
            print(f"! skipping {name}: not found in {CLIPS}"); continue
        print(f"\n=== {name} — {spec.get('description','')}")
        try:
            r = evaluate_clip(name, spec, args)
        except Exception as e:
            print(f"  ERROR: {e}"); continue
        print(f"  phase: {r['phase']} · {len(r['alerts'])} alert(s) raised")
        for hz in r["hazards"]:
            hit = hz in r["caught"]
            per_rule.setdefault(hz["rule"], [0, 0, 0])
            per_rule[hz["rule"]][0 if hit else 1] += 1
            tot[0 if hit else 1] += 1
            print(f"  [{'HIT ' if hit else 'MISS'}] {hz['id']} {hz['rule']} {hz.get('match') or ''} {hz['t']}")
        for al in r["false_alarms"]:
            per_rule.setdefault(al["rule"], [0, 0, 0])
            per_rule[al["rule"]][2] += 1
            tot[2] += 1
            print(f"  [FALSE] {al['rule']} '{al['label']}' @ {al['t']:.1f}s")

    print("\n================ SCORECARD ================")
    print(f"{'Rule':<10}{'Recall':<16}{'Precision':<16}")
    for rule in sorted(per_rule):
        tp, fn, fp = per_rule[rule]
        print(f"{rule:<10}{_rate(tp, tp + fn):<16}{_rate(tp, tp + fp):<16}")
    tp, fn, fp = tot
    print("-" * 42)
    print(f"{'OVERALL':<10}{_rate(tp, tp + fn):<16}{_rate(tp, tp + fp):<16}")
    print(f"\nBRD targets: recall >= 85%, precision >= 80%")


if __name__ == "__main__":
    main()
