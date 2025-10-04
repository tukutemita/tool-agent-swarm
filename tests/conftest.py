"""プロジェクトルートをインポート可能にするPytest設定。"""
from __future__ import annotations

import sys
import types
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
if str(ROOT) not in sys.path:
    sys.path.insert(0, str(ROOT))

try:
    import yaml  # type: ignore  # noqa: F401
except ModuleNotFoundError:  # pragma: no cover
    def _parse_value(value: str):
        lowered = value.lower()
        if lowered in {"true", "false"}:
            return lowered == "true"
        try:
            if "." in value:
                return float(value)
            return int(value)
        except ValueError:
            return value

    def safe_load(text: str):
        result: dict[str, object] = {}
        stack = [result]
        indents = [-1]
        for raw_line in text.splitlines():
            line = raw_line.rstrip()
            if not line or line.lstrip().startswith("#"):
                continue
            indent = len(line) - len(line.lstrip())
            while len(indents) > 1 and indent <= indents[-1]:
                stack.pop()
                indents.pop()
            if ":" not in line:
                continue
            key, value = line.split(":", 1)
            key = key.strip()
            value = value.strip()
            if not value:
                new_dict: dict[str, object] = {}
                stack[-1][key] = new_dict
                stack.append(new_dict)
                indents.append(indent)
            else:
                stack[-1][key] = _parse_value(value)
        return result

    stub_yaml = types.ModuleType("yaml")
    stub_yaml.safe_load = safe_load  # type: ignore[attr-defined]
    sys.modules["yaml"] = stub_yaml

try:
    import httpx  # type: ignore  # noqa: F401
except ModuleNotFoundError:  # pragma: no cover
    class _FakeTimeout:
        def __init__(self, *args, **kwargs) -> None:
            pass

    class _FakeHTTPError(Exception):
        """スタブ用のフォールバックHTTPエラー。"""

    class _FakeAsyncClient:
        def __init__(self, *args, **kwargs) -> None:
            pass

        async def post(self, *args, **kwargs):  # noqa: D401
            raise RuntimeError("HTTP client not available in test stub")

        async def aclose(self) -> None:
            return None

    stub_httpx = types.ModuleType("httpx")
    stub_httpx.AsyncClient = _FakeAsyncClient  # type: ignore[attr-defined]
    stub_httpx.Timeout = _FakeTimeout  # type: ignore[attr-defined]
    stub_httpx.HTTPError = _FakeHTTPError  # type: ignore[attr-defined]
    sys.modules["httpx"] = stub_httpx

try:
    from tenacity import AsyncRetrying  # type: ignore  # noqa: F401
except ModuleNotFoundError:  # pragma: no cover
    class _Attempt:
        def __enter__(self):
            return self

        def __exit__(self, exc_type, exc, tb):
            return False

    class AsyncRetrying:  # type: ignore[override]
        def __init__(self, *args, **kwargs) -> None:
            pass

        def __aiter__(self):
            async def _iterator():
                yield _Attempt()
            return _iterator()

    def stop_after_attempt(*args, **kwargs):  # type: ignore[override]
        return None

    def wait_exponential(*args, **kwargs):  # type: ignore[override]
        return None

    def retry_if_exception_type(*args, **kwargs):  # type: ignore[override]
        return None

    stub_tenacity = types.ModuleType("tenacity")
    stub_tenacity.AsyncRetrying = AsyncRetrying  # type: ignore[attr-defined]
    stub_tenacity.stop_after_attempt = stop_after_attempt  # type: ignore[attr-defined]
    stub_tenacity.wait_exponential = wait_exponential  # type: ignore[attr-defined]
    stub_tenacity.retry_if_exception_type = retry_if_exception_type  # type: ignore[attr-defined]
    sys.modules["tenacity"] = stub_tenacity
