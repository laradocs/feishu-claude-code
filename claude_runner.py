"""
通过 subprocess 调用本机 claude CLI，解析 stream-json 输出。
复用 ~/.claude/ 中已有的 Max 订阅登录凭证，无需额外 API Key。
"""

import asyncio
import json
import os
from typing import Callable, Optional

from bot_config import PERMISSION_MODE, CLAUDE_CLI


async def run_claude(
    message: str,
    session_id: Optional[str] = None,
    model: Optional[str] = None,
    cwd: Optional[str] = None,
    permission_mode: Optional[str] = None,
) -> tuple[str, Optional[str]]:
    """
    调用 claude CLI 并返回完整回复（不再流式）。

    Returns:
        (full_response_text, new_session_id)
    """
    cmd = [
        CLAUDE_CLI,
        "--print",
        "--output-format", "stream-json",
        "--verbose",
        "--include-partial-messages",
        "--permission-mode", permission_mode or PERMISSION_MODE,
    ]
    if session_id:
        cmd += ["--resume", session_id]
    if model:
        cmd += ["--model", model]

    env = os.environ.copy()
    env.pop("CLAUDECODE", None)

    proc = await asyncio.create_subprocess_exec(
        *cmd,
        stdin=asyncio.subprocess.PIPE,
        stdout=asyncio.subprocess.PIPE,
        stderr=asyncio.subprocess.PIPE,
        cwd=cwd or os.path.expanduser("~"),
        env=env,
        limit=10 * 1024 * 1024,  # 10MB，防止大响应超出默认 64KB 限制
    )

    proc.stdin.write((message + "\n").encode())
    await proc.stdin.drain()
    proc.stdin.close()

    full_text = ""
    new_session_id = None

    async for raw_line in proc.stdout:
        line = raw_line.decode("utf-8", errors="replace").strip()
        if not line:
            continue

        try:
            data = json.loads(line)
        except json.JSONDecodeError:
            continue

        event_type = data.get("type")

        if event_type == "system":
            sid = data.get("session_id")
            if sid:
                new_session_id = sid

        elif event_type == "stream_event":
            evt = data.get("event", {})
            evt_type = evt.get("type")

            if evt_type == "content_block_delta":
                delta = evt.get("delta", {})
                delta_type = delta.get("type")

                if delta_type == "text_delta":
                    chunk = delta.get("text", "")
                    if chunk:
                        full_text += chunk

                elif delta_type == "input_json_delta":
                    # Skip tool input tracking since we don't need callbacks
                    pass

            elif evt_type == "content_block_start":
                # Skip tool tracking
                pass

            elif evt_type == "content_block_stop":
                # Skip tool tracking
                pass

        elif event_type == "result":
            sid = data.get("session_id")
            if sid:
                new_session_id = sid
            if not full_text:
                full_text = data.get("result", "")

    stderr_output = await proc.stderr.read()
    await proc.wait()

    if proc.returncode != 0 and not full_text:
        stderr_text = stderr_output.decode("utf-8", errors="replace").strip()
        raise RuntimeError(f"claude exited with code {proc.returncode}: {stderr_text}")

    return full_text.strip(), new_session_id
