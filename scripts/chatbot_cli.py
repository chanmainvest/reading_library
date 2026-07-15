#!/usr/bin/env python3
"""Terminal chatbot for the Chanma Invest reading library.

Uses the same RAG index and local Gemma 4 model as the browser assistant.
No API keys or external LLM servers — inference runs via Node +
@huggingface/transformers (onnx-community/gemma-4-E*B-it-ONNX, q4f16).

Examples:
    uv run --extra cli python scripts/chatbot_cli.py
    uv run --extra cli python scripts/chatbot_cli.py -q "What is a black swan?"
    uv run --extra cli python scripts/chatbot_cli.py --model e4b --device webgpu
    uv run --extra cli python scripts/chatbot_cli.py --book antifragile --scope book
"""
from __future__ import annotations

import argparse
import atexit
import sys
from pathlib import Path

SCRIPTS_DIR = Path(__file__).resolve().parent
if str(SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(SCRIPTS_DIR))

from chatbot_gemma import GEMMA_MODELS, GemmaChatError, LocalGemma  # noqa: E402
from chatbot_rag import RagIndex, build_system_prompt, load_section_text  # noqa: E402

_GEMMA: LocalGemma | None = None


def parse_args(argv: list[str] | None = None) -> argparse.Namespace:
    parser = argparse.ArgumentParser(
        description="Chat with the reading library using local Gemma 4 + RAG.",
    )
    parser.add_argument(
        "-q",
        "--question",
        help="Ask one question and exit (non-interactive).",
    )
    parser.add_argument(
        "--model",
        choices=tuple(GEMMA_MODELS),
        default="e2b",
        help="Gemma size: e2b (~2.3B effective, ~3.1 GB) or e4b (~4.5B, ~6 GB).",
    )
    parser.add_argument(
        "--scope",
        choices=("all", "book", "chapter"),
        default="all",
        help="Retrieval scope (default: all).",
    )
    parser.add_argument(
        "--book",
        metavar="SLUG",
        help="Book slug for --scope book or chapter (e.g. antifragile).",
    )
    parser.add_argument(
        "--section",
        metavar="ID",
        help="Section id for --scope chapter (e.g. section-10).",
    )
    parser.add_argument(
        "--device",
        default="auto",
        choices=("auto", "cpu", "dml", "webgpu", "cuda", "wasm"),
        help="Inference device (default: auto → WebGPU when available, else CPU).",
    )
    parser.add_argument(
        "--embed-device",
        default="cpu",
        choices=("auto", "cpu", "dml", "webgpu", "cuda", "wasm"),
        help="Device for query embedding (default: cpu; Gemma uses --device).",
    )
    parser.add_argument(
        "--top-k",
        type=int,
        default=5,
        help="Number of RAG excerpts to inject (default: 5).",
    )
    parser.add_argument(
        "--max-new-tokens",
        type=int,
        default=512,
        help="Gemma generation cap (default: 512, same as browser chatbot).",
    )
    parser.add_argument(
        "--no-stream",
        action="store_true",
        help="Print the full answer at once instead of streaming tokens.",
    )
    parser.add_argument(
        "--show-context",
        action="store_true",
        help="Print retrieved excerpts before the model answer.",
    )
    return parser.parse_args(argv)


def validate_args(args: argparse.Namespace) -> None:
    if args.scope in ("book", "chapter") and not args.book:
        raise SystemExit("--book SLUG is required when --scope is book or chapter")
    if args.scope == "chapter" and not args.section:
        print(
            "Note: --scope chapter without --section searches the whole book.",
            file=sys.stderr,
        )


def get_gemma(device: str, model: str) -> LocalGemma:
    global _GEMMA
    if _GEMMA is None:
        _GEMMA = LocalGemma(device=device, model=model)
        atexit.register(_GEMMA.close)
    return _GEMMA


def print_retrieved(retrieved: list) -> None:
    if not retrieved:
        print("(no excerpts retrieved)\n")
        return
    print("--- Retrieved excerpts ---")
    for item in retrieved:
        title = item.chunk.title
        preview = item.chunk.text.replace("\n", " ")[:160]
        print(f"  [{title}] {preview}…")
    print("--------------------------\n")


