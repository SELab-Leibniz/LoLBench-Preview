# Running LoLBench with your own agent

LoLBench tasks are plain [Harbor](https://github.com/jasonlim-cerberus/swebenchpro-harbor)
tasks, so any agent Harbor can drive works. `harbor run -a <agent>` accepts three
forms:

1. **Built-in agent** — `-a opencode | codex | claude-code | aider | mini-swe-agent | …`
   (see `harbor run --help`). This is the reference path.
2. **ACP shorthand** — `-a acp:<agent>@<version>` (e.g. `acp:opencode@1.3.9`), for
   any agent that speaks the [Agent Client Protocol](https://agentclientprotocol.com/).
   Lowest effort **if** your agent ships an ACP server.
3. **Custom adapter class** — `-a your.module:ClassName`. Write a small Python
   class that subclasses Harbor's `BaseInstalledAgent` (the base `opencode` uses),
   make it importable, and pass its import path. This is the general path for any
   CLI/library agent.

## The `BaseInstalledAgent` contract

`BaseInstalledAgent` (which extends `BaseAgent`) is the base for CLI agents that
install themselves. You override four methods (inspect your installed version with
`uv run --with harbor python -c "import inspect, harbor.agents.installed.base as b; print(inspect.getsource(b.BaseInstalledAgent))"`):

| Method | Responsibility |
|--------|----------------|
| `name()` | the agent's short name |
| `get_version_command()` | a shell command that verifies the install (or `None`) |
| `install(environment)` | install the agent + tooling in the container; write its config |
| `run(instruction, environment, context)` | solve the task, then **submit the patch** |

Key facilities the base class gives you:

- **`self.model_connection`** resolves the provider from `-m <provider>/<model>`:
  its `.env` carries the API key (e.g. `OPENROUTER_API_KEY`) and `.configured_base_url`
  the endpoint. You pass these to your agent — no hard-coded keys.
- **Network phases.** The install/setup phase runs with the network your agent
  needs to install itself. The `run()` phase is restricted to the task's **agent allowlist**
  (`openrouter.ai`, `api.openai.com`, `api.anthropic.com` — see
  [ANTI_CHEAT.md](ANTI_CHEAT.md)); your agent can reach the model but not source
  hosts.
- **Submitting.** The verifier grades `/logs/artifacts/solution.patch`, produced by
  the in-container **`lolbench-submit`** command (it captures the working-tree diff,
  excluding test paths). Every task's `instruction.md` tells the agent to run it;
  a robust adapter also calls `lolbench-submit` itself at the end of `run()`.

## Worked example: Chrys

[`agents/chrys_agent.py`](agents/chrys_agent.py) is a reference adapter for
[Chrys](https://github.com/0x7c13/chrys). It mirrors how LoLBench runs Chrys
natively:

- **`install()`** — download the pinned Chrys tarball (via `curl` — the sandbox
  reaches github over HTTPS but `git clone` trips an auth prompt through the proxy),
  `uv python install 3.14 && uv sync --extra all`, and write an OpenRouter model
  profile whose `model_id` comes from `self.model_name`.
- **`run()`** — write the instruction to a file and call `chrys run --task <file>
  --agent Code --workdir <repo> --json`, then `lolbench-submit`. The repo is
  auto-detected at `/workspace/<project>` (e.g. `/workspace/ruff` for `ruff_1`).

Three environment realities the adapter (and this command) account for:

1. **Chrys is a *private* repo** — the sandbox has no github credentials, so pass a
   token: `--ae GITHUB_TOKEN=$(gh auth token)`. A **public** agent repo needs none.
2. **Chrys reads `OPENROUTER_API_KEY` from its env** — forward it with
   `--ae OPENROUTER_API_KEY=$OPENROUTER_API_KEY` so it reliably reaches the process.
3. **Chrys builds its own Python env** — that install exceeds the default 360 s setup
   budget, so extend it: `--agent-setup-timeout-multiplier 10`.

Run it:

```bash
export OPENROUTER_API_KEY=sk-or-...
PYTHONPATH=. harbor run \
  -p harbor_tasks/ruff_1 \
  -a agents.chrys_agent:ChrysAgent \
  -m openrouter/deepseek/deepseek-v4-pro \
  --ae OPENROUTER_API_KEY=$OPENROUTER_API_KEY \
  --ae GITHUB_TOKEN=$(gh auth token) \
  --agent-setup-timeout-multiplier 10 \
  --job-name ruff_1_chrys --jobs-dir harbor_runs/ruff_1 \
  --no-delete -n 1 -y \
  --ve LOLBENCH_SUITE=union

cat harbor_runs/ruff_1/ruff_1_chrys/*/verifier/reward.json
```

Validated on `ruff_1`: Chrys applied a patch that built and passed **5/19 F2P,
51/51 P2P** (a genuine partial; `reward 0`) — confirming the custom-agent path
works end-to-end.

## Second worked example: DeepSeek Harness (dsh)

[`agents/dsh_agent.py`](agents/dsh_agent.py) adapts
[DeepSeek Harness](https://github.com/deepseek-ai/deepseek-harness) (npm
`@deepseek-ai/dsh`), driving its **headless CLI** — `dsh --profile headless "<task>"`
uses the cwd as its workspace root and edits the repo with its bash/atomic-write
tools. dsh talks to `https://api.deepseek.com` and reads `DEEPSEEK_API_KEY`.

```bash
export DEEPSEEK_HARNESS_API_KEY=sk-...
PYTHONPATH=. harbor run -p harbor_tasks/ruff_1 \
  -a agents.dsh_agent:DshAgent \
  --ae DEEPSEEK_API_KEY=$DEEPSEEK_HARNESS_API_KEY \
  --allow-agent-host api.deepseek.com \
  --agent-setup-timeout-multiplier 10 \
  --job-name ruff_1_dsh --jobs-dir harbor_runs/ruff_1 --no-delete -n 1 -y \
  --ve LOLBENCH_SUITE=union
```

Two things this example taught (both handled in the adapter):

1. **Native modules with no Linux prebuilt.** dsh needs `node-pty` (its
   `dsh-subprocess` plugin), which ships prebuilts only for macOS/Windows — on the
   Linux container it must compile. Building the whole tree at once starves it (and
   koffi's heavy build), so the adapter installs with `--ignore-scripts` then
   `npm rebuild node-pty` alone (koffi is unused by the headless plugin tree).
2. **A non-DeepSeek-default endpoint** — allowlist `api.deepseek.com` for the agent
   run, and pass the key as `DEEPSEEK_API_KEY` (via `--ae`).

Validated on `ruff_1`: applied, built, **7/19 F2P, 51/51 P2P** (genuine partial).

## Third worked example: iCode (DeepSeek official API)

[`agents/icode_agent.py`](agents/icode_agent.py) adapts
[iCode](https://gitcode.com/michaelling/iCode) (`openjiuwen-icode`), driving its
**headless CLI** — `icode run -t <task> -C <repo> --json` (Chrys-parity flags).
iCode installs from gitcode; its `openjiuwen` SDK dep is the
`michaelling/agent-core` `icode` branch. The adapter rewrites that SSH
`[tool.uv.sources]` entry in `pyproject.toml` to HTTPS before `uv sync`
(Harbor sandboxes often block SSH :22; git `insteadOf` alone is not enough
for uv's git+ssh fetch), and talks to **DeepSeek's official API**
(`https://api.deepseek.com`), reading `DEEPSEEK_API_KEY`.

```bash
export DEEPSEEK_API_KEY=sk-...
export GITCODE_TOKEN=...   # gitcode PAT for private iCode / agent-core fetch
PYTHONPATH=. harbor run -p harbor_tasks/ruff_1 \
  -a agents.icode_agent:ICodeAgent \
  --ae DEEPSEEK_API_KEY=$DEEPSEEK_API_KEY \
  --ae GITCODE_TOKEN=$GITCODE_TOKEN \
  --allow-agent-host api.deepseek.com \
  --agent-setup-timeout-multiplier 10 \
  --job-name ruff_1_icode --jobs-dir harbor_runs/ruff_1 --no-delete -n 1 -y \
  --ve LOLBENCH_SUITE=union
```

Notes specific to this adapter:

1. **gitcode credentials** — pass `--ae GITCODE_TOKEN=...` so install can clone
   iCode and resolve `openjiuwen` over HTTPS without SSH (the default
   `[tool.uv.sources]` entry is `ssh://git@gitcode.com/...`; the adapter
   rewrites it in-tree before `uv sync`).
2. **Not Harbor `-m` routing** — like dsh, this path uses the agent's native key
   and endpoint; allowlist `api.deepseek.com` for the agent run. Override model
   with `--ae ICODE_MODEL=deepseek-reasoner` (default `deepseek-chat`).
3. **Own uv Python** — the adapter strips the CPython ``test`` package from
   iCode's uv-managed 3.12 interpreter, but **keeps ``tomllib``** (required by
   alembic / openjiuwen). Do **not** run this agent on ``cpython_5`` (PEP 680):
   ``tomllib`` would be available to copy into the workspace. Same caution for
   ``cpython_2`` / ``zoneinfo`` if you later strip or leave that module.


Validate on `ruff_1` before a full sweep.

### Anti-cheat notes for custom agents

- **Reach only the model.** If your agent needs an endpoint other than the three
  allowlisted hosts, add **only** that host with `--allow-agent-host <host>` —
  never a source host (`github.com`, `pypi.org`, package mirrors, Maven Central).
- **Mind your agent's own runtime.** The eval image strips whole-module
  deliverables (`tomllib` = PEP 680, `zoneinfo` = PEP 615) from the *system*
  Python, but an agent that ships its **own** interpreter (Chrys / iCode use uv
  Python) reintroduces them. Chrys removes `tomllib` and the CPython `test`
  package in `install()` for this reason. **iCode keeps `tomllib`** (alembic
  imports it) and only strips `test` — so do not use iCode on `cpython_5`.
  Add `zoneinfo` stripping too if you run the PEP-615 task (`cpython_2`) with
  an agent that leaves that module intact.
- **Give it time.** These are long-horizon tasks; the default agent-run budget is
  6 h (`task.toml` `[agent] timeout_sec`). Keep timeout multipliers ≥ 1 (a value
  < 1 also shrinks the setup budget and causes `AgentSetupTimeoutError`).

> The Chrys adapter is a **template**: exact `BaseInstalledAgent`/`BaseEnvironment`
> method names can differ across Harbor versions, and the install/timeouts may need
> tuning for your model. Validate it on one task (`ruff_1`) before a full sweep.
