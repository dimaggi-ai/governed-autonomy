#!/usr/bin/env python3
"""Ladder validation: the latency-hierarchy exhibit vs the sources it quotes.

This repo is an architecture and a sourced latency map, not a simulator —
so its validation project is scoped honestly to what the exhibit claims:
that each rung's VALUE sits inside the band its own cited source
publishes, that each rung cites the source the band was restated from,
and that the structural thesis ("autonomy never makes one loop faster;
policy is certified at human timescales and compiled downward") actually
holds in the data file the figure is generated from.

What this does NOT do, and no numeric check can: verify that a quotation
is a quotation. A band check catches a rung that drifts from its source;
it cannot catch a source that was paraphrased wrongly in REFERENCES.md
in the first place. That remains a human review job.

Two kinds of checks:

  quoted   the rung's value sits inside the band its own cited source
           publishes (bands restated here from REFERENCES.md), AND the
           rung cites that same reference. Passing proves the exhibit
           has not drifted from its citations, not that the sources are
           right.
  sanity   structural invariants of the data file itself: every rung's
           ref resolves to a REFERENCES.md entry, labels are unique, the
           tier vocabulary is the one this validator partitions, and
           every deliberative loop is slower than every mechanism it
           governs. These cite no external evidence and claim none.

Rungs with no numeric band in their source are listed in UNCHECKED and
printed with the table rather than quietly counted as covered.

Run: python3 validate_ladder.py   (exit 1 if any check fails)
"""

from __future__ import annotations

import dataclasses
import json
import re
import sys
from pathlib import Path

HERE = Path(__file__).resolve().parent
LADDER = json.loads((HERE / "latency_ladder.json").read_text())
REFERENCES = (HERE.parent / "REFERENCES.md").read_text()

# The tier vocabulary, partitioned. DELIBERATIVE tiers are where a
# decision is actually made — by a human, a policy engine, or a planner.
# MECHANISM tiers execute pre-authorized policy without deliberating.
# SENSING is neither: it is the observation path that feeds both, so it
# is excluded from the thesis check, and `tier-partition-is-total` below
# makes that exclusion visible rather than silent.
DELIBERATIVE = ("policy", "cognition")
MECHANISM = ("measurement", "reflex", "detection", "control")
SENSING = ("sensing",)

# Rungs whose sources publish no numeric band to check them against.
# Named here so the coverage count cannot quietly overstate itself.
UNCHECKED = (
    ("Self-healing link reroute (IB SHIELD)", "[3]",
     "the adaptive-routing whitepaper describes SHIELD's mechanism but "
     "publishes no port-to-port recovery figure; the 500 us rung is a "
     "class estimate and is the one rung on this ladder not pinned to a "
     "published number."),
)


@dataclasses.dataclass(frozen=True)
class Check:
    name: str
    kind: str      # 'quoted' | 'sanity'
    ref: str       # REFERENCES.md entry for quoted checks; '-' for sanity
    ok: bool
    note: str


def _rung(label_fragment: str) -> dict:
    hits = [r for r in LADDER if label_fragment.lower() in r["label"].lower()]
    if len(hits) != 1:
        raise ValueError(f"{label_fragment!r} matched {len(hits)} rungs")
    return hits[0]


