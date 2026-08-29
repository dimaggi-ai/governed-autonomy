# The latency hierarchy: how far down can certified policy be pushed?

Reference numbers point to [REFERENCES.md](../REFERENCES.md). The figure is generated from one sourced data file, [`exhibit/latency_ladder.json`](../exhibit/latency_ladder.json) (`python3 exhibit/latency_ladder.py`); this table is its human-readable companion, and the two share the same rungs and sources.

## The thesis

**Autonomy does not make any single loop faster. It certifies policy at slow, human timescales and compiles it downward into pre-authorized reflexes that execute without deliberation.** Nanosecond *decision-making* does not exist anywhere in the record; nanosecond *enforcement of certified policy* already ships, in switch ASICs. So "can infrastructure react in nanoseconds?" is the wrong question. The right one is: **how far down the latency hierarchy can a governed system push policy it has certified** — and the answer today is already below a microsecond for simple compiled models, while the sub-10-millisecond control tier in RAN has no standardized programmable point at all yet.

![The latency hierarchy](../figures/latency_ladder.png)

## The hierarchy (grouped by tier; each rung sourced; the figure sorts strictly by timescale)

| Rung | Timescale | Nature | Source |
|---|---|---|---|
| Clock sync (White Rabbit / PTP-HA) | sub-ns (<10 ps over 5 km) | measurement substrate | [1] |
| Switch ASIC cut-through forwarding | ~100 ns (sub-90–130 ns published) | fixed reflex | [2] |
| Per-packet adaptive routing; P4 match-action | ns per packet at 400 G | parameterized reflex (Subnet Manager configures, ASIC decides) | [3] |
| Inline FEC (RS-544 "KP4") | ~100–200 ns, in the PCS | fixed reflex | [4] |
| PFC pause loop | ~1–10 µs | fixed reflex | [5] |
| IB SHIELD self-healing link reroute | µs–ms ("1000× faster than software") | pre-armed reflex | [3] |
| BFD detection floor | 3.3 ms × 3 ≈ 10 ms | detection | [6] |
| Real-time RAN loop (O-DU/O-RU) | <10 ms — **no standardized control point yet** | in-stack control | [7] |
| Near-RT RIC xApps | 10 ms – 1 s | tactical control | [7] |
| OCS hardware switch (MEMS) | ms (production loop: drain→reconfigure→BERT = minutes) | actuator vs certified loop | [9] |
| IP FRR / TI-LFA / optical APS | <50 ms (pre-computed backup paths) | pre-computed protection | [10] |
| IGP convergence (tuned) | hundreds of ms – 1 s | distributed recompute | [11] |
| Non-RT RIC rApps | >1 s (tens of s – min) | policy tier | [7] |
| gNMI telemetry (practice) | 1–30 s typical (spec allows ns) | sensing | [12] |
| BGP convergence (node failure) | seconds – minutes | inter-domain | [11] |
| LLM planner deliberation | seconds – minutes | cognition | [13] |
| TMF intent / assurance loop | minutes | intent tier | [14] |

## What the hierarchy proves

- **Every nanosecond mechanism is a reflex whose parameters were installed by a slower authority.** The Subnet Manager configures adaptive-routing groups; the ASIC picks the port per packet [3]. The P4 compiler installs the pipeline; the pipeline enforces per packet — sub-microsecond in-network inference has been demonstrated on programmable switches [3]. FEC parameters are standardized once and executed forever [4]. No cognition exists at nanosecond timescale anywhere.
- **The 50 ms protection lineage is a 30-year-old instance of the thesis:** "pre-computed backup paths and pre-installed forwarding state, not real-time path computation during a failure event." TI-LFA (RFC 9855) is certified-policy-compiled-downward, standardized [10].
- **O-RAN institutionalizes the timescale separation** the thesis needs: cognition in rApps (>1 s), tactical control in xApps (10 ms–1 s), and an explicit architectural *absence* below 10 ms — the specs "lack a practical approach" there; the proposed fix (dApps on the DU) is compiling downward again [7, 8].
- **OCS is the cleanest case study:** the hardware switches in milliseconds, but production reconfiguration is drain → reconfigure → BERT-qualify → release, orchestrated by SDN — **certification dominates the loop time, deliberately** [9].

## Three honest qualifications

1. **The nanosecond tier is thinner than the slogan.** Genuinely ns: forwarding, FEC, per-packet path choice. Then µs (PFC), then ms (BFD, FRR, OCS). "Nanosecond-scale autonomous decision-making" is not plausible on current evidence; nanosecond-scale *enforcement of certified policy* is already shipping.
2. **The sensing floor binds before the actuation floor.** gNMI telemetry runs at 1–30 s in practice [12], so any centrally-fed loop closes at seconds no matter how fast the decision. Policy can be pushed down only as far as *local* sensing exists — which is exactly what the reflex tier has.
3. **The frontier is moving from below.** OCS forecasts µs switching [9]; P4 puts small *learned* models (never LLMs) at line rate [3]; dApps target sub-10 ms RAN [7, 8]. "How far down can certified policy go" is a live engineering race — and the answer is already sub-microsecond for compiled models.

This is why the [governed-autonomy architecture](architecture.md) separates the planes it does, and why promotion up the [autonomy ladder](architecture.md#the-autonomy-ladder) is gated on certification — the same discipline the [chaos-fidelity standard](https://github.com/dimaggi-ai/ai-cluster-chaos-fidelity) enforces one layer down.
