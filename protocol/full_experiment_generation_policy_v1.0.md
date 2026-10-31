# Full Experiment Generation Policy v1.0

Current status:
FROZEN FOR FULL EXPERIMENT REP1

Project:
Cross-LLM Generalization of Behavioral Detection for Indirect Prompt Injection in LLM Agents

## Purpose

This policy defines the frozen generation and execution configuration for Full Experiment Rep1, finalized before any Full Experiment outcome is observed.

P2 completed the configuration-only smoke test through the same runner/API path intended for the Full Experiment, and P1 reviewed and approved the result.

The Generation Policy is frozen for Rep1. The Full Experiment itself is not execution-ready until fixed IPI injection configuration, preflight validation, and the Final Execution Manifest are completed.

## Source LLMs

LLM-A:
- gemma4:12b

LLM-B:
- qwen3.5:9b

LLM-C:
- lfm2.5

Exact local model digests must be recorded by P2 before Full Experiment execution.

## Benchmark baseline

AgentDojo package:
- 0.1.35

AgentDojo commit:
- a75aba7631d3ca5fb7ab938965c97ead2f9ff84b

Benchmark:
- AgentDojo v1

## Core execution settings

Context window:
- 16384

Maximum agent steps:
- 15

Per-run timeout:
- 180 seconds

Maximum retries:
- 4 retries for execution/runtime failures only

Environment:
- Fresh AgentDojo environment for every experimental run

Parallel tool calls:
- false

The same system-prompt policy, tools, permissions, workflow, scenario, external content, and injection configuration must be used across the three source LLMs for the same logical condition.

## Generation settings

Temperature:
- 0.0 (explicit)

Seed for Full Experiment Rep1:
- 42 (explicit)

These values are proposed before observing any Full Experiment outcomes.

P2 must confirm through the actual Full Experiment runner path that these settings can be supplied consistently to all three source LLMs.

## Thinking / reasoning mode

Pilot runner inspection confirmed that no explicit `think`, `thinking`, or `reasoning_effort` parameter was supplied to Ollama for Gemma, Qwen, or LFM.

All three source LLMs used the same OllamaAdapter logic.

No model-specific thinking logic was present in the Pilot runner.

For the Full Experiment, the proposed thinking policy is:

- Do not explicitly send `think`.
- Do not explicitly send `thinking`.
- Do not explicitly send `reasoning_effort`.
- Allow each source LLM to use its native model/Ollama default reasoning behavior.
- Hidden reasoning must not be collected.
- Hidden reasoning must not be used as detector input.

Rationale:

P2 testing showed that Gemma and Qwen can disable thinking explicitly, while LFM cannot be equivalently disabled.

Forcing `think=false` would therefore create an asymmetric configuration in which Gemma and Qwen operate without thinking while LFM continues to use thinking behavior.

Using the same runner logic and omitting model-specific thinking overrides provides a common interface-level policy across all three source LLMs.

This does not imply that the amount or implementation of internal reasoning is equal across models.

Differences in native reasoning behavior are treated as a limitation and as part of the native source-model behavior.

Pilot inspection also showed:

- Gemma and Qwen exposed thinking separately from final content.
- The Pilot runner did not collect that separate thinking content.
- LFM emitted reasoning inside `<think>...</think>` content.
- The Pilot runner removed these blocks before storing the final trajectory.
- No `<think>` or `</think>` tags were found in the final 54-run `raw_runs.jsonl`.

## Relationship to Pilot

The Pilot consisted of:
- 9 scenario groups
- 2 conditions
- 3 source LLMs
- 1 repetition
- 54 completed runs

The Pilot was development/qualification data.

During the Pilot:
- temperature was not explicitly set;
- seed was not explicitly set;
- thinking was not explicitly overridden;
- all three models used their native/default reasoning behavior;
- hidden reasoning was not retained in the final trajectory dataset.

The Pilot is not pooled with the final controlled Full Experiment dataset.

The 9 Pilot user-task groups have been excluded from the main Full Experiment candidate pool.

## Additional decoding parameters

Before this policy is frozen, P2 must report the behavior of:

- top_p
- top_k
- num_predict / maximum output tokens

