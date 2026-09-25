# Tradai

A local, advisory-only dashboard for a solo investor to monitor a portfolio and a tracker of EU- and US-listed stocks and ETFs. Tradai uses Claude and Jev for daily recommendations after the US close, but never places trades. Portfolio data is stored locally; configured AI providers receive the full context needed for advisory.

## Install

Requires Docker with Compose.

```bash
cp .env.example .env
bin/up -d
```

Open [http://127.0.0.1:3000](http://127.0.0.1:3000). The Portfolio page loading means the local app is working; no API keys are required for portfolio or tracker management.

To enable market data, news, and AI advisory, add the relevant optional credentials described in [`.env.example`](.env.example), then rerun `bin/up -d`. Do not set `ANTHROPIC_API_KEY`; Tradai uses Claude subscription authentication.

## Usage

Add a holding under **Portfolio** or a name under **Tracker**, then choose **Ingest now**. With Claude and Jev credentials configured, choose **Run now** for recommendations.

Product documentation and acceptance checks live in [`spec/`](spec/).
