from __future__ import annotations

import json
import os
import pwd
import shutil
import subprocess
from pathlib import Path


class ClaudeCliError(RuntimeError):
    pass


class ClaudeCliAdapter:
    """Claude Agent CLI via subscription OAuth (`CLAUDE_CODE_OAUTH_TOKEN`). Never uses ANTHROPIC_API_KEY."""

    def __init__(self) -> None:
        self.token = (os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") or "").strip()
        self.binary = os.environ.get("CLAUDE_CLI_PATH", "claude")
        self.timeout = int(os.environ.get("CLAUDE_CLI_TIMEOUT_SECONDS", "300"))
        self.run_as = (os.environ.get("CLAUDE_RUN_AS") or "tradai").strip() or "tradai"

    def enabled(self) -> bool:
        return bool(self.token) and shutil.which(self.binary) is not None

    def status(self) -> dict:
        return {
            "enabled": self.enabled(),
            "token_set": bool(self.token),
            "binary": self.binary,
            "binary_found": shutil.which(self.binary) is not None,
            "anthropic_api_key_set": bool(os.environ.get("ANTHROPIC_API_KEY")),
            "run_as": self.run_as if os.geteuid() == 0 else None,
        }

    def _claude_pw(self) -> pwd.struct_passwd | None:
        """When root, demote Claude CLI to CLAUDE_RUN_AS (skip-permissions forbids root)."""
        if os.geteuid() != 0:
            return None
        try:
            return pwd.getpwnam(self.run_as)
        except KeyError as exc:
            raise ClaudeCliError(
                f"CLAUDE_RUN_AS user '{self.run_as}' not found — rebuild the worker image."
            ) from exc

    def _home(self) -> Path:
        pw = self._claude_pw()
        if pw is not None:
            return Path(pw.pw_dir)
        return Path.home()

    def ensure_onboarding_seed(self) -> None:
        home = self._home()
        cfg = home / ".claude.json"
        home.mkdir(parents=True, exist_ok=True)
        data: dict = {}
        if cfg.exists():
            try:
                data = json.loads(cfg.read_text(encoding="utf-8"))
                if not isinstance(data, dict):
                    data = {}
            except json.JSONDecodeError:
                data = {}
        data["hasCompletedOnboarding"] = True
        data["hasTrustDialogAccepted"] = True
        cfg.write_text(json.dumps(data), encoding="utf-8")
        pw = self._claude_pw()
        if pw is not None:
            os.chown(cfg, pw.pw_uid, pw.pw_gid)

    def analyze(self, prompt: str) -> str:
        if os.environ.get("ANTHROPIC_API_KEY"):
            raise ClaudeCliError(
                "ANTHROPIC_API_KEY is set — unset it so Claude uses CLAUDE_CODE_OAUTH_TOKEN (subscription)."
            )
        if not self.token:
            raise ClaudeCliError("CLAUDE_CODE_OAUTH_TOKEN is not set (run `claude setup-token` on the host).")
        if shutil.which(self.binary) is None:
            raise ClaudeCliError(f"Claude CLI not found ({self.binary}).")

        self.ensure_onboarding_seed()
        env = os.environ.copy()
        # Prefer subscription token; strip API key if somehow present.
        env.pop("ANTHROPIC_API_KEY", None)
        env["CLAUDE_CODE_OAUTH_TOKEN"] = self.token

        pw = self._claude_pw()
        preexec_fn = None
        if pw is not None:
            env["HOME"] = pw.pw_dir
            env["USER"] = pw.pw_name
            env["LOGNAME"] = pw.pw_name
            uid, gid = pw.pw_uid, pw.pw_gid

            def demote() -> None:
                os.setgid(gid)
                os.setuid(uid)

            preexec_fn = demote

        cmd = [
            self.binary,
            "--print",
            "--dangerously-skip-permissions",
            "--output-format",
            "text",
            prompt,
        ]
        try:
            proc = subprocess.run(
                cmd,
                capture_output=True,
                text=True,
                timeout=self.timeout,
                env=env,
                check=False,
                preexec_fn=preexec_fn,
            )
        except subprocess.TimeoutExpired as exc:
            raise ClaudeCliError(f"Claude CLI timed out after {self.timeout}s") from exc

        if proc.returncode != 0:
            err = (proc.stderr or proc.stdout or "").strip()
            raise ClaudeCliError(f"Claude CLI failed (exit {proc.returncode}): {err[:800]}")

        text = (proc.stdout or "").strip()
        if not text:
            raise ClaudeCliError("Claude CLI returned empty output")
        return text
