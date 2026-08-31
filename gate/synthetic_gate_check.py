#!/usr/bin/env python3
"""Synthetic-data validation of the promotion gate.

The gate's five bundled records are hand-written examples; a validator
proven only on its own examples proves nothing. This harness generates
synthetic promotion records from a seeded RNG and pushes them through
the real validator, both ways:

  - randomized VALID records at every level L0-L4 (random actions,
    regions, reviewers, evidence paths) must all be accepted;
  - each record then gets exactly one rule-violating mutation drawn
    from the full rule matrix, and every mutated record must be
    rejected FOR THAT RULE'S REASON, not an incidental one.

Two details that decide whether this harness is worth anything:

  1. The mutated record is submitted under a NEUTRAL name, and the
     reason is matched against the error message with the record-name
     prefix stripped. Submitting it under the mutation's own name made
     three assertions vacuous — "L3-no-canary" contains "canary", so
     `"canary" in error` was satisfied by *any* error at all.
  2. Every rule is mutated in every form it can take, and every
     enumerated value is exercised: all five machine identities, all
     three capped fault domains, both levels where a human reviewer is
     required, and both the missing and the explicitly-false form of
     each boolean assertion. Testing one member of a list proves the
     list is read, not that it is right.

If any synthetic record slips through (or a valid one is refused), the
gate has a hole and this script exits 1. The RNG is seeded, so a
failure reproduces exactly.

Run: python3 synthetic_gate_check.py
"""

from __future__ import annotations

import copy
import random
import sys

try:
    from .validate_promotion import CAPPED_L1, MACHINE_IDENTITIES, validate
except ImportError:                      # run as a script from gate/
    from validate_promotion import CAPPED_L1, MACHINE_IDENTITIES, validate

SEED = 0
N_PER_LEVEL = 20

ACTIONS = ("cordon-node", "drain-rack", "reroute-tenant", "rescale-decode",
           "failover-pool", "recycle-link", "shift-traffic", "park-jobs")
DOMAINS = ("cluster", "network")
SAFE_FAULT_DOMAINS = ("inference-pool", "staging-fabric", "canary-cell",
                      "decode-tier", "test-rack")
REVIEWERS = ("m.nanyonga", "j.okello", "a.namutebi", "site-lead-2")
POOLS = ("pool-a", "pool-b", "inference-pool-eu", "decode-pool-3")
REQUIRED_FIELDS = ("action", "domain", "level", "region", "fault_domain",
                   "evidence", "reviewer")

# The mutated record is submitted under this name so that no mutation's
# own label can satisfy the substring it is supposed to prove.
NEUTRAL = "record"


def synth_valid(level: str, rng: random.Random) -> dict:
    """One synthetic record that should pass the gate at `level`."""
    rec = {
        "action": rng.choice(ACTIONS),
        "domain": rng.choice(DOMAINS),
        "level": level,
        "region": rng.choice(POOLS),
        "fault_domain": rng.choice(SAFE_FAULT_DOMAINS),
        "evidence": [f"evidence/{rng.randrange(10**6)}.log"],
        "reviewer": rng.choice(REVIEWERS),
    }
    lvl = int(level[1])
    if lvl >= 2:
        rec["certified_by"] = [f"chaos/{rng.choice(ACTIONS)}.yaml"]
        rec["abort_never_missed"] = True
        rec["reversible"] = True
    # The ladder is monotone: L4 must clear the L3 bar as well as its own.
    if lvl >= 3:
        rec["evidence"].append("evidence/live-canary-quality.json")
        rec["evidence"].append("evidence/gpu-second-budget-held.json")
    if lvl >= 4:
        rec["evidence"].append("evidence/control-plane-dark-drill.log")
        rec["evidence"].append("evidence/rollback-drill.log")
    return rec


def _del(key):
    def m(r):
        r.pop(key, None)
    return m


def _set(key, val):
    def m(r):
        r[key] = val
    return m


def _strip_evidence(fragment):
    def m(r):
        r["evidence"] = [e for e in r["evidence"] if fragment not in e]
    return m


