"""オーケストレーターと対話するためのGradio UI。"""
from __future__ import annotations

import logging
import os
import secrets
from typing import List, Tuple

import gradio as gr
import httpx

LOGGER = logging.getLogger(__name__)
logging.basicConfig(level=logging.INFO)

API_BASE_URL = os.getenv("ORCHESTRATOR_BASE_URL", "http://localhost:8000")
CHAT_ENDPOINT = f"{API_BASE_URL}/chat"
THEME_JS = """(theme) => { document.body.dataset.theme = theme; }"""
CSS = """
body[data-theme="dark"] {background-color:#0f172a;color:#e2e8f0;}
body[data-theme="dark"] .chatbot {background-color:#1e293b;}
body[data-theme="light"] {background-color:#f8fafc;color:#0f172a;}
body[data-theme="light"] .chatbot {background-color:#ffffff;}
.chatbot {border-radius:12px;}
"""


async def dispatch_message(
    message: str,
    history: List[Tuple[str, str]],
    target_mode: str,
    worker: str,
    session_id: str,
) -> Tuple[List[Tuple[str, str]], str, List[Tuple[str, str]]]:
    """オーケストレーターへメッセージを送り、チャット履歴を更新する。"""

    if not message.strip():
        return history, "", history
    if not session_id.strip():
        # 未入力の場合は簡易な乱数セッションIDを払い出す
        session_id = secrets.token_hex(4)
    target = "pm" if target_mode == "PMへ" else worker
    payload = {"session_id": session_id, "target": target, "message": message}
    LOGGER.info("Dispatching message to %s at %s", target, CHAT_ENDPOINT)
    try:
        async with httpx.AsyncClient(timeout=120.0) as client:
            response = await client.post(CHAT_ENDPOINT, json=payload)
            response.raise_for_status()
            data = response.json()
            reply = data.get("reply", "[空の応答]")
    except httpx.HTTPError as exc:  # noqa: BLE001
        reply = f"[Orchestrator error: {exc}]"
    user_label = f"[{session_id}] ({target.upper()}) {message}"
    bot_label = f"({target.upper()}) {reply}"
    # Gradioチャットの形式に合わせて(ユーザー発言, 応答)のタプルを蓄積
    new_history = history + [(user_label, bot_label)]
    return new_history, "", new_history


def create_interface() -> gr.Blocks:
    with gr.Blocks(css=CSS) as demo:
        gr.Markdown("# Tool Agent Swarm Console")
        gr.HTML("<script>document.body.dataset.theme='light';</script>")
        with gr.Row():
            theme_selector = gr.Radio(
                ["light", "dark"],
                value="light",
                label="テーマ",
                interactive=True,
            )
            session_box = gr.Textbox(
                label="セッションID",
                value=secrets.token_hex(3),
                placeholder="セッションIDを入力",
            )
        with gr.Row():
            target_mode = gr.Radio(
                ["PMへ", "社員を直接指名"],
                label="送信先",
                value="PMへ",
                interactive=True,
            )
            worker_dropdown = gr.Dropdown(
                ["A", "B", "C"],
                value="A",
                label="社員を選択",
                interactive=True,
                visible=False,
            )
        chatbot = gr.Chatbot(label="対話ログ", elem_classes="chatbot")
        message_box = gr.Textbox(label="メッセージ", placeholder="メッセージを入力", lines=4)
        with gr.Row():
            send_button = gr.Button("送信", variant="primary")
            clear_button = gr.Button("クリア")
        history_state = gr.State([])

        def _toggle_worker(mode: str) -> gr.Update:
            # PM以外を選んだときのみ担当者ドロップダウンを表示
            return gr.update(visible=(mode != "PMへ"))

        target_mode.change(_toggle_worker, inputs=target_mode, outputs=worker_dropdown)
        clear_button.click(lambda: ([], []), outputs=[chatbot, history_state])

        async def _submit(message: str, history: List[Tuple[str, str]], mode: str, worker: str, session: str):
            return await dispatch_message(message, history, mode, worker, session)

        send_button.click(
            _submit,
            inputs=[message_box, history_state, target_mode, worker_dropdown, session_box],
            outputs=[chatbot, message_box, history_state],
        )
        message_box.submit(
            _submit,
            inputs=[message_box, history_state, target_mode, worker_dropdown, session_box],
            outputs=[chatbot, message_box, history_state],
        )

        theme_selector.change(fn=None, inputs=theme_selector, outputs=None, js=THEME_JS)

    return demo


if __name__ == "__main__":
    demo = create_interface()
    demo.queue(api_open=False)
    demo.launch(server_name="0.0.0.0", server_port=7860, share=False)
