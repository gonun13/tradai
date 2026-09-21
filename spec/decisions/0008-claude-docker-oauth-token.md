# 0008 — Claude-in-Docker via CLAUDE_CODE_OAUTH_TOKEN

Date: 2026-09-20

## Change

Lock how Claude Agent CLI authenticates inside the Compose worker for Stage 5+.

## Decision

- Worker uses **`CLAUDE_CODE_OAUTH_TOKEN`** from Compose / `.env` (generated on the host with `claude setup-token`).
- Image installs `@anthropic-ai/claude-code` and seeds `~/.claude.json` with `hasCompletedOnboarding` + `hasTrustDialogAccepted` so headless `--print` honors the token.
- Claude CLI is invoked as non-root user `tradai` (`CLAUDE_RUN_AS`) because `--dangerously-skip-permissions` is refused under root/sudo; the worker process itself may still run as root for shared `/data` SQLite with the API.
- **Never** set `ANTHROPIC_API_KEY` in the worker env for the intended path (it wins precedence and switches to API billing).
- Host credential mount is not required for MVP; token-in-env is the supported path.
- Jev uses **`TYPESAFE_API_KEY`** against `https://api.typesafe.ai/v1/systemone` (model `jev-latest` by default).

## Why

Spec open item “Claude auth in Docker” blocked Stage 5. Subscription OAuth via setup-token matches `PROJECT.md` happy path and avoids pay-as-you-go keys.

## Spec touchpoints

- `spec/PROJECT.md`, `spec/architecture.md`, `spec/behaviour/agent-advisory.md`
- `spec/stages.md` Stage 5
