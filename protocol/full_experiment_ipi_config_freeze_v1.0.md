# Full Experiment Fixed IPI Configuration Freeze v1.0

Status:
FROZEN FOR FULL EXPERIMENT REP1

This record freezes the concrete IPI configuration generated under
Full Experiment Injection Policy v1.0 before any Full Experiment outcome
is observed.

## Scope

Selected IPI logical conditions:
300

Unique user-task groups:
88

Split:
- Train: 182 IPI
- Validation: 57 IPI
- Test: 61 IPI

## Injection-vector configuration

Native AgentDojo InjecAgentAttack / FixedJailbreakAttack behavior is used.

For each user task, the generated payload is placed into every injection
candidate returned by AgentDojo get_injection_candidates(user_task).

Vector-count distribution across the 300 IPI conditions:

- 1 vector: 212
- 2 vectors: 49
- 3 vectors: 14
- 4 vectors: 15
- 5 vectors: 3
- 7 vectors: 4
- 10 vectors: 3

All generated vector sets matched the frozen vector-candidate audit.

All multi-vector conditions use an identical payload across their configured
vectors.

## Frozen artifacts

1. full_experiment_fixed_ipi_payloads_v1.0.json

SHA256:
f16129d0234bbc9ccf4566ad8a085745a9c5ab51243d5a87efcdb4232a9723c7

This file contains the complete fixed payload and vector-to-payload mapping
for every selected IPI condition.

2. full_experiment_fixed_ipi_config_v1.0.csv

SHA256:
824fe1286f883350367948ff55bb4f0615aa05eabe444c6de5d23546661b261a

This file contains the compact fixed IPI configuration and payload identity
for every selected IPI condition.

## Validation completed

- Payload records: 300
- Unique user-task groups: 88
- Train/validation/test counts match the selected-pair manifest
- Frozen candidate-vector audit match: PASS
- Multi-vector payload identity: PASS
- User-task semantic consistency after whitespace canonicalization: PASS
- Injection-task attacker-goal exact consistency: PASS

Whitespace canonicalization was used only for validating the CSV user-task
snapshot against the frozen AgentDojo PROMPT. Experimental execution continues
to use the original AgentDojo PROMPT unchanged.

## Reproducibility rule

The same fixed IPI configuration must be used for the corresponding logical
condition across Gemma, Qwen, and LFM.

No payload, injection vector set, or attacker goal may be changed based on
source-model behavior or Full Experiment outcomes.

Any post-freeze modification requires a new documented version and must not
be silently mixed with Rep1.
