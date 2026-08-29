"""Promotion-gate tests: valid records pass; unearned promotions fail.
Run: python3 test_gate.py"""
from pathlib import Path
import yaml
from validate_promotion import validate

GOOD_L2 = {
    "action": "cordon-on-xid-48", "domain": "cluster", "fault_domain": "accelerator",
    "level": "L2", "region": "cluster-a / staging",
    "certified_by": ["dcgm-ecc-dbe-inject"], "evidence": ["runs/x/metrics.json"],
    "abort_never_missed": True, "reversible": True, "reviewer": "platform-oncall",
}


def _s(**over):
    s = {**GOOD_L2}; s.update(over); return s


def test_example_records_all_valid():
    recs = sorted((Path(__file__).parent / "records").glob("*.yaml"))
    assert len(recs) >= 4
    for p in recs:
        errs = validate(yaml.safe_load(p.read_text()), p.name)
        assert not errs, (p.name, errs)


def test_good_l2_passes():
    assert validate(_s(), "t") == []


def test_missing_required_field_fails():
    for f in ("action", "domain", "level", "region", "fault_domain", "evidence", "reviewer"):
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


def test_l4_requires_dark_and_rollback_drills_and_a_pool():
    # missing the drills
    errs = validate(_s(level="L4", region="serving-pool-west",
                       certified_by=["decode-replica-kill"],
                       evidence=["runs/full.json"]), "t")
    assert errs and any("control-plane-dark" in e for e in errs)
    # complete L4
    ok = validate(_s(level="L4", region="serving-pool-west",
                     certified_by=["decode-replica-kill", "planner-kill-mid-heal"],
                     evidence=["full.json", "rollback-drill.json", "control-plane-dark.json"]), "t")
    assert ok == [], ok


if __name__ == "__main__":
    for fn in sorted(k for k in dir() if k.startswith("test_")):
        globals()[fn]()
        print(f"ok {fn}")
    print("all promotion-gate tests passed")
