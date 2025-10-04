# Tool Agent Swarm PoC

This proof-of-concept demonstrates a sequential multi-agent workflow where a PM orchestrator dynamically assigns roles to three interchangeable worker agents (A/B/C). Each agent is backed by the same or different LM Studio hosted local LLM instances, while their conversations remain isolated via per-agent session tracking.

## Features
- FastAPI orchestrator exposing `/chat`, `/health`, and `/assign` (placeholder) endpoints.
- Tenacity-backed retries with exponential backoff for HTTP calls to LM Studio.
- JSONL conversation logging and configurable token-based request authentication.
- Gradio chat UI with ChatGPT-like layout, agent targeting toggle, theme switching, and session selection.

## Quickstart
1. Install dependencies:
   ```bash
   pip install -r requirements.txt
   ```
2. Launch the orchestrator (ensure LM Studio endpoints from `orchestrator/models.yaml` are reachable):
   ```bash
   python orchestrator/server.py
   ```
3. In a separate terminal, start the Gradio UI:
   ```bash
   python ui/app.py
   ```
4. Open the provided URL in a browser. Send messages to the PM to have tasks decomposed and assigned sequentially to workers A/B/C. You can also directly address individual workers for debugging.

## Configuration
- Update `orchestrator/models.yaml` to point at the desired LM Studio HTTP endpoints and to toggle token authentication.
- Edit prompt templates in `orchestrator/prompts/` to fine-tune PM and worker behaviors.

## Testing
Install the Playwright browser binaries once (Chromium is required for the automated GUI test):
```bash
playwright install chromium
```

Then execute the automated test suite, which now includes a headless browser check of the Gradio UI workflow:
```bash
pytest
```

## Logging & State
- Conversation transcripts are appended to `logs/conversations.jsonl`.
- The `/state` directory contains authoritative specification documents that agents may reference when needed.