def answer_question(
    question: str,
    rag: RagIndex,
    args: argparse.Namespace,
    history: list[dict[str, str]],
) -> str:
    print("Retrieving excerpts…", file=sys.stderr)
    retrieved = rag.retrieve(
        question,
        scope=args.scope,
        book_slug=args.book,
        section_id=args.section,
        top_k=args.top_k,
        embed_device=args.embed_device,
    )
    if args.show_context:
        print_retrieved(retrieved)

    print("Generating answer…", file=sys.stderr)

    section_text = ""
    if args.scope == "chapter" and args.book and args.section:
        section_text = load_section_text(args.book, args.section)

    system = build_system_prompt(
        question,
        retrieved,
        scope=args.scope,
        book_slug=args.book,
        section_id=args.section,
        section_text=section_text,
    )
    messages = [
        {"role": "system", "content": system},
        *history,
        {"role": "user", "content": question},
    ]

    gemma = get_gemma(args.device, args.model)
    stream = not args.no_stream
    if stream:
        print("Assistant: ", end="", flush=True)
    answer = gemma.generate(
        messages,
        stream=stream,
        max_new_tokens=args.max_new_tokens,
    )
    if not stream:
        print(f"Assistant: {answer}")

    history.append({"role": "user", "content": question})
    history.append({"role": "assistant", "content": answer})
    return answer


def print_banner(args: argparse.Namespace) -> None:
    scope = args.scope
    if args.book:
        scope += f" / {args.book}"
        if args.section:
            scope += f" / {args.section}"
    model_info = GEMMA_MODELS[args.model]
    print("Reading Library Chatbot (CLI)")
    print(f"  Model   : {model_info['label']} (local)")
    print(f"  Device  : {args.device}")
    print(f"  Scope   : {scope}")
    print("  Commands: /scope all|book|chapter  /book <slug>  /section <id>")
    print("            /show-context  /help  /quit")
    print()


def handle_slash_command(line: str, args: argparse.Namespace) -> bool:
    if not line.startswith("/"):
        return False
    parts = line[1:].split()
    cmd = parts[0].lower() if parts else ""
    if cmd in ("quit", "exit", "q"):
        raise SystemExit(0)
    if cmd == "help":
        print_banner(args)
        return True
    if cmd == "scope" and len(parts) >= 2 and parts[1] in ("all", "book", "chapter"):
        args.scope = parts[1]
        print(f"Scope set to: {args.scope}")
        return True
    if cmd == "book" and len(parts) >= 2:
        args.book = parts[1]
        print(f"Book set to: {args.book}")
        return True
    if cmd == "section" and len(parts) >= 2:
        args.section = parts[1]
        print(f"Section set to: {args.section}")
        return True
    if cmd == "show-context":
        args.show_context = not args.show_context
        print(f"Show context: {args.show_context}")
        return True
    print(f"Unknown command: {line}")
    return True


def run_interactive(rag: RagIndex, args: argparse.Namespace) -> int:
    print_banner(args)
    model_info = GEMMA_MODELS[args.model]
    print(
        f"Loading {model_info['label']}… (first run downloads ~{model_info['size_gb']} GB)",
        file=sys.stderr,
    )
    try:
        get_gemma(args.device, args.model).start()
    except GemmaChatError as err:
        print(f"Error: {err}", file=sys.stderr)
        return 1

    history: list[dict[str, str]] = []
    while True:
        try:
            line = input("You: ").strip()
        except (EOFError, KeyboardInterrupt):
            print()
            break
        if not line:
            continue
        try:
            if handle_slash_command(line, args):
                continue
            answer_question(line, rag, args, history)
        except GemmaChatError as err:
            print(f"\nError: {err}", file=sys.stderr)
        except RuntimeError as err:
            print(f"\nError: {err}", file=sys.stderr)
    return 0


def main(argv: list[str] | None = None) -> int:
    args = parse_args(argv)
    validate_args(args)

    print("Loading RAG index…", file=sys.stderr)
    try:
        rag = RagIndex()
    except (FileNotFoundError, ValueError) as err:
        print(f"Error: {err}", file=sys.stderr)
        return 1
    print(f"  {len(rag.chunks)} chunks ready\n", file=sys.stderr)

    if args.question:
        history: list[dict[str, str]] = []
        try:
            model_info = GEMMA_MODELS[args.model]
            print(f"Loading {model_info['label']}…", file=sys.stderr)
            get_gemma(args.device, args.model).start()
            answer_question(args.question, rag, args, history)
        except GemmaChatError as err:
            print(f"Error: {err}", file=sys.stderr)
            return 1
        except RuntimeError as err:
            print(f"Error: {err}", file=sys.stderr)
            return 1
        return 0

    return run_interactive(rag, args)


if __name__ == "__main__":
    raise SystemExit(main())
