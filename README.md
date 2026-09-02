# Governed Autonomy for GPU Clusters and Networks: from human intent to nanosecond in-ASIC reflexes

**An autonomous control plane for infrastructure is not an LLM with `kubectl` — it is a governed loop where a planner proposes, a separate referee approves against an allow-list, typed tools execute, and every action is verified against a certified contract.** This repository is the architecture for that loop across two domains — the AI cluster and the autonomous network — plus its signature evidence: a sourced **latency hierarchy** showing that autonomy never makes one loop faster; it certifies policy at human timescales and compiles it downward into pre-authorized reflexes.

**The through-line:** the [chaos-fidelity standard](https://github.com/dimaggi-ai/ai-cluster-chaos-fidelity) certifies that a recovery behavior *works*; [reliability economics](https://github.com/dimaggi-ai/reliability-economics) prices *what it is worth*; this repo is the controller that must *pass those experiments before it is trusted* — and the promotion of any action up the autonomy ladder is machine-checked against exactly that evidence [18].

*Fifth in the DIMAGGI series on turning GPU capital into usable compute. All claims trace to [REFERENCES.md](REFERENCES.md).*

---

## The signature exhibit: the latency hierarchy

![The latency hierarchy](figures/latency_ladder.png)

"Can infrastructure react in nanoseconds?" is the wrong question — nanosecond *decision-making* exists nowhere. The right question is **how far down the latency hierarchy a governed system can push policy it has certified**, and the answer is already sub-microsecond for compiled models (switch-ASIC forwarding, per-packet adaptive routing, in-network P4 inference at <450 ns/packet) while the sub-10 ms RAN tier has no standardized control point at all yet. Every rung is sourced; the figure and table are generated from one data file. Full reasoning: [docs/latency-hierarchy.md](docs/latency-hierarchy.md).

## The architecture

Five planes kept separate so a planner cannot edit policy by talking well (intent, decision, world-model + checker, sense, actuation); a **referee that is a different identity from the healer** ("if one process can propose and approve a drain, you have no control plane"); a cycle that **fails closed when the brain is dark**; and an **autonomy ladder (L0–L4)** where an action class is promoted only on the evidence of a green chaos experiment. Details: [docs/architecture.md](docs/architecture.md). The same discipline instantiated for DC fabric, IP, optical, and RAN — aligned precisely to TM Forum's AN levels and O-RAN's loop timescales — is in [docs/autonomous-networks.md](docs/autonomous-networks.md).

## The machine-checkable artifact: the promotion gate

Consistent with the standard's "fail the PR, not the prose" ethos, [`gate/`](gate/) refuses an autonomy-promotion record that claims a level it has not earned: L2+ must cite a certifying chaos experiment, carry evidence, and hold its abort; L4 needs a control-plane-dark drill and a rollback drill in one declared pool; irreversible fault domains (production training fabric, power interlocks) are capped at L1; and every record names who proposed the action as well as who approved it, because a record where those are one identity is not a control plane.

Actions that **span halls** are governed by a ceiling rather than by evidence, because what caps them is whose compute they spend: requesting a circuit or cordoning your own slice reaches L1, draining or moving a neighbour is L0 however green the experiment, and retuning a circuit under a live collective has no level at all. Every span record states its blast radius as a tail rather than a mean — average link utilisation is how operators get surprised — and a span that can black-hole two halls goes to a person whatever the latency looks like. See [docs/span-actions.md](docs/span-actions.md).

```
pip install pyyaml matplotlib
make test        # gate validator + rejection tests + both validation halves
make exhibit     # regenerate the latency-hierarchy figure from its data file
```

Or install the gate as a command and check your own promotion records:

```
pip install governed-autonomy-gate         # from PyPI
promotion-gate my-promotion-record.yaml    # refuse it if the level is unearned
promotion-gate                             # check the bundled reference records
promotion-gate --help                      # usage
```

(Or install the latest from source: `pip install git+https://github.com/dimaggi-ai/governed-autonomy`.)

## The validation project

This repo is an architecture and a sourced map, not a simulator — so its
validation is scoped to what it actually claims, and both halves run in
CI. **Public data:** [exhibit/validate_ladder.py](exhibit/validate_ladder.py)
checks **16 of the 17** rungs of the latency hierarchy against the band
its own cited source publishes — and, separately, that each rung *cites
the reference the band was restated from*, so a rung and its citation
cannot drift apart. Bands are the source's own numbers, not looser
prose: the QM8790 brief's sub-130 ns rather than a ~100 ns "class"
figure, Mission Apollo's stated 10–20 ms rather than "millisecond-scale",
and BFD's 3.3 ms×3 = 9.9 ms with a ±0.2 ms *rounding* tolerance rather
than a ±10% envelope. The 17th rung (IB SHIELD) has no published figure
to check against; it is named in the script's `UNCHECKED` list and
printed with the results rather than quietly counted as covered.

What this **cannot** do, and the script says so: a numeric band check
catches a rung that drifts from its source, but not a source
paraphrased wrongly in REFERENCES.md to begin with. It validates the
exhibit against its citations, not the citations against their sources.

Structural checks cover the thesis itself: every **deliberative** loop
(policy, cognition) is at least an order of magnitude slower than every
**mechanism** that executes pre-authorized policy (measurement, reflex,
detection, control) — 20× at present. Protection mechanisms like FRR
and BFD sit on the mechanism side deliberately: FRR is a certified
policy compiled downward, which *is* the thesis, not a counterexample.
A companion check asserts the partition covers every tier, so a new
tier cannot be added without deciding in code which side it falls on.

**Synthetic data:**
[gate/synthetic_gate_check.py](gate/synthetic_gate_check.py) generates
102 seeded synthetic promotion records across L0–L4 that must all pass
the real gate, then applies **36 single-rule mutations** — every rule,
in every form it can be violated (absent *and* explicitly false), and
every enumerated value (all five machine identities, all three capped
fault domains, both levels requiring a human reviewer) — and requires
each to be refused *for its own rule's stated reason*. The mutated
record is submitted under a neutral name and the reason is matched
against the message with that name stripped; submitting it under the
mutation's own label had made three assertions vacuous, since
"L3-no-canary" contains "canary". A validator proven only on its own
bundled examples proves nothing.

This harness found a real gate defect: the ladder was **not monotone**.
L4 used its own rules but skipped L3's, so an L4 promotion could be
claimed without a live-traffic quality canary or a held GPU-second
budget — the bundled L4 record had neither. The gate now reads
`level >= 3`, and both the record and a regression test were fixed.

```
make test    # gate + rejection tests + both validation halves
```

## Honest scope

This is an **architecture and a sourced latency map**, not a cluster or network simulator — the quantitative work lives in the sibling repos. The network chapter is standards-aligned prose evidenced by early public field demonstrations, not a benchmark; where an earlier draft paraphrased TM Forum or O-RAN loosely, the corrected framing is stated inline. The [latency hierarchy](docs/latency-hierarchy.md#three-honest-qualifications) carries its own qualifications (the nanosecond tier is thin; the sensing floor binds first).

## Series — turning GPU capital into usable compute

- **GPU Cluster Networking** ([network-vs-more-gpus](https://github.com/dimaggi-ai/network-vs-more-gpus))
- **GPU Cluster Scheduling** ([scheduler-vs-more-gpus](https://github.com/dimaggi-ai/scheduler-vs-more-gpus))
- **Chaos Fidelity Standard** ([ai-cluster-chaos-fidelity](https://github.com/dimaggi-ai/ai-cluster-chaos-fidelity)) — certifies the experiments
- **Reliability Economics** ([reliability-economics](https://github.com/dimaggi-ai/reliability-economics)) — prices which recovery policy wins where
- **Governed Autonomy** (this work) — the controller that must pass them, cluster and network

---

*Margaret (Maggie) Nanyonga — Founder & Principal Architect, [DIMAGGI AI](https://dimaggi.ai). Governed AI infrastructure: the control, reliability, and audit layer for autonomous systems operating production networks and compute.*
