"""Reference Harbor agent adapter for iCode
(https://gitcode.com/michaelling/iCode).

Subclasses Harbor's ``BaseInstalledAgent`` and drives iCode's **headless CLI**
(``icode run -t … -C … --json``), which mirrors Chrys's non-TUI surface. iCode
installs from gitcode; its ``openjiuwen`` SDK dep comes from the
``michaelling/agent-core`` ``icode`` branch (SSH in ``pyproject.toml`` — rewritten
to HTTPS in-tree before ``uv sync``, because Harbor sandboxes often block
SSH :22 and uv's git+ssh path can bypass git ``insteadOf``).

Talks to DeepSeek's official API (``https://api.deepseek.com``) and reads
``DEEPSEEK_API_KEY`` (iCode also accepts ``ICODE_API_KEY`` / ``OPENJIUWEN_API_KEY``):

    export DEEPSEEK_API_KEY=sk-...
    export GITCODE_TOKEN=...   # private fetch / agent-core; gitcode personal access token
    PYTHONPATH=. harbor run -p harbor_tasks/ruff_1 \
      -a agents.icode_agent:ICodeAgent \
      --ae DEEPSEEK_API_KEY=$DEEPSEEK_API_KEY \
      --ae GITCODE_TOKEN=$GITCODE_TOKEN \
      --allow-agent-host api.deepseek.com \
      --agent-setup-timeout-multiplier 10 \
      --job-name ruff_1_icode --jobs-dir harbor_runs/ruff_1 --no-delete -n 1 -y \
      --ve LOLBENCH_SUITE=union

NOTE: template — validate on ``ruff_1`` before a full sweep.
"""
from __future__ import annotations

from typing import override

from harbor.agents.installed.base import BaseInstalledAgent
from harbor.environments.base import BaseEnvironment
from harbor.models.agent.context import AgentContext

# Pin a commit for reproducibility (iCode 0.1.35).
ICODE_PIN = "bb3681a67196cba232b6193bf7e6406f610a0db0"
ICODE_HTTPS = "https://gitcode.com/michaelling/iCode.git"
# Install under the agent user's HOME — the agent phase runs as a non-root user
# ("agent"), which cannot write to /opt. $HOME is expanded by the shell at run time.
ICODE_DIR = "$HOME/icode"
DEEPSEEK_BASE_URL = "https://api.deepseek.com"
DEFAULT_MODEL = "deepseek-chat"


