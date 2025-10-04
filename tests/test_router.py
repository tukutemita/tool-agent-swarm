"""Routerの設定読み込みを検証するユニットテスト。"""
from __future__ import annotations

import asyncio
from pathlib import Path
from typing import List

import pytest

from orchestrator.router import Router


def test_router_configuration_reload(tmp_path: Path, monkeypatch: pytest.MonkeyPatch) -> None:
    prompts_dir = tmp_path / "prompts"
    prompts_dir.mkdir()
    pm_prompt = prompts_dir / "pm.system.md"
    worker_prompt = prompts_dir / "worker.system.md"
    pm_prompt.write_text("PM prompt", encoding="utf-8")
    worker_prompt.write_text("Worker prompt", encoding="utf-8")
    config_path = tmp_path / "models.yaml"
    config_path.write_text(
        """
agents:
  pm:
    endpoint: http://localhost:1234
    system_prompt: prompts/pm.system.md
  A:
    endpoint: http://localhost:1234
    system_prompt: prompts/worker.system.md
timeouts:
  request_sec: 5
retries:
  max_attempts: 1
security:
  enabled: true
  token_env: TEST_TOKEN
""",
        encoding="utf-8",
    )

    async def scenario() -> None:
        router = Router(config_path)
        await router.ensure_latest_config()
        assert router.security_config.enabled is True
        assert router.security_config.token_env == "TEST_TOKEN"

        captured_messages: List[str] = []

        async def fake_invoke(target: str, messages: List[dict[str, str]]) -> str:
            captured_messages.append(messages[-1]["content"])
            return "ok"

        monkeypatch.setattr(router, "_invoke_model", fake_invoke)
        reply = await router.send_message("pm", "session", "hello")
        assert reply == "ok"
        assert any("hello" in message for message in captured_messages)

    asyncio.run(scenario())
