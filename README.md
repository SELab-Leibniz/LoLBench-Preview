# LoLBench

A benchmark for evaluating coding agents on **large, real-world feature
implementations**.

Each LoLBench task asks an agent to implement a substantial feature described by
its original design document — a CPython **PEP**, an Apache Flink **FLIP**, an
Apache Kafka **KIP**, a Ruff **issue**, or a FastAPI **PEP** — and grades the
result against the test suite from the pull request that actually shipped it.

Each task pins the project at the **parent commit** of a merged PR and gives the
agent the feature's requirement document. The agent produces a patch; that patch
is graded in a separate Docker container against the PR's real tests.

A task is **resolved** when every **fail-to-pass (F2P)** test (failing before the
feature exists) now passes **and** every **pass-to-pass (P2P)** test (guarding
against regressions) still passes.

## The 20 tasks

| Task | Project | PR | Feature |
|------|---------|----|---------|
| `cpython_1`  | CPython | [#19503](https://github.com/python/cpython/pull/19503)   | PEP 617 — New PEG parser |
| `cpython_2`  | CPython | [#19909](https://github.com/python/cpython/pull/19909)   | PEP 615 — `zoneinfo` (IANA time zones) |
| `cpython_3`  | CPython | [#22917](https://github.com/python/cpython/pull/22917)   | PEP 634 — Structural pattern matching |
| `cpython_4`  | CPython | [#29581](https://github.com/python/cpython/pull/29581)   | PEP 654 — Exception groups & `except*` |
| `cpython_5`  | CPython | [#31498](https://github.com/python/cpython/pull/31498)   | PEP 680 — `tomllib` |
| `cpython_6`  | CPython | [#31018](https://github.com/python/cpython/pull/31018)   | PEP 646 — Variadic generics |
| `cpython_8`  | CPython | [#102855](https://github.com/python/cpython/pull/102855) | PEP 701 — Formalized f-strings |
| `cpython_9`  | CPython | [#101441](https://github.com/python/cpython/pull/101441) | PEP 709 — Inlined comprehensions |
| `cpython_10` | CPython | [#103764](https://github.com/python/cpython/pull/103764) | PEP 695 — Type parameter syntax |
| `cpython_11` | CPython | [#116129](https://github.com/python/cpython/pull/116129) | PEP 696 — Type defaults for type parameters |
| `cpython_12` | CPython | [#119891](https://github.com/python/cpython/pull/119891) | PEP 649 — Deferred annotation evaluation |
| `fastapi_1`  | FastAPI | [#4871](https://github.com/fastapi/fastapi/pull/4871)    | PEP 593 — `Annotated` |
| `flink_1`    | Apache Flink | [#9976](https://github.com/apache/flink/pull/9976)   | FLIP-77 — ConfigOptions with data types |
| `flink_7`    | Apache Flink | [#25731](https://github.com/apache/flink/pull/25731) | FLIP-467 — Generalized watermarks |
| `flink_9`    | Apache Flink | [#25978](https://github.com/apache/flink/pull/25978) | FLIP-499 — Event time by generalized watermark |
| `flink_10`   | Apache Flink | [#26001](https://github.com/apache/flink/pull/26001) | FLIP-501 — Window extension (DataStream V2) |
| `flink_11`   | Apache Flink | [#26567](https://github.com/apache/flink/pull/26567) | FLIP-498 — `AsyncTableFunction` |
| `kafka_1`    | Apache Kafka | [#7378](https://github.com/apache/kafka/pull/7378)   | KIP-470 — `TopologyTestDriver` usability |
| `kafka_2`    | Apache Kafka | [#11572](https://github.com/apache/kafka/pull/11572) | KIP-769 — Connect plugin-listing APIs |
| `ruff_1`     | Ruff (Rust)  | [#9599](https://github.com/astral-sh/ruff/pull/9599) | Issue-8368 — CLI config overrides |

Languages span Python/C (CPython), Java/Scala (Flink, Kafka), Python (FastAPI),
and Rust (Ruff). Each task ships as a self-contained
[Harbor](https://github.com/jasonlim-cerberus/swebenchpro-harbor) task under
[`harbor_tasks/`](harbor_tasks/), with its evaluation image published to Docker
Hub (`smartdub26/lolbench:<task>-1.0.0`).

## Quickstart

**Prerequisites:** Docker (with a running daemon) and enough free disk for a
multi-GB image, plus a model API key.

```bash
# 1. Install the Harbor CLI (one time).
uv tool install harbor          # https://docs.astral.sh/uv/ ; or: pipx install harbor

# 2. Get an API key for your model provider (reference setting: OpenRouter).
export OPENROUTER_API_KEY=sk-or-...          # https://openrouter.ai/keys

# 3. Run an agent on one task (OpenCode + DeepSeek-V4-Pro via OpenRouter).
harbor run \
  -p harbor_tasks/ruff_1 \
  -a opencode \
  -m openrouter/deepseek/deepseek-v4-pro \
  --job-name ruff_1_run --jobs-dir harbor_runs/ruff_1 \
  --no-delete -n 1 -y \
  --ve LOLBENCH_SUITE=union

# 4. Read the reward (1.0 = resolved, 0.0 = not).
cat harbor_runs/ruff_1/ruff_1_run/*/verifier/reward.json
```

The first run pulls the task's Docker image (multi-GB) and can take 20+ minutes;
later runs reuse the cached image.

**Full step-by-step reproduction** — including the oracle parity check, all suite
modes, and running all 20 tasks in parallel — is in [REPRODUCE.md](REPRODUCE.md).

## Anti-cheat

The gold answer for every task is a public merged PR, so LoLBench is hardened
against retrieval. During the agent phase the container is on a **network
allowlist** (only the model endpoints are reachable — `github.com`, `pypi.org`,
package mirrors, and Maven Central are all blocked), and the images are built with
the upstream git history pruned, unreachable objects dropped, and auxiliary source
trees removed. Grading runs in a separate, isolated container. See
[ANTI_CHEAT.md](ANTI_CHEAT.md) for the full policy.

## Choosing a different agent or model

Harbor ships many agents (`harbor run --help` lists them: `opencode`, `codex`,
`claude-code`, `aider`, `mini-swe-agent`, …). Use `-a <agent> -m <provider>/<model>`
with the matching provider key in your environment. The agent-phase allowlist
permits `openrouter.ai`, `api.openai.com`, and `api.anthropic.com`; to reach a
different endpoint add `--allow-agent-host <host>`. See
[REPRODUCE.md](REPRODUCE.md#choosing-an-agent--model).

**Bring your own agent** — write a small `BaseInstalledAgent` adapter and run
`-a your.module:ClassName`, or use `-a acp:<agent>@<version>` for ACP-compatible
agents. See [CUSTOM_AGENT.md](CUSTOM_AGENT.md) and the reference adapters:
[`agents/chrys_agent.py`](agents/chrys_agent.py) (Chrys),
[`agents/dsh_agent.py`](agents/dsh_agent.py) (DeepSeek Harness), and
[`agents/icode_agent.py`](agents/icode_agent.py) (iCode).