class ICodeAgent(BaseInstalledAgent):
    """Run the iCode headless CLI inside a LoLBench Harbor task."""

    @staticmethod
    @override
    def name() -> str:
        return "icode"

    @override
    def get_version_command(self) -> str | None:
        return (
            f"{ICODE_DIR}/.venv/bin/icode --help >/dev/null 2>&1 "
            "&& echo icode-installed"
        )

    @override
    async def install(self, environment: BaseEnvironment) -> None:
        # Setup/install has the network needed to fetch tooling. The task-solving
        # run() that follows is restricted to the model allowlist.
        await self.ensure_system_dependencies(environment, ("git", "curl"))

        # Fetch pinned iCode over HTTPS (curl/git), then uv sync. openjiuwen is
        # declared as ssh://git@gitcode.com/... in [tool.uv.sources]. Harbor
        # sandboxes often block SSH :22, and uv's git+ssh fetch can bypass
        # insteadOf — so rewrite pyproject.toml to HTTPS before `uv sync`, and
        # still set insteadOf for any remaining git SSH URLs.
        # GIT_TERMINAL_PROMPT=0 stops any git-sourced dep hanging on a prompt.
        # Private repos / agent-core need --ae GITCODE_TOKEN=... (gitcode PAT).
        await self.exec_as_agent(environment, command=(
            "set -euo pipefail; export GIT_TERMINAL_PROMPT=0; "
            "command -v uv >/dev/null || curl -LsSf https://astral.sh/uv/install.sh | sh; "
            'export PATH="$HOME/.local/bin:$PATH"; '
            'if [ -n "${GITCODE_TOKEN:-}" ]; then '
            '  _auth="https://oauth2:${GITCODE_TOKEN}@gitcode.com/"; '
            "else "
            '  _auth="https://gitcode.com/"; '
            "fi; "
            # Belt-and-suspenders for any SSH git URLs uv/git still emit.
            'git config --global url."${_auth}".insteadOf "ssh://git@gitcode.com/"; '
            'git config --global url."${_auth}".insteadOf "git@gitcode.com:"; '
            f"rm -rf {ICODE_DIR}; "
            'if [ -n "${GITCODE_TOKEN:-}" ]; then '
            f'  git clone --filter=blob:none --no-checkout '
            f'"https://oauth2:${{GITCODE_TOKEN}}@gitcode.com/michaelling/iCode.git" {ICODE_DIR}; '
            "else "
            f'  git clone --filter=blob:none --no-checkout "{ICODE_HTTPS}" {ICODE_DIR}; '
            "fi; "
            f"cd {ICODE_DIR}; "
            f"git fetch --depth 1 origin {ICODE_PIN}; "
            f"git checkout {ICODE_PIN}; "
            "uv python install 3.12; "
            # Force HTTPS for openjiuwen so uv never invokes SSH (port 22).
            # Use uv's managed Python (sandbox image may lack system python3).
            "UV_PY=$(uv python find 3.12); "
            "cat > /tmp/rewrite_openjiuwen.py <<'EOF'\n"
            "import os, pathlib\n"
            "p = pathlib.Path('pyproject.toml')\n"
            "text = p.read_text()\n"
            "token = os.environ.get('GITCODE_TOKEN', '').strip()\n"
            "core = (\n"
            "    f'https://oauth2:{token}@gitcode.com/michaelling/agent-core.git'\n"
            "    if token else\n"
            "    'https://gitcode.com/michaelling/agent-core.git'\n"
            ")\n"
            "old = 'ssh://git@gitcode.com/michaelling/agent-core.git'\n"
            "if old not in text:\n"
            "    raise SystemExit('expected SSH openjiuwen source missing')\n"
            "p.write_text(text.replace(old, core))\n"
            "print('rewrote openjiuwen source to HTTPS')\n"
            "EOF\n"
            '"$UV_PY" /tmp/rewrite_openjiuwen.py; '
            # Skip default dev group (pytest, etc.). --frozen omitted for now:
            # pinned iCode commit may lack uv.lock (NonZeroAgentExitCodeError).
            "uv sync --no-default-groups; "
            ".venv/bin/icode --help >/dev/null"
        ), env=self.extra_env)

        # Anti-cheat: iCode runs on its OWN uv Python, which the eval image's
        # stdlib strip does not reach. Remove CPython's bundled ``test`` package
        # so it cannot be copied into the workspace.
        #
        # Do NOT strip ``tomllib``: iCode/openjiuwen (alembic) needs it to start.
        # Consequence: do not run this agent on ``cpython_5`` (PEP 680) — the
        # model could copy tomllib from the agent interpreter into the task tree.
        await self.exec_as_agent(environment, command=(
            'find "$HOME/.local/share/uv/python" -type d '
            r'-name test -path "*python3.12*" '
            "-prune -exec rm -rf {} + 2>/dev/null || true"
        ))

    @override
    async def run(
        self,
        instruction: str,
        environment: BaseEnvironment,
        context: AgentContext,
    ) -> None:
        # iCode reads DEEPSEEK_API_KEY / ICODE_* from env. We do not use Harbor's
        # -m / model_connection routing (that path is OpenRouter-oriented).
        env = dict(self.extra_env)
        env.setdefault("ICODE_API_BASE", DEEPSEEK_BASE_URL)
        env.setdefault("ICODE_PROVIDER", "DeepSeek")
        env.setdefault("ICODE_MODEL", DEFAULT_MODEL)

        await self.exec_as_agent(environment, command=(
            "set -eu; mkdir -p /logs/agent/icode /logs/agent/icode-project; "
            f"cat > /tmp/lolbench_task.md <<'LOLBENCH_TASK_EOF'\n{instruction}\nLOLBENCH_TASK_EOF"
        ))

        # Solve, then submit. Repo is at /workspace/<project>; -C sets the tool cwd.
        # -p isolates iCode's own project/session tree away from the eval repo.
        # lolbench-submit captures the working-tree diff for the verifier.
        await self.exec_as_agent(environment, env=env, command=(
            'export PATH="$HOME/.local/bin:$PATH"; '
            'repo=$(dirname "$(find /workspace -maxdepth 2 -type d -name .git 2>/dev/null | head -1)"); '
            '[ -n "$repo" ] && [ "$repo" != "." ] || repo=/workspace; cd "$repo"; '
            f'{ICODE_DIR}/.venv/bin/icode -p /logs/agent/icode-project '
            "run -t /tmp/lolbench_task.md "
            f'-C "$repo" -a code --json 2>&1 | tee /logs/agent/icode.txt; '
            'lolbench-submit "$repo" || true'
        ))
