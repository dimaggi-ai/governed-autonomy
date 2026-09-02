#!/usr/bin/env python3
"""Autonomy-promotion gate — a machine-checkable link to the chaos standard.

An action class may only be promoted up the autonomy ladder on evidence, and
the evidence is a green chaos experiment. This validator refuses a promotion
record that claims a level it has not earned — the same "fail the PR, not the
prose" discipline the chaos-fidelity standard applies to experiments.

Rules (see ../docs/architecture.md#the-autonomy-ladder):
  - required fields present;
  - L2 and above must cite >=1 certifying chaos experiment, carry evidence, and
    assert abort_never_missed;
  - L0/L1 must name a human reviewer (human authorizes);
  - L4 must include a control-plane-dark drill and a rollback drill in the
    evidence, and be scoped to a single declared pool;
  - physically-irreversible domains (production training fabric, power
    interlocks) may not be promoted above L1;
  - whoever proposed the action may not also be the one who approved it;
  - span actions -- the ones that reach across halls -- carry a ceiling that no
    evidence lifts, and must state a blast radius with a tail figure in it
    (see ../docs/span-actions.md).

Usage:
  python3 validate_promotion.py                 # validate every record in records/
  python3 validate_promotion.py path/to/rec.yaml
Exit 0 = all valid; 1 = at least one invalid.
"""
from __future__ import annotations
import re
import sys
from pathlib import Path

import yaml

LEVELS = ("L0", "L1", "L2", "L3", "L4")
DOMAINS = ("cluster", "network", "span")
# certified_by is required non-empty only at L2+ (enforced below); L0/L1 are
# human-authorized and may legitimately cite no experiment. fault_domain is
# required so the irreversibility cap cannot be evaded by omitting it.
# proposed_by is required so the Actuate/Guard split can be checked rather than
# asserted: a record that does not say who proposed the action cannot show that
# somebody else approved it.
REQUIRED = ("action", "domain", "level", "region", "fault_domain", "evidence",
            "reviewer", "proposed_by")
# domains where a real fault is irreversible/dangerous: capped at L1
CAPPED_L1 = ("production-training-fabric", "power-interlock", "optical-live-fiber")
# reviewer names that are not a human sign-off
MACHINE_IDENTITIES = ("auto", "agent", "planner", "system", "referee")

# -- span actions -----------------------------------------------------------
# A job that spans halls can be acted on in a small, closed set of ways. Each
# carries a ceiling that is a property of the action rather than of the
# evidence: no number of green experiments promotes it, because what caps it is
# whose work the action spends, not how well it is understood.
#
# The four at L1 touch only the job that asked for them. The four at L0 reach
# across a job or tenant boundary -- draining a neighbour to make room is
# spending somebody else's compute -- and a person decides that whatever the
# size of the slice.
SPAN_CEILING = {
    "request-circuit": "L1",
    "release-circuit": "L1",
    "cordon-slice": "L1",
    "delay-checkpoint": "L1",
    "drain-slice": "L0",
    "move-job": "L0",
    "shrink-job": "L0",
    "reconstitute-slice": "L0",
}
# Actions with no level at all. These are not "L0" -- L0 means a person may
# authorize it; these mean the record should not exist.
SPAN_FORBIDDEN = {
    "retune-live-collective":
        "retuning a circuit underneath a live collective is not an autonomy "
        "level, it is an outage: the retune is milliseconds and the collective "
        "is microseconds, so there is no evidence that could promote it",
}
# A blast radius stated as an average is the one that surprises operators.
TAIL_FIELDS = ("p99_rtt_ms", "p999_rtt_ms", "tail_rtt_ms", "p99_9_rtt_ms")
MEAN_FIELDS = ("mean_rtt_ms", "avg_rtt_ms", "average_rtt_ms",
               "mean_link_utilization", "avg_link_utilization")


