# Autonomous networks: the same discipline, a different domain

Reference numbers point to [REFERENCES.md](../REFERENCES.md). This chapter instantiates the [governed-autonomy architecture](architecture.md) for networks — DC fabric, IP, optical, and RAN as bounded autonomous domains. The claims are aligned to the standards bodies that own this space; where an earlier draft paraphrased loosely, the corrected framing is stated.

## Domains, not one brain

Each domain runs its own closed loop with its own telemetry, allow-list, checker, and referee; a supervisor passes only **intent and constraints** across boundaries. This is the split Ericsson publishes: an *autonomous domain* with an intent manager that "controls the assurance loop for domain-specific requirements within the domain," and conflict handling done as a utility-maximizing compromise *inside* the coordinating intent managers — not a separate boundary box [15].

| Domain | Loop speed | Typical actuators |
|---|---|---|
| DC fabric / AI rail | seconds | NIC, leaf, PFC, ECMP, NCCL path |
| IP / PE | seconds–minutes | IGP/BGP, SR, QoS, ACL |
| Optical / OLS | minutes | WDM plan, restore path, power |
| RAN | ms (xApp) to minutes (rApp) | RIC, PCI, power, mobility |
| E2E service | minutes | cross-domain intent broker |

## Autonomy levels — align to TM Forum, precisely

TM Forum's Autonomous Networks framework defines levels **L0–L5** (IG1218 framework; IG1252 evaluation methodology [16]), scored across five operational dimensions — Intent/Experience, Awareness, Analysis, Decision, Execution — each assigned to People or System. Two corrections a careful reader will expect, and that this repo makes:

- **At L4, intent is *shared* (People/System), not human-owned.** "The human owns intent" strictly describes **L3**. IG1218's L4 language is a "cross-domain environment... driven by intent"; the *per-domain* framing this repo uses is how L4 is actually **certified**, not how it is defined — the first industry L4 certification (2025) covered one scenario in one domain [16].
- **The industry sits at L1–L2, with L3 the near-term target.** TM Forum survey data places most operators' most-mature domain at L1–L2; ~17% reached L3 in at least one domain in 2025 [16]. "Most operators at L2–L3" overstates by roughly a level.

## O-RAN timescales — the reflex tier is architecturally empty

O-RAN integrates three control loops [7]: **non-real-time** (rApps, >1 s), **near-real-time** (xApps, 10 ms–1 s), and **real-time** (<10 ms) — which lives in the O-DU/O-RU and is explicitly **not in a RIC**; the specs "lack a practical approach" below 10 ms, and the proposed extension (dApps on the DU) is the [latency hierarchy's](latency-hierarchy.md) "compile downward" move again. The intent-to-action schema layer is real and standard: TMF921 intent objects, YANG/NETCONF/gNMI for configuration, O-RAN A1/E2 for RIC policy [7, 16].

## The pattern already exists in the field

Loosely stated in an earlier draft as "field patterns"; here demonstrated with concrete public work (2024–2026):

- **Multi-agent optical OAM** (plan/task hierarchies) — IEEE ComMag 2025 and a production million-link optical-management demo [17].
- **RIC fabrics decomposing natural-language intent across timescales** — ORION (MCP-based SMO + rApp + xApp closed loop) and AgentRAN (rApps/xApps/dApps decomposition) [17].
- **MCP toolboxes on real optical line systems** — *demonstrated* (one public IPoDWDM-via-MCP paper), not yet plural [17].

Copy the *shape* — hierarchy, typed tools, a checker/twin before every write — not the anti-pattern of a single LLM holding the whole NOC's credentials.

## Honest scope

This is an architecture and a standards-aligned map, not a network simulator. The RAN loop-speed row is a published shorthand ("10 ms–1 s" is the exact near-RT figure); the "field patterns" rest on early public demonstrations, not deployment at scale; and the governance mechanism (typed actuators + referee + certified promotion) is asserted as the right shape, evidenced by the field examples, not benchmarked here. The quantitative artifact in this repo is the [latency hierarchy](latency-hierarchy.md); the network domain is treated at the architecture level.