def checks() -> list[Check]:
    out: list[Check] = []

    # ------------------------------------------------------------- quoted
    # Bands restated from the exhibit's own citations. A rung outside its
    # source's band means the exhibit misquotes the source it names; a
    # rung citing a different reference means the band and the rung have
    # come apart, which the ref check below catches independently.
    quoted = (
        ("clock-sync-sub-ns", "[1]", "Clock sync", 0.0, 1e-9,
         "White Rabbit / PTP-HA: sub-nanosecond synchronization."),
        ("asic-forwarding-sub-130ns", "[2]", "cut-through", 0.0, 1.3e-7,
         "Switch port-to-port latency: the QM8790 product brief publishes "
         "'sub-130ns'; Quantum-X800 is <100 ns. Band is the slower of the "
         "two published ceilings, so the rung must clear the figure the "
         "cited brief actually prints."),
        ("p4-match-action-sub-450ns", "[3]", "adaptive routing", 1e-9, 4.5e-7,
         "Line-rate match-action: 'a few nanoseconds per packet', with "
         "measured in-network inference <450 ns/packet as the ceiling."),
        ("kp4-fec-inline", "[4]", "KP4", 1e-7, 2e-7,
         "RS(544,514) inline FEC: ~100-200 ns added latency."),
        ("pfc-pause-loop", "[5]", "PFC", 1e-6, 1e-5,
         "802.1Qbb reaction loop: ~1-10 us intra-DC."),
        ("oran-realtime-below-10ms", "[7]", "Real-time RAN", 0.0, 0.010,
         "O-RAN real-time loop: <10 ms, in the O-DU/O-RU."),
        ("oran-near-rt-band", "[7]", "Near-RT", 0.010, 1.0,
         "Near-RT RIC xApps: 10 ms - 1 s."),
        ("oran-non-rt-above-1s", "[7]", "Non-RT", 1.0, float("inf"),
         "Non-RT rApps: >1 s."),
        ("ocs-commercial-10-20ms", "[9]", "OCS", 1e-2, 2e-2,
         "Mission Apollo: 'millisecond-scale switching', and 'commercial "
         "OCS switching times are typically between 10-20ms'. Band is the "
         "source's own numeric range, not the looser prose one."),
        ("frr-under-50ms", "[10]", "FRR", 0.0, 0.050,
         "50 ms protection lineage (G.841 / RFC 5654 / TI-LFA)."),
        ("igp-tuned-sub-second", "[11]", "IGP", 0.1, 1.0,
         "Tuned IGP convergence: sub-second, the result the cited paper "
         "is titled for."),
        ("bgp-node-failure-seconds-to-minutes", "[11]", "BGP", 1.0, 600.0,
         "BGP node-failure convergence at default MRAI: seconds to "
         "minutes. A qualitative range, checked as one."),
        ("gnmi-practice-band", "[12]", "gNMI", 5.0, 30.0,
         "gNMI in practice: vendor minimums 5-10 s, common 30 s."),
        ("llm-planner-seconds-to-minutes", "[13]", "LLM planner", 1.0, 600.0,
         "Planner deliberation at seconds-minutes across the 2024-26 "
         "network-agent systems surveyed."),
        ("tmf-intent-oss-timescale", "[14]", "TMF intent", 60.0, 3600.0,
         "TMF921 intent / assurance loops run at OSS timescale (minutes)."),
    )
    for name, ref, frag, lo, hi, note in quoted:
        rung = _rung(frag)
        in_band = lo <= rung["seconds"] <= hi
        # The ref column is only meaningful if the rung actually cites it.
        cites_it = str(rung["ref"]) == ref.strip("[]")
        detail = note
        if not in_band:
            detail = f"OUT OF BAND ({rung['seconds']:g} s not in [{lo:g}, {hi:g}]). {note}"
        elif not cites_it:
            detail = f"REF MISMATCH (rung cites [{rung['ref']}], band restated from {ref}). {note}"
        out.append(Check(name, "quoted", ref, in_band and cites_it, detail))

    # BFD is arithmetic, not a band: the source's own numbers give
    # 3.3 ms x3 = 9.9 ms exactly, and the rung rounds it to 10 ms. The
    # tolerance is that rounding (0.2 ms), not a percentage envelope.
    bfd = _rung("BFD")
    out.append(Check(
        "bfd-floor-arithmetic", "quoted", "[6]",
        abs(bfd["seconds"] - 9.9e-3) <= 2e-4 and str(bfd["ref"]) == "6",
        "Detection floor is arithmetic from the source's numbers: "
        "3.3 ms x3 = 9.9 ms exactly, quoted as ~10 ms. Tolerance is the "
        "0.1 ms rounding (+/-0.2 ms), not a percentage of the value.",
    ))

    # ------------------------------------------------------------- sanity
    refs_defined = set(re.findall(r"- \*\*\[(\d+)\]\*\*", REFERENCES))
    refs_used = {str(r["ref"]) for r in LADDER}
    out.append(Check(
        "every-ref-resolves", "sanity", "-",
        refs_used <= refs_defined,
        f"Every rung's ref number resolves to a REFERENCES.md entry "
        f"(used: {sorted(refs_used, key=int)}).",
    ))

    labels = [r["label"] for r in LADDER]
    out.append(Check(
        "labels-unique", "sanity", "-",
        len(labels) == len(set(labels)),
        "No duplicate rung labels — the figure legend cannot silently "
        "collapse two rungs.",
    ))

    tiers = {r["tier"] for r in LADDER}
    partition = set(DELIBERATIVE) | set(MECHANISM) | set(SENSING)
    out.append(Check(
        "tier-partition-is-total", "sanity", "-",
        tiers == partition,
        f"Every tier in the data file falls in exactly one side of the "
        f"partition this validator uses — deliberative {DELIBERATIVE}, "
        f"mechanism {MECHANISM}, observation {SENSING}. A new tier cannot "
        f"be added without deciding, in code, which side of the thesis it "
        f"sits on.",
    ))

    covered = {c.name for c in out if c.kind == "quoted" and c.ok}
    unchecked_labels = {label for label, _, _ in UNCHECKED}
    out.append(Check(
        "coverage-is-declared", "sanity", "-",
        len(covered) + len(unchecked_labels) == len(LADDER),
        f"{len(covered)} of {len(LADDER)} rungs carry a numeric band "
        f"check; the remaining {len(unchecked_labels)} are named in "
        f"UNCHECKED with the reason. The two numbers must add up, so a "
        f"rung cannot be dropped from the checks without being declared.",
    ))

    slowest_mechanism = max(r["seconds"] for r in LADDER
                            if r["tier"] in MECHANISM)
    fastest_deliberation = min(r["seconds"] for r in LADDER
                               if r["tier"] in DELIBERATIVE)
    ratio = fastest_deliberation / slowest_mechanism
    out.append(Check(
        "deliberation-is-slower-than-every-mechanism", "sanity", "-",
        ratio >= 10.0,
        f"The repo's thesis, checked in its own data: the fastest "
        f"DELIBERATIVE loop ({fastest_deliberation:g} s) is "
        f"{ratio:.0f}x slower than the slowest MECHANISM that executes "
        f"pre-authorized policy ({slowest_mechanism:g} s). The threshold "
        f"is an order of magnitude, not the observed margin. This "
        f"partition deliberately puts protection mechanisms (BFD, FRR, "
        f"IGP, OCS) on the MECHANISM side even though they sit in "
        f"control-plane tiers: FRR is a certified policy compiled "
        f"downward, which is the thesis, not a counterexample to it. "
        f"Grouping them as 'decisions' made the earlier form of this "
        f"check depend on how two rungs were tiered.",
    ))

    return out


def validate() -> tuple[list[Check], bool]:
    cs = checks()
    return cs, all(c.ok for c in cs)


def main() -> int:
    cs, ok = validate()
    w = max(len(c.name) for c in cs)
    print(f"{'check':<{w}}  {'kind':<6}  {'ref':<5}  verdict")
    for c in cs:
        print(f"{c.name:<{w}}  {c.kind:<6}  {c.ref:<5}  "
              f"{'PASS' if c.ok else 'FAIL'}")
        if not c.ok:
            print(f"{'':<{w}}  -> {c.note}")
    print()
    print("rungs with no numeric band in their source (not counted as covered):")
    for label, ref, why in UNCHECKED:
        print(f"  - {label} {ref}\n      {why}")
    print()
    if ok:
        n_quoted = sum(1 for c in cs if c.kind == "quoted")
        print(f"all {len(cs)} checks pass — {n_quoted} of {len(LADDER)} rungs "
              f"sit inside the band their own cited source publishes AND "
              f"cite that source; sanity checks pin the exhibit's structure. "
              f"This validates the exhibit against its citations, not the "
              f"citations against their sources.")
    else:
        print("LADDER VALIDATION FAILED")
    return 0 if ok else 1


if __name__ == "__main__":
    sys.exit(main())