def validate(rec: dict, name: str) -> list[str]:
    errs: list[str] = []

    def err(m):
        errs.append(f"  {name}: {m}")

    for f in REQUIRED:
        if f not in rec or rec[f] in (None, "", []):
            err(f"missing required field '{f}'")
    if errs:
        return errs

    if rec["level"] not in LEVELS:
        err(f"level must be one of {LEVELS}")
        return errs
    if rec["domain"] not in DOMAINS:
        err(f"domain must be one of {DOMAINS}")

    lvl = LEVELS.index(rec["level"])
    certs = rec.get("certified_by") or []
    evidence = rec.get("evidence") or []
    if isinstance(certs, str):
        certs = [certs]
    if isinstance(evidence, str):
        evidence = [evidence]

    # L0/L1: a human must authorize (not a machine identity)
    if lvl <= 1 and str(rec.get("reviewer", "")).strip().lower() in MACHINE_IDENTITIES:
        err("L0/L1 requires a named human reviewer, not a machine identity")

    # The Actuate/Guard split, checked rather than asserted. Actuate proposes;
    # Guard approves. One identity doing both is not a control plane, and it is
    # the failure that drains the wrong hall.
    if str(rec.get("proposed_by", "")).strip().lower() == str(
            rec.get("reviewer", "")).strip().lower():
        err("proposed_by and reviewer are the same identity: Actuate proposes "
            "and Guard approves, and one process doing both is not a control "
            "plane")

    # L2+: must be earned by a green experiment, with evidence and abort held,
    # and must POSITIVELY assert reversibility (fail closed on irreversibility —
    # you cannot promote above human sign-off without declaring the action's
    # effect reversible).
    if lvl >= 2:
        if not certs:
            err(f"{rec['level']} requires >=1 certifying chaos experiment in 'certified_by'")
        if not evidence:
            # Reachable only for falsy values the REQUIRED sweep does not
            # list (0, False, {}) — narrow, but it fails closed rather than
            # letting a falsy evidence field through at L2+.
            err(f"{rec['level']} requires evidence paths")
        if rec.get("abort_never_missed") is not True:
            err(f"{rec['level']} requires abort_never_missed: true")
        if rec.get("reversible") is not True:
            err(f"{rec['level']} requires reversible: true (fail-closed on irreversibility)")

    # L3: the live-traffic bar — a quality canary and a held GPU-second budget,
    # strictly above L2's staged evidence. Note `lvl >= 3`, not `== "L3"`: the
    # ladder must be monotone, so L4 inherits every L3 requirement. (It did
    # not before, which made L4 cheaper to claim than L3.)
    if lvl >= 3:
        joined3 = " ".join(str(x) for x in list(certs) + list(evidence)).lower()
        if "canary" not in joined3:
            err(f"{rec['level']} requires a live-traffic quality canary "
                f"(the L3 bar) in certified_by/evidence")
        if "budget" not in joined3 and "gpu-second" not in joined3:
            err(f"{rec['level']} requires a held wasted-GPU-second budget "
                f"(the L3 bar) in certified_by/evidence")

    # L4: hardest drills + single pool, on top of everything L3 requires
    if lvl >= 4:
        joined = " ".join(str(x) for x in list(certs) + list(evidence)).lower()
        if "control-plane-dark" not in joined and "planner-dark" not in joined:
            err("L4 requires a control-plane-dark drill in certified_by/evidence")
        if "rollback" not in joined:
            err("L4 requires a rollback drill in certified_by/evidence")
        if not re.search(r"pool", str(rec["region"]).lower()):
            err("L4 must be scoped to a single declared pool (region names a pool)")

    # -- span actions -------------------------------------------------------
    # A span action's ceiling is a property of the action. Evidence does not
    # lift it, so this runs whatever the record cites.
    if rec["domain"] == "span":
        action = str(rec["action"]).strip().lower()
        if action in SPAN_FORBIDDEN:
            err(f"span action '{action}' has no autonomy level at all: "
                f"{SPAN_FORBIDDEN[action]}")
        elif action not in SPAN_CEILING:
            err(f"unknown span action '{action}': the span action set is closed "
                f"({', '.join(sorted(SPAN_CEILING))}), and an unrecognised "
                f"action fails closed rather than defaulting to permitted")
        else:
            ceiling = SPAN_CEILING[action]
            if lvl > LEVELS.index(ceiling):
                err(f"span action '{action}' is capped at {ceiling} and cannot "
                    f"be {rec['level']}: what caps it is whose work it spends, "
                    f"not how well it is understood")

        radius = rec.get("blast_radius")
        if not isinstance(radius, dict):
            err("a span record requires a 'blast_radius' mapping: a span is "
                "asked to cross halls, and how far the failure reaches is the "
                "field the decision turns on")
        else:
            halls = radius.get("halls")
            if not isinstance(halls, int) or halls < 1:
                err("blast_radius.halls must be an integer >= 1")
            elif halls >= 2 and lvl > 0:
                err(f"blast_radius.halls is {halls}: a span that can black-hole "
                    f"more than one hall is L0 whatever its latency looks like, "
                    f"and cannot be {rec['level']}")
            scope = str(radius.get("scope", "")).strip()
            if not scope:
                err("blast_radius requires a 'scope': how far one failure "
                    "reaches (rack, hall, campus-ring). It is not called "
                    "failure_domain because the record already carries "
                    "fault_domain for the kind of fault, and two fields a "
                    "letter apart is how the wrong one gets filled in")
            tails = [f for f in TAIL_FIELDS if f in radius]
            means = [f for f in MEAN_FIELDS if f in radius]
            if not tails:
                if means:
                    err(f"blast_radius states only {', '.join(means)}: average "
                        f"link utilisation is how operators get surprised. Give "
                        f"a tail figure (one of {', '.join(TAIL_FIELDS)}) as "
                        f"well; the mean may stay alongside it")
                else:
                    err(f"blast_radius requires a tail figure (one of "
                        f"{', '.join(TAIL_FIELDS)}): the tail and the scope are "
                        f"the fields the decision turns on")

    # irreversible domains capped at L1
    fault_domain = str(rec.get("fault_domain", "")).lower()
    if fault_domain in CAPPED_L1 and lvl > 1:
        err(f"fault_domain '{fault_domain}' is irreversible/dangerous — capped at L1, "
            f"cannot be {rec['level']}")

    return errs


