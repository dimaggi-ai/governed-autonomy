"""Promotion-gate tests: valid records pass; unearned promotions fail.
Run: python3 test_gate.py"""
from pathlib import Path
import yaml
try:
    from .validate_promotion import validate
except ImportError:                      # run as a script from gate/
    from validate_promotion import validate

GOOD_L2 = {
    "action": "cordon-on-xid-48", "domain": "cluster", "fault_domain": "accelerator",
    "level": "L2", "region": "cluster-a / staging",
    "certified_by": ["dcgm-ecc-dbe-inject"], "evidence": ["runs/x/metrics.json"],
    "abort_never_missed": True, "reversible": True, "reviewer": "platform-oncall",
    "proposed_by": "healer-agent",
}

GOOD_SPAN_L1 = {
    "action": "request-circuit", "domain": "span",
    "fault_domain": "inter-hall-circuit", "level": "L1",
    "region": "campus-east / hall-a -> hall-b",
    "certified_by": [], "evidence": ["runs/circuit/dry-run.txt"],
    "blast_radius": {"halls": 1, "scope": "single-circuit",
                     "p99_rtt_ms": 0.9},
    "proposed_by": "healer-agent", "reviewer": "fabric-oncall",
}


def _s(**over):
    s = {**GOOD_L2}; s.update(over); return s


def _span(**over):
    s = {**GOOD_SPAN_L1}
    radius = {**s["blast_radius"], **over.pop("blast_radius", {})}
    s.update(over); s["blast_radius"] = radius
    return s


def test_example_records_all_valid():
    recs = sorted((Path(__file__).parent / "records").glob("*.yaml"))
    assert len(recs) >= 8
    for p in recs:
        errs = validate(yaml.safe_load(p.read_text()), p.name)
        assert not errs, (p.name, errs)


def test_good_l2_passes():
    assert validate(_s(), "t") == []


def test_missing_required_field_fails():
    for f in ("action", "domain", "level", "region", "fault_domain", "evidence",
              "reviewer", "proposed_by"):
        s = _s(); del s[f]
        assert validate(s, "t"), f


def test_l2_without_certifying_experiment_fails():
    errs = validate(_s(certified_by=[]), "t")
    assert errs and "certifying chaos experiment" in errs[0]


def test_l2plus_must_assert_reversible():
    """Fail-closed: you cannot promote above human sign-off without positively
    declaring the action reversible."""
    s = _s(); s.pop("reversible")
    errs = validate(s, "t")
    assert errs and "reversible" in " ".join(errs)


def test_fault_domain_is_required_so_cap_cannot_be_evaded():
    s = _s(); del s["fault_domain"]
    assert validate(s, "t")                        # missing fault_domain rejected


def test_l3_requires_canary_and_budget():
    base = dict(level="L3", region="prod (sampled)", reversible=True,
                certified_by=["decode-replica-kill"])
    # missing both canary and budget
    errs = validate(_s(evidence=["runs/x.json"], **base), "t")
    assert errs and any("canary" in e for e in errs)
    # complete L3
    ok = validate(_s(evidence=["quality-canary.json", "gpu-second-budget.json"], **base), "t")
    assert ok == [], ok


def test_l2_without_abort_held_fails():
    s = _s(); s.pop("abort_never_missed")
    errs = validate(s, "t")
    assert errs and "abort_never_missed" in errs[0]


def test_l0_requires_human_reviewer():
    # a machine identity as reviewer at L0 is rejected
    errs = validate(_s(level="L0", reviewer="auto", certified_by=[], evidence=["p"]), "t")
    assert errs and "human reviewer" in " ".join(errs)
    # a human reviewer at L0 passes
    assert validate(_s(level="L0", reviewer="fabric-oncall", certified_by=[], evidence=["p"]), "t") == []


def test_irreversible_domain_capped_at_l1():
    errs = validate(_s(fault_domain="production-training-fabric", level="L2"), "t")
    assert errs and "capped at L1" in " ".join(errs)
    # L1 on the same domain is fine
    ok = validate(_s(fault_domain="production-training-fabric", level="L1",
                     certified_by=[], evidence=["p"]), "t")
    assert ok == []


def test_l4_inherits_the_l3_bar():
    """The ladder must be monotone: a higher level cannot be cheaper.

    L4 previously used `level == "L4"` for its own rules and `== "L3"`
    for the live-traffic bar, so an L4 claim skipped the canary and the
    held budget entirely.
    """
    errs = validate(_s(level="L4", region="serving-pool-west",
                       certified_by=["c"], reversible=True,
                       abort_never_missed=True,
                       evidence=["full.json", "rollback-drill.json",
                                 "control-plane-dark.json"]), "t")
    assert any("quality canary" in e for e in errs), errs
    assert any("GPU-second budget" in e for e in errs), errs


