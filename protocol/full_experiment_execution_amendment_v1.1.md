# Full Experiment Execution Amendment v1.1

Status:
FROZEN TECHNICAL AMENDMENT
FINAL RUNTIME GATE REQUIRED BEFORE EXECUTION

Project:
Cross-LLM Generalization of Behavioral Detection for
Indirect Prompt Injection in LLM Agents

## 1. Reason for Amendment

During pre-execution verification, P2 discovered that
runner v0.2.1 could not directly execute the frozen
per-run execution manifest.

The original runner expanded logical scenarios across
three models and generated its own run identifiers.

Runner v0.2.2 adds manifest-driven execution and
preserves the canonical run_id.

This incompatibility was identified before any Full
Experiment outcomes were collected.

## 2. Technical Changes

- Runner: 0.2.1 -> 0.2.2
- Execution Manifest: v1.0 -> v1.1
- Manifest-driven per-run execution
- Canonical run_id preservation
- Attempt-aware evidence and snapshots
- Additional runtime and identity checks

The model adapter and experimental scenario logic
remain unchanged.

## 3. Isolated Ollama Instance

Reason:
The original Ollama installation was automatically
updated to 0.35.1 under another Windows account.

P2 cannot modify that session.

Approved isolated instance:
- Server version: 0.35.0
- Endpoint: http://127.0.0.1:11435/v1
- Separate remote account
- Separate model storage
- Same physical machine and GPU
- Context length: 16384

All three model digests matched the frozen values.

GPU coordination:
P1 confirms that the machine owner will not use
the shared GPU during Full Experiment execution.

GPU and runtime isolation must still be checked
immediately before and during execution.

## 4. Evidence Review

P2 isolated-instance smoke tests:
151/151 PASS

Verified:
- Three source LLMs
- Benign scenarios
- Single-vector IPI
- Multi-vector IPI
- Frozen payload SHA256
- Canonical run_id
- Fresh environment
- Tool calling
- Retry identity
- Attempt-specific evidence
- Generation request configuration

Smoke runs are non-experimental and must not enter
the Full Experiment dataset.

## 5. Scientific Configuration

UNCHANGED:
- AgentDojo benchmark v1
- 388 logical conditions
- 88 benign logical conditions
- 300 IPI logical conditions
- 1,164 execution rows
- Train/validation/test assignment
- User-task groups
- Attack scenario selection
- Injection vectors and payloads
- Model identities and digests
- Temperature: 0.0
- Seed: 42
- Context: 16384
- Parallel tool calls: false
- Prompts, tools, and evaluation semantics

No Full Experiment outcomes were used for
experimental design decisions.

## 6. Source Manifest v1.0

SHA256:
3135d4e5a18d2bc72b897a239b87c4f1d156f97cfdae48a9a5e1e30a6bc921cf

## 7. Final Manifest v1.1

File:
full_experiment_execution_manifest_v1.1.csv

SHA256:
94760ff7985160b903b4d037940f2a3c1363d11e662eb77f0be66b68b30214ac

Exactly two CSV fields changed:
- execution_manifest_version
- runner_version

All other fields remain unchanged.

## 8. Execution Authorization

The execution definition is frozen.

This amendment does not authorize the experimental
batch to start automatically.

P2 must receive the v1.1 handoff and demonstrate:
- Correct artifact checksums
- Correct runner v0.2.2
- Correct Ollama server v0.35.0
- Matching model digests
- Context 16384
- No conflicting GPU usage
- Successful final runtime gate

P2 must STOP on any mismatch.

Full Experiment execution requires final P1 GO.

## 9. Provenance

The frozen v1.0 manifest and original evidence
must be retained without modification.

Any further execution-environment change requires
a separately documented amendment.