def _collect(args: list[str]) -> list:
    here = Path(__file__).resolve().parent
    raw = [Path(a) for a in args] if args else [here / "records"]
    out = []
    for p in raw:
        out.extend(sorted(p.glob("*.yaml")) if p.is_dir() else [p])
    return out


def main(argv: list[str]) -> int:
    if any(a in ("-h", "--help") for a in argv[1:]):
        print(
            "promotion-gate — check autonomy-promotion records against the L0-L4 gate.\n\n"
            "Usage:\n"
            "  promotion-gate [RECORD.yaml | DIR ...]\n\n"
            "With no arguments, validates the bundled reference records.\n"
            "Exit status: 0 if all records pass the gate, 1 otherwise."
        )
        return 0
    paths = _collect(argv[1:])
    if not paths:
        print("no promotion records found (pass a .yaml record or a directory)")
        return 1
    all_errs: list[str] = []
    for p in paths:
        try:
            rec = yaml.safe_load(p.read_text())
        except (OSError, yaml.YAMLError) as e:
            print(f"FAIL {p.name}")
            all_errs.append(f"  {p.name}: unreadable ({e})")
            continue
        e = validate(rec, p.name)
        print(f"{'FAIL' if e else 'ok  '} {p.name}")
        all_errs.extend(e)
    if all_errs:
        print(f"\n{len(all_errs)} problem(s):")
        print("\n".join(all_errs))
        return 1
    print(f"\nall {len(paths)} promotion records valid")
    return 0


def cli() -> None:
    """Console entry point (`promotion-gate [record.yaml | dir ...]`)."""
    raise SystemExit(main(sys.argv))


if __name__ == "__main__":
    raise SystemExit(main(sys.argv))