def test_l4_requires_dark_and_rollback_drills_and_a_pool():
    # missing the drills
    errs = validate(_s(level="L4", region="serving-pool-west",
                       certified_by=["decode-replica-kill"],
                       evidence=["runs/full.json"]), "t")
    assert errs and any("control-plane-dark" in e for e in errs)
    # complete L4 — note it must also clear the L3 bar (the ladder is
    # monotone: L4 inherits the live-traffic canary and the held budget)
    ok = validate(_s(level="L4", region="serving-pool-west",
                     certified_by=["decode-replica-kill", "planner-kill-mid-heal"],
                     evidence=["full.json", "rollback-drill.json",
                               "control-plane-dark.json",
                               "quality-canary.json",
                               "gpu-second-budget.json"]), "t")
    assert ok == [], ok


def test_proposer_cannot_also_be_the_approver():
    """The Actuate/Guard split, checked rather than asserted."""
    errs = validate(_s(proposed_by="platform-oncall"), "t")
    assert errs and "not a control plane" in " ".join(errs)


def test_good_span_record_passes():
    assert validate(_span(), "t") == []


def test_a_span_ceiling_is_not_liftable_by_evidence():
    """The point of the ceiling: a green experiment does not move it.

    `drain-slice` spends a neighbour's compute, so it stays at L0 however well
    it is understood. Handing this record everything L2 asks for -- a citation,
    evidence, a held abort, a reversibility claim -- must still fail.
    """
    errs = validate(_span(action="drain-slice", level="L2",
                          certified_by=["metro-cut-inject"],
                          evidence=["runs/drain/metrics.json"],
                          abort_never_missed=True, reversible=True), "t")
    assert errs and "capped at L0" in " ".join(errs)


def test_an_l1_span_action_cannot_reach_l2():
    errs = validate(_span(level="L2", certified_by=["metro-cut-inject"],
                          abort_never_missed=True, reversible=True), "t")
    assert errs and "capped at L1" in " ".join(errs)
    # and L1 on the same action is fine
    assert validate(_span(), "t") == []


def test_an_unknown_span_action_fails_closed():
    errs = validate(_span(action="reroute-everything"), "t")
    assert errs and "fails closed" in " ".join(errs)


def test_retuning_a_live_collective_has_no_level_at_any_level():
    for level in ("L0", "L1", "L2", "L3", "L4"):
        errs = validate(_span(action="retune-live-collective", level=level,
                              certified_by=["ocs-retune-inject"],
                              evidence=["runs/retune/x.json"],
                              abort_never_missed=True, reversible=True), "t")
        assert errs and "no autonomy level at all" in " ".join(errs), level


def test_a_span_record_needs_a_blast_radius():
    s = _span(); s.pop("blast_radius")
    errs = validate(s, "t")
    assert errs and "blast_radius" in " ".join(errs)


def test_a_blast_radius_needs_a_scope():
    """How far one failure reaches is a required field, not an inference."""
    s = _span()
    s["blast_radius"] = {"halls": 1, "p99_rtt_ms": 0.9}
    errs = validate(s, "t")
    assert errs and "scope" in " ".join(errs)


def test_a_mean_only_blast_radius_is_refused():
    """Average link utilisation is how operators get surprised."""
    s = _span()
    s["blast_radius"] = {"halls": 1, "scope": "single-circuit",
                         "mean_rtt_ms": 0.4}
    errs = validate(s, "t")
    assert errs and "average link utilisation" in " ".join(errs)
    # the mean is welcome once a tail is there too
    s["blast_radius"]["p99_rtt_ms"] = 0.9
    assert validate(s, "t") == []


def test_two_halls_forces_l0_however_good_the_latency_looks():
    errs = validate(_span(action="cordon-slice", level="L1",
                          blast_radius={"halls": 2, "mean_rtt_ms": 0.2}), "t")
    joined = " ".join(errs)
    assert errs and "black-hole" in joined
    # the same action reaching one hall is L1
    assert validate(_span(action="cordon-slice", level="L1"), "t") == []


def test_the_span_domain_does_not_loosen_the_cluster_rules():
    """A span record still has to be a valid record."""
    s = _span(); del s["reviewer"]
    assert validate(s, "t")


if __name__ == "__main__":
    for fn in sorted(k for k in dir() if k.startswith("test_")):
        globals()[fn]()
        print(f"ok {fn}")
    print("all promotion-gate tests passed")