For each parameter, P2 must report whether:
- it is explicitly supplied by the runner;
- it is omitted;
- a model/Ollama default is inherited.

These parameters must not be changed after Full Experiment outcomes are observed.

## Retry policy

Retries are allowed only for execution/runtime failures.

Retries are not experimental repetitions.

Every attempt must be logged.

Failed attempts must not be overwritten.

The accepted attempt must remain identifiable.

## Repetition policy

Primary Full Experiment:
- Rep1 only

A future Rep2 requires a separate predeclared repetition policy.

Rep2 must not overwrite Rep1.

## Required reproducibility metadata

P2 must record:

- model role
- exact model name
- exact model digest
- Ollama version
- AgentDojo version
- AgentDojo commit
- generation settings
- context length
- maximum agent steps
- timeout
- retry and attempt information
- thinking/reasoning configuration
- timestamps
- run_id
- logical condition ID
- scenario group ID
- split assignment
- repetition ID

## Required configuration-only smoke test

Before this policy can be frozen, P2 must test all three source LLMs through the same `/v1/chat/completions` runner path intended for the Full Experiment.

The smoke test must confirm:

1. temperature=0.0 can be supplied consistently.
2. seed=42 can be supplied consistently.
3. no `think`, `thinking`, or `reasoning_effort` override is sent.
4. parallel_tool_calls=false is retained.
5. top_p handling is documented.
6. top_k handling is documented.
7. num_predict / maximum-output-token handling is documented.
8. effective context length remains 16384.
9. tool calling works for all three models.
10. no unexpected response truncation or configuration error occurs.

The smoke test is technical validation only.

Smoke-test runs must not be included in the Full Experiment dataset.

## Freeze condition

This Generation Policy becomes frozen for Full Experiment Rep1 only after:

1. P2 completes the required configuration-only smoke test.
2. P2 reports the resolved configuration to P1.
3. P1 reviews the result.
4. Any necessary technical corrections are made before experimental execution.
5. P1 explicitly marks the policy as frozen.

Until then:

- Generation Policy status = FROZEN FOR FULL EXPERIMENT REP1
- Generation Policy frozen = true
- Full Experiment execution ready = false
  - Pending fixed IPI injection configuration, preflight validation, and Final Execution Manifest.


## Final configuration smoke-test approval

P2 completed the final configuration-only smoke test using runner v0.2.1 through the actual `/v1/chat/completions` path.

The smoke test was technical validation only and was not included in the experimental dataset.

Resolved Full Experiment Rep1 configuration:

- runner_version: 0.2.1
- temperature: 0.0 explicit
- seed: 42 explicit
- parallel_tool_calls: false
- think/thinking/reasoning_effort: omitted
- top_p: omitted / inherited default
- top_k: omitted / inherited default
- num_predict/max_tokens: omitted / inherited default
- context length: 16384
- maximum agent steps: 15
- timeout: 180 seconds
- fresh environment: required for every experimental run
- Ollama version: 0.35.0
- AgentDojo package: 0.1.35
- AgentDojo reference commit: a75aba7631d3ca5fb7ab938965c97ead2f9ff84b

Expected model identities:

- LLM-A requested/resolved model: gemma4:12b
  digest: 6114515d63c17436a7c0417d82820ac65ad643e2806c5a3c89cb62846436ed0b

- LLM-B requested/resolved model: qwen3.5:9b
  digest: 6488c96fa5faab64bb65cbd30d4289e20e6130ef535a93ef9a49f42eda893ea7

- LLM-C requested model: lfm2.5
  resolved model: lfm2.5:latest
  digest: 9cf756159fc2f3b9128c6a3f544ec90c5e9b8afdbb4179a57b8aea9de589cfb2

All three models passed:
- explicit temperature=0.0
- explicit seed=42
- tool-call generation and parsing
- digest verification
- effective context length 16384
- metadata logging
- generation-settings logging
- no observed truncation
- no configuration errors

Before Full Experiment execution, P2 must:
- launch/configure Ollama with context length 16384;
- verify all three expected model digests;
- stop and notify P1 if any digest does not match.

The Generation Policy is frozen before observing any Full Experiment outcomes.

Changes to this policy after Full Experiment execution begins require a new documented protocol version and must not be silently mixed with Rep1.
