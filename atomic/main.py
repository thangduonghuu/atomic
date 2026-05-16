#!/usr/bin/env python3
import os
import re

import shutil
from rich.console import Console
from rich.markdown import Markdown
from rich.rule import Rule
from prompt_toolkit import Application
from prompt_toolkit.buffer import Buffer
from prompt_toolkit.layout import Layout
from prompt_toolkit.layout.containers import HSplit, Window
from prompt_toolkit.layout.controls import BufferControl, FormattedTextControl
from prompt_toolkit.key_binding import KeyBindings
from prompt_toolkit.styles import Style

from atomic import llm, tools, model_picker

console = Console()

TOOL_READ = re.compile(r'<read_file\s+path=["\']([^"\']+)["\']')
TOOL_LIST = re.compile(r'<list_dir\s+path=["\']([^"\']+)["\']')

INPUT_STYLE = Style.from_dict({
    "":       "bg:#1c1c1c #e0e0e0",
    "prompt": "bg:#1c1c1c #00d7ff bold",
})

def get_input() -> str:
    result = [None]
    buf = Buffer()
    kb = KeyBindings()

    @kb.add("enter")
    def submit(event):
        result[0] = buf.text
        event.app.exit()

    @kb.add("c-c")
    def cancel(event):
        event.app.exit(exception=KeyboardInterrupt)

    @kb.add("c-d")
    def eof(event):
        event.app.exit(exception=EOFError)

    w = shutil.get_terminal_size().columns
    border = [("fg:#444444", "─" * w)]

    layout = Layout(
        HSplit([
            Window(FormattedTextControl(border), height=1),
            Window(
                BufferControl(buffer=buf),
                height=1,
                style="class:",
                get_line_prefix=lambda lineno, wrap_count: [("class:prompt", " > ")],
            ),
            Window(FormattedTextControl(border), height=1),
        ])
    )

    app = Application(layout=layout, key_bindings=kb, style=INPUT_STYLE, full_screen=False)
    app.run()
    return (result[0] or "").strip()


SYSTEM_PROMPT = f"""You are a local coding assistant with direct filesystem access on this machine.
Current working directory: {os.getcwd()}

IMPORTANT: You CAN and MUST read files using these tools. Never say you cannot access files.

TOOLS — output exactly these tags to use them:
  <list_dir path="."/>
  <read_file path="app.py"/>

EXAMPLES:
User: check this project
Assistant: <list_dir path="."/>

User: what's in main.py?
Assistant: <read_file path="main.py"/>

After receiving file contents, give your review. Never say you cannot access the filesystem."""


def make_history() -> list[dict]:
    dir_listing = tools.list_dir(".")
    return [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"FYI, current directory contains:\n{dir_listing}"},
        {"role": "assistant", "content": "Got it, I can see the files in your current directory."},
    ]


def process_tool_calls(response: str, history: list[dict]) -> tuple[str, dict]:
    stats = {}
    while True:
        tool_results = []

        for path in TOOL_LIST.findall(response):
            console.print(f"  [dim]⚙ listing {path}[/dim]")
            tool_results.append(f"Directory listing of {path}:\n{tools.list_dir(path)}")

        for path in TOOL_READ.findall(response):
            console.print(f"  [dim]⚙ reading {path}[/dim]")
            content = tools.read_file(path)
            if content:
                tool_results.append(f"Contents of {path}:\n```\n{content}\n```")
            else:
                tool_results.append(f"Could not read {path}.")

        if not tool_results:
            return response, stats

        history.append({"role": "assistant", "content": response})
        history.append({"role": "user", "content": "\n\n".join(tool_results)})
        result = llm.chat(history)
        response = result["content"]
        stats = result

    return response, stats


def run():
    model_path = model_picker.pick_model()
    console.print(f"\n  [dim]Loading model...[/dim]", end="\r")
    llm.load_model(model_path)

    model_name = os.path.basename(model_path)
    console.print(Rule(f"[cyan]atomic[/cyan]  [dim]{model_name}[/dim]"))

    history = make_history()

    while True:
        try:
            user_input = get_input()
        except (EOFError, KeyboardInterrupt):
            llm.stop()
            console.print("\n[dim]bye.[/dim]")
            break

        if not user_input:
            continue

        if user_input in ("/exit", "/quit"):
            llm.stop()
            console.print("[dim]bye.[/dim]")
            break

        if user_input == "/clear":
            console.clear()
            console.print(Rule(f"[cyan]atomic[/cyan]  [dim]{model_name}[/dim]"))
            history = make_history()
            continue

        if user_input == "/model":
            model_path = model_picker.pick_model()
            console.print("  [dim]Loading model...[/dim]", end="\r")
            llm.load_model(model_path)
            model_name = os.path.basename(model_path)
            history = make_history()
            console.print(f"  [dim]Switched to {model_name}[/dim]\n")
            continue

        if user_input.startswith("/read "):
            path = user_input[6:].strip()
            content = tools.read_file(path)
            if content is None:
                continue
            history.append({"role": "user", "content": f"Please review this file ({path}):\n```\n{content}\n```"})
        elif any(kw in user_input.lower() for kw in ("project", "directory", "code", "review", "check", "bug", "fix", "error", "issue")):
            listing = tools.list_dir(".")
            history.append({"role": "user", "content": f"{user_input}\n\n[Current directory files:\n{listing}]"})
        else:
            history.append({"role": "user", "content": user_input})

        console.print("  [dim]thinking...[/dim]", end="\r")
        try:
            result = llm.chat(history)
        except Exception as e:
            console.print(f"  [red]error: {e}[/red]")
            continue

        reply = result["content"]
        reply, tool_stats = process_tool_calls(reply, history)
        if tool_stats:
            result = tool_stats

        elapsed = result.get("elapsed", 0)
        total = result.get("total_tokens", 0)
        tps = result.get("completion_tokens", 0) / elapsed if elapsed > 0 else 0

        console.print("           \r", end="")
        console.print(Markdown(reply))
        console.print(f"\n[dim]  ⏱ {elapsed:.1f}s · {total} tokens · {tps:.0f} tok/s[/dim]")

        history.append({"role": "assistant", "content": reply})


if __name__ == "__main__":
    run()
