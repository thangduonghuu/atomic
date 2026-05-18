#!/usr/bin/env python3
import os
import re
import sys
import shutil
import threading
from rich.console import Console
from rich.live import Live
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
CODE_BLOCK = re.compile(r'```(bash|sh|shell|python|py)\n(.*?)```', re.DOTALL)
THINK_BLOCK = re.compile(r'<think>(.*?)</think>', re.DOTALL)

LANG_NORM = {"sh": "bash", "shell": "bash", "py": "python", "python3": "python"}

_script_session_allowed: bool | None = None  # None=not asked, True=allowed, False=denied


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

    app = Application(layout=layout, key_bindings=kb, style=INPUT_STYLE, full_screen=False, erase_when_done=True)
    app.run()
    return (result[0] or "").strip()


SYSTEM_PROMPT = """You are a local coding assistant with direct filesystem access on this machine.
Current working directory: {cwd}

IMPORTANT: You CAN and MUST read files using these tools. Never say you cannot access files.

TOOLS — output exactly these tags to use them:
  <list_dir path="."/>
  <read_file path="app.py"/>

When suggesting commands or scripts, wrap them in fenced code blocks with the language tag so they can be executed directly.

CRITICAL RULES for bash scripts:
- ALWAYS put ALL related commands in a SINGLE bash block. Each bash block runs as an independent subprocess — cd commands do NOT persist between blocks.
- WRONG (cd lost between blocks):
  ```bash
  cd myproject
  ```
  ```bash
  npm install
  ```
- CORRECT (everything in one block):
  ```bash
  cd myproject && npm install && npm run build
  ```
- When creating a project and writing files, do it all in one bash block using heredocs.
  ALWAYS create ALL subdirectories before writing files into them:
  ```bash
  mkdir -p myapp/src myapp/public && cd myapp
  cat > src/App.jsx << 'EOF'
  // your code here
  EOF
  cat > public/index.html << 'EOF'
  <!-- html here -->
  EOF
  npm install
  ```

EXAMPLES:
User: check this project
Assistant: <list_dir path="."/>

User: what's in main.py?
Assistant: <read_file path="main.py"/>

User: how much disk space is left?
Assistant: ```bash
df -h .
```

After receiving file contents or script output, give your analysis. Never say you cannot access the filesystem."""


