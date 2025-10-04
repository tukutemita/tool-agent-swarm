"""PMと社員エージェント間の対話を調整するためのルーター機能群。"""
from __future__ import annotations

import asyncio
import json
import logging
from dataclasses import dataclass
from pathlib import Path
from typing import Any, Dict, List, MutableMapping, Optional, Tuple

import httpx
import yaml
from tenacity import AsyncRetrying, retry_if_exception_type, stop_after_attempt, wait_exponential

LOGGER = logging.getLogger(__name__)


@dataclass
class AgentConfig:
    """単一エージェントの設定パラメーターを保持する。"""

    endpoint: str
    system_prompt_path: Path
    model: str = "local-model"

    @classmethod
    def from_mapping(cls, data: MutableMapping[str, Any], base_dir: Path) -> "AgentConfig":
        endpoint = data["endpoint"]
        prompt_path = Path(data["system_prompt"])
        if not prompt_path.is_absolute():
            prompt_path = (base_dir / prompt_path).resolve()
        model = data.get("model", "local-model")
        return cls(endpoint=endpoint, system_prompt_path=prompt_path, model=model)


@dataclass
class TimeoutConfig:
    """LM Studioへのリクエストで利用するタイムアウト設定。"""

    request_sec: float = 120.0
    connect_sec: float = 10.0


@dataclass
class RetryConfig:
    """LM Studioリクエスト時のリトライポリシー。"""

    max_attempts: int = 2
    base_backoff_sec: float = 2.0


@dataclass
class SecurityConfig:
    """トークン検証の有効化を制御するセキュリティ設定。"""

    token_env: Optional[str] = None
    enabled: bool = False


class Router:
    """エージェント解決・セッション維持・LM Studioへの中継を担う。"""

    def __init__(self, config_path: Path) -> None:
        self._config_path = config_path
        self._config_mtime: Optional[float] = None
        self._agent_configs: Dict[str, AgentConfig] = {}
        self._system_prompts: Dict[str, str] = {}
        self._timeout_config = TimeoutConfig()
        self._retry_config = RetryConfig()
        self._security_config = SecurityConfig()
        self._sessions: Dict[Tuple[str, str], List[Dict[str, str]]] = {}
        self._config_lock = asyncio.Lock()
        self._client = httpx.AsyncClient()

    @property
    def security_config(self) -> SecurityConfig:
        """現在のセキュリティ設定を返す。"""

        return self._security_config

    async def close(self) -> None:
        """内部で利用するHTTPリソースをクローズする。"""

        await self._client.aclose()

    async def ensure_latest_config(self) -> None:
        """YAML設定ファイルの変更を検知して再読み込みする。"""

        # 設定ファイル読み込みは競合を避けるため排他制御する
        async with self._config_lock:
            try:
                mtime = self._config_path.stat().st_mtime
            except FileNotFoundError as exc:
                LOGGER.error("models.yaml missing at %s", self._config_path)
                raise RuntimeError("Configuration file missing") from exc
            if self._config_mtime is not None and mtime <= self._config_mtime:
                return
            LOGGER.info("Reloading configuration from %s", self._config_path)
            config_data = yaml.safe_load(self._config_path.read_text()) or {}
            agents = config_data.get("agents", {})
            base_dir = self._config_path.parent
            new_agent_configs: Dict[str, AgentConfig] = {}
            new_prompts: Dict[str, str] = {}
            # 各エージェントの設定を解析し、プロンプトも同時に読込む
            for name, mapping in agents.items():
                agent_cfg = AgentConfig.from_mapping(mapping, base_dir)
                if not agent_cfg.system_prompt_path.exists():
                    raise RuntimeError(f"Prompt not found for agent {name}: {agent_cfg.system_prompt_path}")
                new_agent_configs[name] = agent_cfg
                new_prompts[name] = agent_cfg.system_prompt_path.read_text(encoding="utf-8")
            timeouts = config_data.get("timeouts", {})
            retries = config_data.get("retries", {})
            security = config_data.get("security", {})
            self._timeout_config = TimeoutConfig(
                request_sec=float(timeouts.get("request_sec", 120.0)),
                connect_sec=float(timeouts.get("connect_sec", 10.0)),
            )
            self._retry_config = RetryConfig(
                max_attempts=int(retries.get("max_attempts", 2)),
                base_backoff_sec=float(retries.get("base_backoff_sec", 2.0)),
            )
            self._security_config = SecurityConfig(
                token_env=security.get("token_env"),
                enabled=bool(security.get("enabled", False)),
            )
            self._agent_configs = new_agent_configs
            self._system_prompts = new_prompts
            self._config_mtime = mtime

    def _get_session_history(self, target: str, session_id: str) -> List[Dict[str, str]]:
        # (ターゲット, セッション)単位で履歴を分離し、system promptを初回に注入する
        key = (target, session_id)
        if key not in self._sessions:
            prompt = self._system_prompts[target]
            self._sessions[key] = [
                {"role": "system", "content": prompt},
            ]
        return self._sessions[key]

    async def send_message(self, target: str, session_id: str, message: str) -> str:
        """指定エージェントへメッセージを送信し応答を返す。"""

        await self.ensure_latest_config()
        if target not in self._agent_configs:
            raise ValueError(f"Unknown agent target: {target}")
        history = self._get_session_history(target, session_id)
        # ユーザー発言を履歴に積み、モデルへ問い合わせる
        history.append({"role": "user", "content": message})
        response = await self._invoke_model(target, history)
        if not response.strip():
            LOGGER.warning("Empty response from %s. Requesting self-summary.", target)
            history.append(
                {
                    "role": "system",
                    "content": "The previous reply was empty or off-topic. Provide a concise self-summary of the intended answer.",
                }
            )
            response = await self._invoke_model(target, history)
        history.append({"role": "assistant", "content": response})
        return response

    async def _invoke_model(self, target: str, messages: List[Dict[str, str]]) -> str:
        agent_config = self._agent_configs[target]
        # LM Studio互換APIへ渡すリクエストボディを作成
        payload = {
            "model": agent_config.model,
            "messages": messages,
        }
        timeout = httpx.Timeout(
            timeout=self._timeout_config.request_sec,
            connect=self._timeout_config.connect_sec,
        )

        async def _send_request() -> str:
            LOGGER.debug("Dispatching request to %s", agent_config.endpoint)
            # 逐次処理のためPOST送信し、HTTPエラーは例外として扱う
            response = await self._client.post(agent_config.endpoint, json=payload, timeout=timeout)
            response.raise_for_status()
            data = response.json()
            if isinstance(data, dict):
                if "choices" in data:
                    # OpenAI互換レスポンス
                    content = data["choices"][0]["message"]["content"]
                    return content
                if "message" in data:
                    content = data["message"]["content"]
                    return content
                if "content" in data:
                    return data["content"]
            # ここまでで形式を特定できない場合は上位へ例外を伝播させる
            raise RuntimeError(f"Unexpected response format from {agent_config.endpoint}: {json.dumps(data)[:200]}")

        # tenacityでHTTPエラーやフォーマット異常を指数バックオフで再試行
        retrying = AsyncRetrying(
            stop=stop_after_attempt(self._retry_config.max_attempts),
            wait=wait_exponential(multiplier=self._retry_config.base_backoff_sec),
            retry=retry_if_exception_type((httpx.HTTPError, RuntimeError)),
            reraise=True,
        )
        async for attempt in retrying:
            with attempt:
                return await _send_request()
        return ""


__all__ = ["Router"]
