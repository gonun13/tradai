from __future__ import annotations

import json
import os
import pwd
import shutil
import subprocess
from pathlib import Path
from typing import Any

# 0027: the default Claude Code system prompt and tool definitions cost ~27k input tokens a
# call — for a researcher that never touches a tool. This replaces them.
DEFAULT_SYSTEM_PROMPT = (
    "You are a research analyst inside Tradai, a personal advisory tool. You have no tools. "
    "Answer only from the data in the message, and return only what is asked."
)


class ClaudeCliError(RuntimeError):
    pass


class ClaudeCliAdapter:
    """Claude Agent CLI via subscription OAuth (`CLAUDE_CODE_OAUTH_TOKEN`). Never uses ANTHROPIC_API_KEY."""

    def __init__(self) -> None:
        self.token = (os.environ.get("CLAUDE_CODE_OAUTH_TOKEN") or "").strip()
        self.binary = os.environ.get("CLAUDE_CLI_PATH", "claude")
        self.timeout = int(os.environ.get("CLAUDE_CLI_TIMEOUT_SECONDS", "300"))
        self.run_as = (os.environ.get("CLAUDE_RUN_AS") or "tradai").strip() or "tradai"
        self.model = (os.environ.get("CLAUDE_MODEL") or "").strip() or None
        self.effort = (os.environ.get("CLAUDE_EFFORT") or "").strip() or None
        # Usage from the last call (CLI `--output-format json`), recorded on the run.
        self.last_usage: dict[str, Any] | None = None

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
            "model": self.model,
            "effort": self.effort,
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

    def analyze(
        self,
        prompt: str,
        *,
        system_prompt: str | None = None,
        json_schema: dict[str, Any] | None = None,
    ) -> str:
        """
        One bare, tool-less turn. Returns the answer text — with `json_schema`, the validated
        structured output serialized as JSON, so callers parse one shape either way.
        """
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
            "--tools", "",
            "--strict-mcp-config",
            "--no-session-persistence",
            "--system-prompt", system_prompt or DEFAULT_SYSTEM_PROMPT,
            "--output-format", "json",
        ]
        if json_schema is not None:
            cmd += ["--json-schema", json.dumps(json_schema, separators=(",", ":"))]
        if self.model:
            cmd += ["--model", self.model]
        if self.effort:
            cmd += ["--effort", self.effort]
        cmd.append(prompt)
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

        out = (proc.stdout or "").strip()
        if not out:
            raise ClaudeCliError("Claude CLI returned empty output")
        try:
            envelope = json.loads(out)
        except json.JSONDecodeError:
            # Older CLIs, or a wrapper printing plain text: treat it as the answer itself.
            self.last_usage = None
            return out
        if not isinstance(envelope, dict):
            return out
        self.last_usage = _usage_summary(envelope)
        if envelope.get("is_error"):
            raise ClaudeCliError(f"Claude CLI error: {str(envelope.get('result') or envelope)[:800]}")
        structured = envelope.get("structured_output")
        if structured is not None:
            return json.dumps(structured, ensure_ascii=False)
        text = str(envelope.get("result") or "").strip()
        if not text:
            raise ClaudeCliError("Claude CLI returned empty output")
        return text


def _usage_summary(envelope: dict[str, Any]) -> dict[str, Any]:
    usage = envelope.get("usage") if isinstance(envelope.get("usage"), dict) else {}
    models = envelope.get("modelUsage") if isinstance(envelope.get("modelUsage"), dict) else {}
    return {
        "input_tokens": usage.get("input_tokens"),
        "cache_creation_input_tokens": usage.get("cache_creation_input_tokens"),
        "cache_read_input_tokens": usage.get("cache_read_input_tokens"),
        "output_tokens": usage.get("output_tokens"),
        "num_turns": envelope.get("num_turns"),
        "duration_ms": envelope.get("duration_ms"),
        "models": sorted(models.keys()),
    }
