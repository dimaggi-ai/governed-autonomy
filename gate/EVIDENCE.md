# Promotion evidence trust boundary

`validate` and the installed CLI enforce authenticated receipts at L2–L4.
`validate_structure` is a declaration linter only: its synthetic tests and
bundled YAML examples do not establish an earned promotion. Existing examples
without authentic receipts now fail the full gate deliberately.

Run `promotion-gate --trust /secure/runner-policy.json --evidence-root /runs RECORD.yaml`.
The policy is supplied by the verifier, not by the record. It maps issuer IDs
to a secret `key_hex` (at least 32 random bytes), `allowed_actions`,
`allowed_domains`, `allowed_regions`, and `execution_kinds`.

The policy also supplies `predicate_limits`: `abort_never_missed_deadline_ms`,
`rollback_deadline_ms`, `max_relative_regression`, `min_canary_samples`,
`budget_gpu_seconds`, and `min_dark_duration_ms` for the predicates required at
the requested level. These bounds are chosen by the verifier, never relaxed by
the result producer.

Every artifact must be JSON with schema `infra-experiment-result/v1`, matching
`implementation_sha256`, `experiment`, and `execution_kind`, and an
`observations` map of predicate names to nonempty trial lists. The verifier
computes predicate success from trial contents:

- Abort/rollback: triggered, completed within deadline, safe terminal state.
- Reversibility: applied action and matching before/restored state digests.
- Canary: baseline/canary goodput, sample count and regression within policy.
- GPU budget: observed wasted GPU-seconds within the authorized budget.
- Dark control plane: unavailable controller, local safety held, sufficient duration.

The signed `checks` map must match these computed predicates. A valid signature
over invalid content fails. Missing predicates and inconsistent metadata fail.
These checks establish internal consistency and policy conformance, not that
an authorized runner could not lie about its observations. Tests cover L2 and
the full set of L4 receipt predicates with ephemeral simulation-only keys.

Run `python -m unittest gate.test_evidence` for the signed adversarial fixtures.

Receipts use `infra-evidence-receipt/v1`. Their HMAC-SHA256 binds the promotion
declaration, `implementation_sha256`, certifying experiment, artifact hashes and
runner-evaluated checks. The operator must verify that the declared implementation
digest identifies the code being promoted; the runner attests what it tested.
The verifier checks actual artifact bytes and refuses missing files, changed
declarations, unauthorized scopes and invalid signatures. Higher levels require
authenticated canary/budget and dark-control-plane/rollback predicates.

This is local shared-secret runner authentication, not public non-repudiation.
The trusted runner must compute checks honestly and protect its key; a key
holder can fabricate attestations. Hashes alone do not prove execution. A
simulation receipt remains simulation evidence and must not be authorized by
a production trust policy. No production keys or trusted production runners
are supplied. Unit tests generate ephemeral keys and explicitly trust only a
simulation fixture; they demonstrate the gate, not hardware remediation.
