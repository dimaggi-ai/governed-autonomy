# Governed autonomy: the controller that must pass the experiments

Reference numbers point to [REFERENCES.md](../REFERENCES.md).

An autonomous controller for GPU clusters and networks is not a clever chat on top of Kubernetes. It is a closed loop that senses the factory, predicts a contract violation, picks an action from a bounded set, executes it through typed tools, and verifies the same numbers the [chaos-fidelity standard](https://github.com/dimaggi-ai/ai-cluster-chaos-fidelity) made falsifiable. An LLM plans; it does not hold cluster credentials. Autonomy is certified per action class, not declared for the platform.

## Five planes

Keep them separate so a planner cannot edit policy by talking well.

- **Intent & policy** — SLOs, power caps, tenancy, the allow-list, the operating region. Humans change this plane; agents do not.
- **Decision** — supervisor, specialist agents, referee. The LLM lives only here.
- **World model** — current state plus a short forecast, plus a *narrow* checker (not a fantasy digital twin): gang-legal placement, power envelope, replica-ratio arithmetic, the checkpoint digest of what is actually mounted. If the checker says no, the plan does not ship.
- **Sense** — DCGM, node conditions, fabric counters, NCCL errors, pending GPUs, checkpoint hashes, TTFT/ITL, the frozen quality slice, wasted GPU-seconds. No opinions.
- **Actuation** — typed tools with a schema, timeout, dry-run, and a structured result. Credentials live here, scoped per tool, never on the planner.

## The referee is a separate identity

| Role | Output | Forbidden |
|---|---|---|
| Detector | fault class, layer, blast estimate | any write |
| Diagnostician | ranked causes with evidence | any write |
| Healer | an allow-listed plan | novel commands |
| Referee | approve / rewrite / deny + the log | skipping the log |

**If one process can both propose and approve a drain, you have no control plane.** Every promotion record names `proposed_by` as well as `reviewer`, and the gate refuses the record when they are the same identity — the claim is checked, not asserted. The referee is a runtime policy firewall — DIMAGGI's [Tool Guard](https://dimaggi.ai) is exactly this class of component: it intercepts each tool call *before execution* and applies allow / deny / redact / escalate against declared policy, with a tamper-evident audit trail. This is the same enforcement the chaos standard names for the observation plane (agents *reading* `sacct`), extended to actuation (agents *operating* the cluster and the network).

## One cycle

`observe → estimate → plan → check → authorize → execute → verify → record`

Authorization depends on the action's **certified level**; verify uses the chaos contract for that class; the record (plan, tool traces, metrics) is the only legal source for "we healed it." The controller **fails closed when the brain is dark**: if the planner, the referee, or the tool bus is down, writes stop — it does not half-heal.

## The autonomy ladder

An action class is promoted only on evidence, and the evidence is a green chaos experiment. This is the machine-checkable link to the standard: every catalog spec carries an `autonomy_class`, and a promotion record must cite the experiment that certifies it (validated by [`gate/`](../gate/)).

| Level | Authorizes | Promotion evidence |
|---|---|---|
| **L0** | human only | — (novel fabric, power interlocks, production training topology) |
| **L1** | human signs the plan | checker + dry-run clean |
| **L2** | auto inside the region | a named chaos experiment green N times, abort never missed |
| **L3** | auto, sampled review | quality canary + wasted-GPU-second budget held on live traffic |
| **L4** | auto in one declared pool | full domain suite + rollback drill + control-plane-dark drill |

L4 is a pool, not the company. The region is written on the promotion record: cluster, tenant, action, level, evidence paths.

### Actions that span halls have a ceiling instead

A job that crosses halls is governed by one extra rule: for the span action set, the binding constraint is not how well the behaviour is understood but **whose work the action spends**. Requesting a circuit, cordoning a slice and delaying a checkpoint touch only the job that asked, and cap at L1. Draining, moving, resharding and reconstituting cross a tenant boundary, and are L0 whatever the slice is worth — the ceiling is a property of the action, so no experiment lifts it. Retuning a circuit under a live collective has no level at all. Every span record also carries a blast radius, stated as a tail rather than a mean, and a span that can black-hole two halls is L0 however good the latency looks. The table and the reasoning: [docs/span-actions.md](span-actions.md).

## Why this is one architecture for two domains

The same five planes, the same referee, the same ladder govern an AI **cluster** and an autonomous **network** — they differ only in their actuators (cordon/drain/reshard vs `reroute_sr`/`open_restore_path`/`set_ric_policy`) and their timescales. The [autonomous-networks chapter](autonomous-networks.md) instantiates the network side; the [latency hierarchy](latency-hierarchy.md) is why the *decision* plane stays at seconds while enforcement compiles downward into the reflex tier. The bridge between the two domains is intent, not shared credentials: the cluster controller may **emit** a network intent ("keep rail R at X for 12 h") and the network domain may **emit** a constraint back ("rail R has 20 min of restore budget") — neither holds the other's tools.
