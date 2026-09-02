# Span actions: the ceiling that evidence does not lift

A job that fits in one hall is governed by the ladder in
[architecture.md](architecture.md#the-autonomy-ladder): an action class earns
its level by passing a chaos experiment, and the promotion gate refuses a claim
the evidence does not support.

A job that **spans halls** needs one more rule, because for these actions the
binding constraint is not how well the behaviour is understood. It is whose work
the action spends.

## The action set is closed

There are eight things a controller may do to a spanning job, and the gate
refuses anything else by name rather than defaulting to permitted.

| Span action | Ceiling | Why the ceiling sits there |
|---|---|---|
| `request-circuit` | **L1** | Touches only the job that asked. The plant is somebody else's; the intent either compiles to the vendor API or is refused. |
| `release-circuit` | **L1** | Same, in reverse. |
| `cordon-slice` | **L1** | Shrinks the owning job. Keeps the hardware, breaks that job's checkpoint, costs nobody else. |
| `delay-checkpoint` | **L1** | Spends the owning job's progress, not a neighbour's. |
| `drain-slice` | **L0** | Evicts a neighbour to make room. |
| `move-job` | **L0** | Crosses a hall boundary and usually drains someone on arrival. |
| `shrink-job` | **L0** | Reshards, which is a different job than the one the tenant submitted. |
| `reconstitute-slice` | **L0** | Rebuilds a rectangle out of whatever is free, which is a placement decision about the whole pod. |

And one action with no level at all:

| Refused outright | Why |
|---|---|
| `retune-live-collective` | An OCS retune is milliseconds. A collective is microseconds. This is not an autonomy level; it is an outage with a plan attached. No record for it is valid at any level. |

**The line is a tenant boundary, not a size.** Draining one small neighbour is
L0 and cordoning a large slice of your own job is L1, because the question the
level answers is whose compute is being spent, not how much. That also means no
number of green experiments promotes an L0 span action: the ceiling is a
property of the action, and the gate applies it before it looks at the
citations. `test_a_span_ceiling_is_not_liftable_by_evidence` hands a
`drain-slice` record everything L2 asks for — a certifying experiment, evidence
paths, a held abort, a reversibility claim — and it is still refused.

## Blast radius, stated as a tail

Every span record carries a `blast_radius`, and two things about it are checked.

**Two halls is L0.** A span that can black-hole more than one hall goes to a
person whatever its latency looks like. The controller is not being asked
whether the path is fast; it is being asked whether a single failure can take
out two rooms.

**A mean is not a blast radius.** The gate requires a `scope` — how far one
failure reaches: rack, hall, campus-ring — and a tail figure, `p99_rtt_ms` or
one of its siblings, and it refuses a radius that states only a mean. Average
link utilisation is how operators get surprised: it is healthy on exactly the
link whose p99 is about to end a training run. The mean may sit alongside the
tail; it may not stand in for it. The bundled `span-move-job-L0.yaml` record
keeps both, and its note says which one would have waved the move through.

## Actuate proposes, Guard approves

`proposed_by` is a required field on every record now, span or not, and the gate
refuses a record where it equals `reviewer`.

The repository has always said that if one process can both propose and approve
a drain you have no control plane. Until this rule it said so only in prose. The
field costs one line per record and turns the claim into something a CI run can
fail on.

The scope field is called `scope` and not `failure_domain` on purpose: the
record already carries `fault_domain` for the *kind* of fault, and two required
fields a letter apart is how the wrong one gets filled in.

## What this is not

The gate checks a record against a policy. It cannot check the record against
the world: nothing stops somebody writing `halls: 1` on a span that reaches
three, or naming a proposer who never proposed. What it buys is that the claim
has to be *written down* in a field a reviewer and a CI run can both see, which
is a smaller thing than verification and a larger one than prose.

These are **policy rules, not measurements**. Nothing here says how often a
two-hall span actually fails, what a retune actually costs a collective, or how
much a drain is worth. The gate checks that a promotion record is consistent
with a stated policy; whether the policy is right for a given plant is an
argument to have with the numbers, in the sibling repositories that carry them.
