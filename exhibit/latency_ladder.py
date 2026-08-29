#!/usr/bin/env python3
"""Render the latency hierarchy of autonomous infrastructure from a sourced
data table. The thesis in one figure: autonomy does not make one loop faster;
it certifies policy at slow (cognition) timescales and compiles it downward
into pre-authorized reflexes. Nanosecond *deliberation* does not exist;
nanosecond *enforcement of certified policy* already ships in ASICs.

Each rung carries its verified timescale and a source key (see REFERENCES.md).
Data is the single source of truth; the figure and the docs read from it.
"""
from __future__ import annotations
import json
from pathlib import Path

import matplotlib
matplotlib.use("Agg")
import matplotlib.pyplot as plt

# (label, seconds (order-of-magnitude), tier, ref)  — tier drives the color band
RUNGS = [
    ("Clock sync (White Rabbit / PTP-HA)", 1e-10, "measurement", "1"),
    ("Switch ASIC cut-through forwarding", 1e-7, "reflex", "2"),
    ("Per-packet adaptive routing / P4 match-action", 1e-7, "reflex", "3"),
    ("Inline FEC (RS-544 'KP4')", 1.5e-7, "reflex", "4"),
    ("PFC pause loop", 5e-6, "reflex", "5"),
    ("Self-healing link reroute (IB SHIELD)", 5e-4, "reflex", "3"),
    ("BFD detection floor (3.3 ms x3)", 1e-2, "detection", "6"),
    ("Real-time RAN loop (O-DU/O-RU)", 8e-3, "control", "7"),
    ("Near-RT RIC xApps", 3e-1, "control", "7"),
    ("OCS hardware switch (MEMS)", 1.5e-2, "control", "9"),
    ("IP FRR / TI-LFA / optical APS (<50 ms)", 4e-2, "control", "10"),
    ("IGP convergence (tuned)", 5e-1, "control", "11"),
    ("Non-RT RIC rApps", 1e1, "policy", "7"),
    ("gNMI telemetry (practice)", 1e1, "sensing", "12"),
    ("BGP convergence (node failure)", 3e2, "policy", "11"),
    ("LLM planner deliberation", 6e1, "cognition", "13"),
    ("TMF intent / assurance loop", 3e2, "cognition", "14"),
]

TIER_COLOR = {
    "measurement": "#9e9e9e",
    "reflex": "#c0504d",       # nanosecond-to-microsecond compiled reflexes
    "detection": "#e08050",
    "control": "#4f81bd",
    "sensing": "#8064a2",
    "policy": "#4e9a06",
    "cognition": "#2c3e50",    # human-timescale deliberation
}


def write_data(outdir: Path):
    (outdir / "latency_ladder.json").write_text(json.dumps(
        [{"label": l, "seconds": s, "tier": t, "ref": r} for l, s, t, r in RUNGS],
        indent=2) + "\n")


def fig(outdir: Path):
    order = sorted(RUNGS, key=lambda x: x[1])
    labels = [f"{l}" for l, *_ in order]
    secs = [s for _, s, *_ in order]
    colors = [TIER_COLOR[t] for *_, t, _ in [(l, s, t, r) for l, s, t, r in order]]
    y = range(len(order))
    fig, ax = plt.subplots(figsize=(11, 7.5))
    ax.barh(list(y), secs, color=colors, log=True, height=0.72)
    ax.set_yticks(list(y), labels, fontsize=8.5)
    ax.set_xlabel("reaction / decision timescale (seconds, log)")
    ax.set_xlim(1e-10, 1e3)
    # tier bands legend
    seen = {}
    for l, s, t, r in order:
        seen[t] = TIER_COLOR[t]
    handles = [plt.Rectangle((0, 0), 1, 1, color=c) for c in seen.values()]
    ax.legend(handles, list(seen.keys()), loc="lower right", fontsize=8, title="tier")
    # annotate the two frontiers
    ax.axvline(1e-6, color="#c0504d", ls=":", lw=1)
    ax.text(1e-6, len(order) - 0.5, " nanosecond reflexes\n (compiled, in-ASIC)",
            color="#c0504d", fontsize=8.5, va="top")
    ax.axvline(1.0, color="#2c3e50", ls=":", lw=1)
    ax.text(1.0, 1.2, " cognition\n (LLM / intent)", color="#2c3e50", fontsize=8.5)
    ax.set_title("The latency hierarchy of autonomous infrastructure\n"
                 "cognition certifies policy at the top; it is compiled downward "
                 "into pre-authorized reflexes at the bottom", fontsize=11)
    fig.tight_layout()
    fig.savefig(outdir.parent / "figures" / "latency_ladder.png", dpi=150)
    plt.close(fig)


if __name__ == "__main__":
    here = Path(__file__).resolve().parent
    (here.parent / "figures").mkdir(exist_ok=True)
    write_data(here)
    fig(here)
    print("wrote latency_ladder.json and figures/latency_ladder.png")
