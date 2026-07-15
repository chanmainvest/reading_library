"""Local Gemma 4 subprocess used by scripts/chatbot_cli.py."""
from __future__ import annotations

import json
import subprocess
import sys
import threading
from pathlib import Path
from typing import Any

GEMMA_SCRIPT = Path(__file__).resolve().parent / "gemma_chat.mjs"

GEMMA_MODELS: dict[str, dict[str, object]] = {
    "e2b": {
        "label": "Gemma 4 E2B",
        "size_gb": 3.1,
        "id": "onnx-community/gemma-4-E2B-it-ONNX",
    },
    "e4b": {
        "label": "Gemma 4 E4B",
        "size_gb": 6.0,
        "id": "onnx-community/gemma-4-E4B-it-ONNX",
    },
}


class GemmaChatError(RuntimeError):
    pass


class LocalGemma:
    """Long-lived Node worker running gemma_chat.mjs --server."""

    def __init__(self, *, device: str = "auto", model: str = "e2b") -> None:
        self.device = device
        self.model = model
        self._proc: subprocess.Popen[str] | None = None
        self._next_id = 1
        self._lock = threading.Lock()
        self._last_download: tuple[str, int] | None = None
        self.model_label = str(GEMMA_MODELS.get(model, {}).get("label", model))
        self.size_gb = GEMMA_MODELS.get(model, {}).get("size_gb", 3.1)
        self._active_device = device

    def _build_cmd(self, *, device: str) -> list[str]:
        return [
            "node",
            str(GEMMA_SCRIPT),
            "--server",
            "--device",
            device,
            "--model",
            self.model,
        ]

    def start(self) -> None:
        if self._proc is not None:
            return
        if self.model not in GEMMA_MODELS:
            raise GemmaChatError(
                f"Unknown model '{self.model}'. Choose one of: {', '.join(GEMMA_MODELS)}"
            )
        if not GEMMA_SCRIPT.is_file():
            raise GemmaChatError(f"Missing {GEMMA_SCRIPT}")

        self._start_worker(self.device)

    def _start_worker(self, device: str) -> None:
        if self._proc is not None:
            self.close()
        cmd = self._build_cmd(device=device)
        try:
            self._proc = subprocess.Popen(
                cmd,
                cwd=GEMMA_SCRIPT.parent,
                stdin=subprocess.PIPE,
                stdout=subprocess.PIPE,
                stderr=subprocess.PIPE,
                text=True,
                encoding="utf-8",
                bufsize=1,
            )
        except FileNotFoundError as exc:
            raise GemmaChatError(
                "Node.js is required. Install Node and run: cd scripts && npm install"
            ) from exc

        assert self._proc.stderr is not None
        ready = False
        for _ in range(5000):
            line = self._proc.stderr.readline()
            if not line:
                break
            line = line.strip()
            if not line:
                continue
            try:
                event = json.loads(line)
            except json.JSONDecodeError:
                print(line, file=sys.stderr)
                continue
            etype = event.get("type")
            if etype == "ready":
                ready = True
                self._active_device = str(event.get("device", device))
                print(
                    f"{event.get('model_label', self.model_label)} ready "
                    f"({self._active_device})",
                    file=sys.stderr,
                )
                break
            if etype == "loading":
                size = event.get("size_gb", self.size_gb)
                print(
                    f"Loading {event.get('model_label', self.model_label)} "
                    f"(first run downloads ~{size} GB)…",
                    file=sys.stderr,
                )
            elif etype == "download":
                pct = event.get("pct")
                file_name = event.get("file", "")
                if pct is not None:
                    key = (file_name, int(pct))
                    if key != self._last_download:
                        self._last_download = key
                        print(f"  download {file_name}: {pct}%", file=sys.stderr)
            elif etype == "error":
                raise GemmaChatError(event.get("message", "model load failed"))

        if not ready:
            err = ""
            if self._proc.stderr:
                err = self._proc.stderr.read()
            raise GemmaChatError(f"Gemma worker failed to start. {err}".strip())

    def close(self) -> None:
        if self._proc is None:
            return
        try:
            if self._proc.stdin:
                self._proc.stdin.close()
        except OSError:
            pass
        self._proc.terminate()
        try:
            self._proc.wait(timeout=5)
        except subprocess.TimeoutExpired:
            self._proc.kill()
        self._proc = None

    def _generate_once(
        self,
        messages: list[dict[str, str]],
        *,
        stream: bool,
        max_new_tokens: int,
    ) -> str:
        assert self._proc is not None and self._proc.stdin and self._proc.stdout

        req_id = self._next_id
        self._next_id += 1
        payload = {
            "id": req_id,
            "messages": messages,
            "max_new_tokens": max_new_tokens,
        }
        self._proc.stdin.write(json.dumps(payload) + "\n")
        self._proc.stdin.flush()

        answer = ""
        while True:
            line = self._proc.stdout.readline()
            if not line:
                raise GemmaChatError("Gemma worker exited unexpectedly")
            line = line.strip()
            if not line:
                continue
            event: dict[str, Any] = json.loads(line)
            if event.get("id") not in (None, req_id):
                continue
            etype = event.get("type")
            if etype == "token":
                chunk = event.get("text", "")
                if chunk and stream:
                    print(chunk, end="", flush=True)
                continue
            if etype == "done":
                answer = str(event.get("text", ""))
                break
            if etype == "error":
                raise GemmaChatError(event.get("message", "generation failed"))

        if stream:
            print()
        return answer

    @staticmethod
    def _looks_bad(answer: str) -> bool:
        text = answer.strip()
        return len(text) < 20

    def generate(
        self,
        messages: list[dict[str, str]],
        *,
        stream: bool = True,
        max_new_tokens: int = 512,
    ) -> str:
        self.start()
        assert self._proc is not None

        with self._lock:
            answer = self._generate_once(
                messages,
                stream=stream,
                max_new_tokens=max_new_tokens,
            )
            if (
                self._looks_bad(answer)
                and self._active_device not in ("cpu", "wasm")
                and self.device != "cpu"
            ):
                print(
                    f"GPU ({self._active_device}) returned a short/empty answer; "
                    "retrying on CPU…",
                    file=sys.stderr,
                )
                self._start_worker("cpu")
                if stream:
                    print("Assistant: ", end="", flush=True)
                answer = self._generate_once(
                    messages,
                    stream=stream,
                    max_new_tokens=max_new_tokens,
                )
            return answer
