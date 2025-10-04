"""マルチエージェントPoC向けFastAPIオーケストレーターサービス。"""
from __future__ import annotations

import asyncio
import json
import logging
import os
from datetime import datetime
from pathlib import Path
from typing import Any, Awaitable, Callable, Dict, Optional

from fastapi import Depends, FastAPI, HTTPException, Request, status
from fastapi.middleware.cors import CORSMiddleware
from pydantic import BaseModel, Field
from starlette.concurrency import run_in_threadpool

from .router import Router

BASE_DIR = Path(__file__).resolve().parent.parent
LOG_DIR = BASE_DIR / "logs"
LOG_DIR.mkdir(parents=True, exist_ok=True)
LOG_PATH = LOG_DIR / "conversations.jsonl"
MODELS_PATH = BASE_DIR / "orchestrator" / "models.yaml"

logging.basicConfig(
    level=logging.INFO,
    format="%(asctime)s [%(levelname)s] %(name)s: %(message)s",
)
LOGGER = logging.getLogger(__name__)


class ChatRequest(BaseModel):
    """``/chat``エンドポイントに送信されるリクエストペイロード。"""

    session_id: str = Field(..., description="Client provided session identifier")
    target: str = Field(..., regex="^(pm|A|B|C)$", description="Agent target identifier")
    message: str = Field(..., min_length=1, description="User message content")


class ChatResponse(BaseModel):
    """エージェントの応答を保持するレスポンススキーマ。"""

    reply: str
    target: str
    session_id: str


class AssignRequest(BaseModel):
    """``/assign``エンドポイント用のプレースホルダーリクエスト。"""

    description: str


class SequentialTaskQueue:
    """バックグラウンドワーカーでタスクを逐次処理するキュー。"""

    def __init__(self) -> None:
        self._queue: "asyncio.Queue[tuple[Callable[[], Awaitable[Any]], asyncio.Future[Any]]]" = asyncio.Queue()
        self._worker_task: Optional[asyncio.Task[None]] = None
        self._started = False

    async def start(self) -> None:
        if self._started:
            return
        self._started = True
        self._worker_task = asyncio.create_task(self._worker())

    async def stop(self) -> None:
        if self._worker_task:
            self._worker_task.cancel()
            try:
                await self._worker_task
            except asyncio.CancelledError:
                LOGGER.info("Sequential worker stopped")

    async def submit(self, coro_factory: Callable[[], Awaitable[Any]]) -> Any:
        """専用ワーカーに処理を委譲し、結果が返るまで待機する。"""
        loop = asyncio.get_running_loop()
        future: asyncio.Future[Any] = loop.create_future()
        await self._queue.put((coro_factory, future))
        return await future

    async def _worker(self) -> None:
        LOGGER.info("Sequential queue worker started")
        while True:
            coro_factory, future = await self._queue.get()
            try:
                result = await coro_factory()
            except Exception as exc:  # noqa: BLE001
                LOGGER.exception("Task execution failed: %s", exc)
                if not future.done():
                    future.set_exception(exc)
            else:
                if not future.done():
                    future.set_result(result)
            finally:
                self._queue.task_done()


class ConversationLogger:
    """対話ログをJSONLファイルに永続化するロガー。"""

    def __init__(self, file_path: Path) -> None:
        self._file_path = file_path
        self._lock = asyncio.Lock()

    async def append(self, record: Dict[str, Any]) -> None:
        async with self._lock:
            await run_in_threadpool(self._write_record, record)

    def _write_record(self, record: Dict[str, Any]) -> None:
        # ファイル生成を含め同期I/Oで処理し、スレッドプールから呼び出す
        self._file_path.parent.mkdir(parents=True, exist_ok=True)
        with self._file_path.open("a", encoding="utf-8") as handle:
            handle.write(json.dumps(record, ensure_ascii=False) + "\n")


router_manager = Router(MODELS_PATH)
conversation_logger = ConversationLogger(LOG_PATH)
queue = SequentialTaskQueue()
app = FastAPI(title="Tool Agent Swarm Orchestrator", version="0.1.0")

app.add_middleware(
    CORSMiddleware,
    allow_origins=["*"],
    allow_credentials=True,
    allow_methods=["*"],
    allow_headers=["*"],
)


async def verify_token(request: Request) -> None:
    """設定に基づいて任意のベアラートークン検証を行う。"""

    await router_manager.ensure_latest_config()
    security = router_manager.security_config
    if not security.enabled:
        # トークン検証が無効な場合はそのまま通過
        return
    expected = None
    if security.token_env:
        # 環境変数から期待値を取得し、CIなどでも統一管理できるようにする
        expected = os.getenv(security.token_env)
    if not expected:
        LOGGER.error("Security enabled but token env variable %s is undefined", security.token_env)
        raise HTTPException(status_code=status.HTTP_500_INTERNAL_SERVER_ERROR, detail="Token misconfiguration")
    header = request.headers.get("Authorization", "")
    if header != f"Bearer {expected}":
        raise HTTPException(status_code=status.HTTP_401_UNAUTHORIZED, detail="Invalid token")


@app.on_event("startup")
async def on_startup() -> None:
    await router_manager.ensure_latest_config()
    await queue.start()
    LOGGER.info("Orchestrator ready")


@app.on_event("shutdown")
async def on_shutdown() -> None:
    await queue.stop()
    await router_manager.close()


@app.get("/health")
async def health() -> Dict[str, str]:
    """ヘルスチェック用エンドポイント。"""

    return {"status": "ok"}


@app.post("/chat", response_model=ChatResponse)
async def chat(request_body: ChatRequest, _: None = Depends(verify_token)) -> ChatResponse:
    """チャットリクエストを逐次キュー経由でルーティングする。"""

    async def _execute() -> ChatResponse:
        LOGGER.info("Processing message for %s (session=%s)", request_body.target, request_body.session_id)
        # Routerを通じてPM/社員のセッション履歴を維持しつつ送信
        reply = await router_manager.send_message(request_body.target, request_body.session_id, request_body.message)
        await conversation_logger.append(
            {
                "timestamp": datetime.utcnow().isoformat(),
                "session_id": request_body.session_id,
                "target": request_body.target,
                "message": request_body.message,
                "reply": reply,
            }
        )
        # フロントエンドにはエージェント種別と返信本文を返却
        return ChatResponse(reply=reply, target=request_body.target, session_id=request_body.session_id)

    try:
        response: ChatResponse = await queue.submit(_execute)
    except ValueError as exc:
        raise HTTPException(status_code=status.HTTP_400_BAD_REQUEST, detail=str(exc)) from exc
    return response


@app.post("/assign")
async def assign(_: AssignRequest, __: None = Depends(verify_token)) -> Dict[str, str]:
    """今後の外部割当ワークフロー向けプレースホルダーエンドポイント。"""

    raise HTTPException(status_code=status.HTTP_501_NOT_IMPLEMENTED, detail="Assignment endpoint not implemented")


if __name__ == "__main__":
    import uvicorn

    uvicorn.run("orchestrator.server:app", host="0.0.0.0", port=8000, reload=False)