# (name, level the mutation applies at, mutate(rec), reason substring the
# gate must give). One entry per rule in validate_promotion.py, per form
# that rule can be violated in, and per enumerated value it reads.
MUTATIONS = tuple(
    # every required field, each asserted to be named in its own error
    [(f"missing-{f}", "L2", _del(f), f"missing required field '{f}'")
     for f in REQUIRED_FIELDS]
    # every machine identity, at both levels that require a human
    + [(f"machine-reviewer-{who}-at-{lv}", lv, _set("reviewer", who),
        "named human reviewer")
       for lv in ("L0", "L1") for who in MACHINE_IDENTITIES]
    # every irreversible domain, none of which may sit above L1
    + [(f"capped-domain-{d}", "L3", _set("fault_domain", d), "capped at L1")
       for d in CAPPED_L1]
    + [
        ("bad-level", "L2", _set("level", "L5"), "level must be one of"),
        ("bad-domain", "L2", _set("domain", "cloud"), "domain must be one of"),
        ("capped-domain-mixed-case", "L3",
         _set("fault_domain", "Production-Training-Fabric"), "capped at L1"),
        ("L2-without-certification", "L2", _del("certified_by"),
         "certifying chaos experiment"),
        # both forms of each boolean assertion: absent, and present-but-false
        ("L2-abort-not-held", "L2", _set("abort_never_missed", False),
         "abort_never_missed"),
        ("L2-abort-missing", "L2", _del("abort_never_missed"),
         "abort_never_missed"),
        ("L2-irreversible-missing", "L2", _del("reversible"), "reversible: true"),
        ("L2-reversible-false", "L2", _set("reversible", False),
         "reversible: true"),
        # falsy-but-present evidence: slips past the required-field sweep
        ("L2-evidence-falsy", "L2", _set("evidence", 0), "requires evidence paths"),
        ("L3-no-canary", "L3", _strip_evidence("canary"), "quality canary"),
        ("L3-no-budget", "L3", _strip_evidence("budget"), "GPU-second budget"),
        # L4 inherits the L3 bar — these fail before the fix that made the
        # ladder monotone, and are the regression test for it
        ("L4-no-canary", "L4", _strip_evidence("canary"), "quality canary"),
        ("L4-no-budget", "L4", _strip_evidence("budget"), "GPU-second budget"),
        ("L4-no-dark-drill", "L4", _strip_evidence("control-plane-dark"),
         "control-plane-dark"),
        ("L4-no-rollback", "L4", _strip_evidence("rollback"), "rollback drill"),
        ("L4-not-pool-scoped", "L4", _set("region", "eu-west"),
         "single declared pool"),
    ]
)


def _message(e: str) -> str:
    """The error text with the record-name prefix removed."""
    return e.split(": ", 1)[1] if ": " in e else e


def run() -> tuple[int, int, list[str]]:
    rng = random.Random(SEED)
    problems: list[str] = []

    accepted = 0
    for level in ("L0", "L1", "L2", "L3", "L4"):
        for i in range(N_PER_LEVEL):
            rec = synth_valid(level, rng)
            errs = validate(rec, f"synth-{level}-{i}")
            if errs:
                problems.append(f"valid {level} record refused: {errs}")
            else:
                accepted += 1

    # The validator accepts a bare string where a list is expected; the
    # bundled records never exercise that branch.
    for level in ("L2", "L4"):
        rec = synth_valid(level, rng)
        rec["certified_by"] = rec["certified_by"][0]
        rec["evidence"] = " ".join(rec["evidence"])
        errs = validate(rec, f"synth-{level}-string-fields")
        if errs:
            problems.append(
                f"valid {level} record with string-valued certified_by/"
                f"evidence refused: {errs}")
        else:
            accepted += 1

    rejected = 0
    for name, level, mutate, reason in MUTATIONS:
        rec = synth_valid(level, rng)
        mutated = copy.deepcopy(rec)
        mutate(mutated)
        errs = validate(mutated, NEUTRAL)
        if not errs:
            problems.append(f"mutation {name} slipped through the gate")
        elif not any(reason in _message(e) for e in errs):
            problems.append(
                f"mutation {name} rejected for the wrong reason: {errs} "
                f"(expected {reason!r})")
        else:
            rejected += 1

    return accepted, rejected, problems


def main() -> int:
    accepted, rejected, problems = run()
    total_valid = 5 * N_PER_LEVEL + 2
    print(f"synthetic valid records accepted:  {accepted}/{total_valid}")
    print(f"synthetic mutations rejected:      {rejected}/{len(MUTATIONS)} "
          f"(each for its own rule's stated reason, matched against the "
          f"message with the record name stripped)")
    if problems:
        print("\nGATE HOLES:")
        for p in problems:
            print(f"  - {p}")
        return 1
    print("\nthe gate holds: every synthetic valid record passes, every "
          "single-rule violation is refused with its rule's reason")
    return 0


if __name__ == "__main__":
    sys.exit(main())
