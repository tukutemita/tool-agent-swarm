"""PlaywrightでGradio UIの操作性を検証するE2Eテスト。"""
from __future__ import annotations

import subprocess
from typing import Any, Dict, List

import pytest

pytest.importorskip("gradio")
playwright_sync_api = pytest.importorskip("playwright.sync_api")
sync_playwright = playwright_sync_api.sync_playwright

from ui.app import create_interface


@pytest.fixture(scope="session", autouse=True)
def ensure_chromium() -> None:
    """Chromiumブラウザが未インストールの場合は事前に取得する。"""

    result = subprocess.run(
        ["playwright", "install", "chromium"],
        capture_output=True,
        text=True,
        check=False,
    )
    if result.returncode != 0 and "already installed" not in result.stdout:
        pytest.skip(
            "Chromiumのインストールに失敗したためGUIテストをスキップします。"
            f" stdout={result.stdout} stderr={result.stderr}"
        )


@pytest.mark.timeout(120)
def test_gradio_gui_flow(monkeypatch: pytest.MonkeyPatch) -> None:
    """GUI上でPMと社員宛てにメッセージを送信できることを確認する。"""

    captured_payloads: List[Dict[str, Any]] = []

    class _DummyResponse:
        def __init__(self, payload: Dict[str, Any]) -> None:
            self._payload = payload

        def json(self) -> Dict[str, Any]:
            return self._payload

        def raise_for_status(self) -> None:
            return None

    class _DummyAsyncClient:
        """UIからのHTTP呼び出しを横取りして応答をスタブする。"""

        def __init__(self, *args: Any, **kwargs: Any) -> None:
            return

        async def __aenter__(self) -> "_DummyAsyncClient":
            return self

        async def __aexit__(self, exc_type, exc, tb) -> None:  # type: ignore[override]
            return None

        async def post(self, url: str, json: Dict[str, Any], **kwargs: Any) -> _DummyResponse:
            captured_payloads.append(json)
            reply_text = f"受信:{json['target']}:{json['message']}"
            return _DummyResponse({"reply": reply_text})

    monkeypatch.setattr("ui.app.httpx.AsyncClient", _DummyAsyncClient)

    demo = create_interface()
    demo.queue(api_open=False)

    try:
        _, local_url, _ = demo.launch(
            server_name="127.0.0.1",
            server_port=None,
            prevent_thread_lock=True,
            show_api=False,
            share=False,
            inline=False,
        )

        with sync_playwright() as playwright:
            browser = playwright.chromium.launch(headless=True)
            page = browser.new_page()
            page.goto(local_url, wait_until="networkidle")

            page.wait_for_selector("text=Tool Agent Swarm Console")

            page.get_by_label("セッションID").fill("session-gui")
            page.get_by_label("メッセージ").fill("PMへの依頼")
            page.get_by_role("button", name="送信").click()
            page.wait_for_selector("text=受信:pm:PMへの依頼")

            page.get_by_text("社員を直接指名").click()
            page.select_option("select[aria-label=\"社員を選択\"]", "B")
            page.get_by_label("メッセージ").fill("ワーカー依頼")
            page.get_by_role("button", name="送信").click()
            page.wait_for_selector("text=受信:B:ワーカー依頼")

            browser.close()
    finally:
        demo.close()

    targets = [payload["target"] for payload in captured_payloads]
    assert targets == ["pm", "B"], f"想定外の送信先が記録されました: {targets}"
    assert all(payload["session_id"] == "session-gui" for payload in captured_payloads), "セッションIDが維持されていません"