def make_history() -> list[dict]:
    dir_listing = tools.list_dir(".")
    return [
        {"role": "system", "content": SYSTEM_PROMPT.format(cwd=os.getcwd())},
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


def _print_script(lang: str, code: str):
    console.print(f"\n  [cyan bold]▶ {lang}[/cyan bold]")
    for line in code.splitlines():
        console.print(f"  [dim]│[/dim] {line}")


def _run_with_autofix(code: str, lang: str, history: list[dict], max_retries: int = 3):
    for attempt in range(max_retries):
        output_lines = []
        with Live("  [dim]running...[/dim]", console=console, refresh_per_second=15, transient=False) as live:
            def on_line(line: str):
                output_lines.append(line)
                display = "\n".join(f"  [dim]│[/dim] {l}" for l in output_lines[-20:])
                live.update(display)
            result = tools.run_script(code, lang, on_line=on_line)

        if result["ok"]:
            console.print(f"  [green]✓ done[/green]\n")
            history.append({"role": "user", "content": f"Script ran successfully. Output:\n```\n{result['output']}\n```"})
            return

        console.print(f"  [red]✗ error (exit {result['returncode']}):[/red]\n{result['output']}\n")

        if attempt == max_retries - 1:
            history.append({"role": "user", "content": f"Script failed:\n```\n{result['output']}\n```"})
            return

        console.print("  [dim]asking AI to fix...[/dim]", end="\r")
        history.append({"role": "user", "content": (
            f"Script failed (exit {result['returncode']}):\n```\n{result['output']}\n```\n"
            f"Fix the script and provide only the corrected version."
        )})
        fix_result = llm.chat(history)
        fix_reply = fix_result["content"]
        console.print("           \r", end="")
        console.print(Markdown(fix_reply))
        history.append({"role": "assistant", "content": fix_reply})

        fix_match = CODE_BLOCK.search(fix_reply)
        if not fix_match:
            return

        lang = LANG_NORM.get(fix_match.group(1), fix_match.group(1))
        code = fix_match.group(2).strip()
        console.print(f"  [yellow]retrying (attempt {attempt + 2}/{max_retries})...[/yellow]")
        _print_script(lang, code)


def _is_truncated(text: str) -> bool:
    """Detect obvious truncation: unclosed code block."""
    return text.count("```") % 2 != 0


def _stream_chat(history: list[dict]) -> tuple[str, dict] | tuple[None, None]:
    buffer = []
    done = threading.Event()

    error = None
    result = None
    with Live("[dim]thinking...[/dim]", console=console, refresh_per_second=4, transient=True) as live:
        def on_token(t: str):
            buffer.append(t)
        try:
            result = llm.chat(history, on_token=on_token)
        except Exception as e:
            error = e
        finally:
            done.set()

    if error:
        console.print(f"  [red]error: {error}[/red]")
        return None, None
    return result["content"], result


def _respond(history: list[dict]) -> str | None:
    reply, result = _stream_chat(history)
    if reply is None:
        return None

    # If response was cut off mid-code-block, ask the model to continue
    for _ in range(3):
        if not _is_truncated(reply):
            break
        console.print("  [dim]↻ response truncated, continuing...[/dim]")
        history.append({"role": "assistant", "content": reply})
        history.append({"role": "user", "content": "Continue your response from where you left off."})
        cont, cont_result = _stream_chat(history)
        history.pop()
        history.pop()
        if cont is None:
            break
        reply = reply.rstrip() + "\n" + cont
        result = cont_result

    reply, tool_stats = process_tool_calls(reply, history)
    if tool_stats:
        result = tool_stats

    elapsed = result.get("elapsed", 0)
    comp = result.get("completion_tokens", 0)
    tps = comp / elapsed if elapsed > 0 else 0

    think_match = THINK_BLOCK.search(reply)
    if think_match:
        thinking = think_match.group(1).strip()
        reply = THINK_BLOCK.sub("", reply).strip()
        think_tokens = len(thinking.split())
        console.print(f"\n[dim]  ◦ thought for ~{think_tokens} words[/dim]\n")

    console.print(Markdown(reply))
    console.print(f"\n[dim]  ⏱ {elapsed:.1f}s · {comp} tok · {tps:.0f} tok/s[/dim]")
    return reply


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
            model_path = model_picker.pick_model(force=True)
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

        reply = _respond(history)
        if reply is None:
            continue

        history.append({"role": "assistant", "content": reply})

        scripts_ran = False
        for match in CODE_BLOCK.finditer(reply):
            global _script_session_allowed
            lang = LANG_NORM.get(match.group(1), match.group(1))
            code = match.group(2).strip()

            _print_script(lang, code)

            if _script_session_allowed is None:
                console.print("\n  [yellow]Run scripts automatically this session?[/yellow] [dim](y / n)[/dim]")
                try:
                    answer = input("  > ").strip().lower()
                except (EOFError, KeyboardInterrupt):
                    break
                _script_session_allowed = answer in ("y", "yes")

            if not _script_session_allowed:
                continue

            _run_with_autofix(code, lang, history)
            scripts_ran = True

        if scripts_ran:
            follow_up = _respond(history)
            if follow_up:
                history.append({"role": "assistant", "content": follow_up})





def _model_add(path: str):
    from atomic import config as cfg_mod
    path = os.path.expanduser(path)
    if not os.path.exists(path):
        console.print(f"[red]File not found: {path}[/red]")
        sys.exit(1)
    cfg_mod.set_default(path)
    console.print(f"[green]Registered and set as default:[/green] {os.path.basename(path)}")


def _model_download(repo_id: str | None):
    from atomic import download as dl
    from atomic import config as cfg_mod
    try:
        path = dl.interactive_download(repo_id)
        cfg_mod.set_default(path)
        console.print(f"\n[green]Downloaded and set as default:[/green] {os.path.basename(path)}")
    except (KeyboardInterrupt, EOFError):
        console.print("\n[dim]Cancelled.[/dim]")
    except Exception as e:
        console.print(f"[red]Error: {e}[/red]")
        sys.exit(1)


def _model_list():
    from atomic import config as cfg_mod
    cfg = cfg_mod.load()
    default = cfg.get("default_model")
    recent = cfg.get("recent_models", [])
    found = cfg_mod.find_gguf_files()

    seen = set()
    options = []
    for p in recent + found:
        if p not in seen:
            seen.add(p)
            options.append(p)

    if not options:
        console.print("[dim]No models found.[/dim]")
        return

    console.print("\n  [bold]Available models:[/bold]\n")
    for p in options:
        exists = os.path.exists(p)
        marker = "[green]*[/green]" if p == default else " "
        status = "" if exists else " [red](missing)[/red]"
        console.print(f"  {marker} {os.path.basename(p)}{status}")
        console.print(f"    [dim]{p}[/dim]")
    console.print()


def _print_help():
    console.print("\n  [bold cyan]atomic[/bold cyan] — local LLM assistant\n")
    console.print("  [bold]Usage:[/bold]")
    console.print("    atomic                        Start chat")
    console.print("    atomic model                  Re-select default model")
    console.print("    atomic model list             List available models")
    console.print("    atomic model add <path>       Register a local .gguf file")
    console.print("    atomic model download [repo]  Download from HuggingFace\n")
    console.print("  [dim]Inside chat: /model  /clear  /read <path>  /exit[/dim]\n")


def main():
    args = sys.argv[1:]

    if not args:
        run()
        return

    if args[0] in ("-h", "--help", "help"):
        _print_help()
        return

    if args[0] == "model":
        sub = args[1] if len(args) > 1 else None
        if sub is None:
            model_picker.pick_model(force=True)
        elif sub == "list":
            _model_list()
        elif sub == "add":
            if len(args) < 3:
                console.print("[red]Usage: atomic model add <path>[/red]")
                sys.exit(1)
            _model_add(args[2])
        elif sub == "download":
            _model_download(args[2] if len(args) > 2 else None)
        else:
            console.print(f"[red]Unknown subcommand: {sub}[/red]")
            _print_help()
            sys.exit(1)
        return

    console.print(f"[red]Unknown command: {args[0]}[/red]")
    _print_help()
    sys.exit(1)


if __name__ == "__main__":
    main()
