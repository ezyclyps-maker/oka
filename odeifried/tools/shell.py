"""Shell tool. Runs as the agent's (non-root) user, with a timeout. The policy has already approved it."""
from __future__ import annotations

import subprocess

from . import Tool


def run_shell(ctx, command: str) -> str:
    try:
        r = subprocess.run(["bash", "-c", command], capture_output=True, text=True, timeout=60)
    except subprocess.TimeoutExpired:
        return "ERROR: timed out after 60s"
    out = (r.stdout + ("\n[stderr]\n" + r.stderr if r.stderr.strip() else ""))[:4000]
    return f"exit code {r.returncode}\n{out}"


TOOLS = [Tool("run_shell", "Run a bash command. Prefer the file tools for moving/creating files.",
              {"command": {"type": "string"}}, ["command"], run_shell)]
