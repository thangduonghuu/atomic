#!/usr/bin/env python3
import os
import re
import sys
import time
import queue
import shutil
import difflib
import readline  # noqa: F401 — enables arrow keys, history, backspace via input()
import threading
from rich.console import Console
from rich.live import Live
from rich.markdown import Markdown
from rich.panel import Panel
from rich.rule import Rule
from rich.text import Text

from atomic import llm, tools, model_picker

console = Console()

_session_written: list[tuple[str, str]] = []  # (path, "created"|"modified")

DIFF_COLLAPSE_THRESHOLD = 40

EXT_TO_LANG = {
    ".py": "python", ".js": "javascript", ".ts": "typescript",
    ".jsx": "javascript", ".tsx": "typescript", ".sh": "bash",
    ".bash": "bash", ".json": "json", ".yaml": "yaml", ".yml": "yaml",
    ".toml": "toml", ".md": "markdown", ".html": "html", ".css": "css",
    ".rs": "rust", ".go": "go", ".rb": "ruby", ".java": "java",
    ".c": "c", ".cpp": "cpp", ".h": "c",
}

TOOL_READ    = re.compile(r'<read_file\s+path=["\']([^"\']+)["\']')
TOOL_LIST    = re.compile(r'<list_dir\s+path=["\']([^"\']+)["\']')
TOOL_WRITE   = re.compile(r'<write_file\s+path=["\']([^"\']+)["\']\s*>(.*?)</write_file>', re.DOTALL)
CREATE_NOTE  = re.compile(r'<create_note\s+path=["\']([^"\']+)["\']\s*>(.*?)</create_note>', re.DOTALL)
CODE_BLOCK   = re.compile(r'```(bash|sh|shell|python|py|bash-server)\n(.*?)```', re.DOTALL)
THINK_BLOCK  = re.compile(r'<think>(.*?)</think>', re.DOTALL)

LANG_NORM = {"sh": "bash", "shell": "bash", "py": "python", "python3": "python"}

_script_session_allowed: bool | None = None  # None=not asked, True=allowed, False=denied
_server_queue: queue.Queue = queue.Queue()


def _server_print(line: str) -> None:
    _server_queue.put(line)


def _flush_server_output() -> None:
    lines = []
    try:
        while True:
            lines.append(_server_queue.get_nowait())
    except queue.Empty:
        pass
    for line in lines:
        console.print(f"  [dim]│[/dim] [cyan][srv][/cyan] {line}")


def get_input() -> str:
    w = shutil.get_terminal_size().columns
    border = f"[dim]{'─' * w}[/dim]"
    console.print(border)
    try:
        line = input(" > ")
    except KeyboardInterrupt:
        print()
        raise
    console.print(border)
    return line.strip()



SYSTEM_PROMPT = """You are a local coding assistant with direct filesystem access on this machine.
Current working directory: {cwd}

You have the following tools. Use them freely — never say you cannot access files.

TOOLS:

1. List a directory:
   <list_dir path="."/>

2. Read a file:
   <read_file path="src/app.py"/>

3. Write (create or overwrite) a file — use this instead of bash heredocs:
   <write_file path="src/app.py">
   full file content here
   </write_file>

4. Run a one-time shell command (auto-executed — must exit on its own):
   ```bash
   command here
   ```
   Each bash block is an independent subprocess. Chain commands with && if order matters.

5. Show a long-running command the user must start manually (NOT auto-executed):
   ```bash-server
   command here
   ```
   Use this for anything that runs forever: dev servers, watchers, repls, etc.
   The system will display it but tell the user to run it in a separate terminal.

6. Create a work note — a markdown planning document saved to disk:
   <create_note path=".notes/plan.md">
   # Plan: Feature Name
   ...
   </create_note>
   Use this when a task is complex enough to warrant a written plan. Prefer .notes/ as the directory.

WORKFLOW:
- Explore with <list_dir> and <read_file> to understand the codebase.
- Create or edit files with <write_file>.
- Use ```bash``` for install, build, test, scaffold — commands that finish and exit.
- Use ```bash-server``` for start/serve/watch commands that stay running.
- After each tool result, continue until the task is complete.

EXAMPLES:
User: what files are here?
Assistant: <list_dir path="."/>

User: show me main.py
Assistant: <read_file path="main.py"/>

User: create a hello world script
Assistant: <write_file path="hello.py">
print("Hello, world!")
</write_file>

User: set up a react app with vite
Assistant: ```bash
npm create vite@latest . -- --template react && npm install
```
Now start the dev server:
```bash-server
npm run dev
```"""


THINK_SYSTEM_PROMPT = """You are a deep-thinking software investigator. Your job is to thoroughly understand a problem and produce a structured, actionable work note.
Current working directory: {cwd}

You have these investigation tools:

1. List a directory:
   <list_dir path="."/>

2. Read a file:
   <read_file path="src/app.py"/>

3. Create a work note — your primary deliverable:
   <create_note path=".notes/plan.md">
   # Plan: Feature Name

   ## Problem
   What needs to be done and why.

   ## Investigation
   What you found in the codebase that is relevant.

   ## Approach
   The strategy you recommend.

   ## Steps
   - [ ] Step 1 — file: path/to/file.py
   - [ ] Step 2 — file: path/to/other.py
   ...

   ## Open Questions
   Anything that needs clarification before or during implementation.
   </create_note>

WORKFLOW:
1. Use <list_dir> and <read_file> to understand the relevant parts of the codebase.
2. Think carefully: what exists, what is missing, what are the constraints and trade-offs.
3. Always end with a <create_note> containing your full investigation and implementation plan.
4. Be specific — name exact files, functions, and line-level changes where possible.
"""


def make_history() -> list[dict]:
    dir_listing = tools.list_dir(".")
    return [
        {"role": "system", "content": SYSTEM_PROMPT.format(cwd=os.getcwd())},
        {"role": "user", "content": f"FYI, current directory contains:\n{dir_listing}"},
        {"role": "assistant", "content": "Got it, I can see the files in your current directory."},
    ]


def _make_think_history() -> list[dict]:
    dir_listing = tools.list_dir(".")
    return [
        {"role": "system", "content": THINK_SYSTEM_PROMPT.format(cwd=os.getcwd())},
        {"role": "user", "content": f"Current directory contains:\n{dir_listing}"},
        {"role": "assistant", "content": "Ready to investigate. What would you like me to plan?"},
    ]


def _get_lang(path: str) -> str:
    return EXT_TO_LANG.get(os.path.splitext(path)[1].lower(), "text")


def _hl_line(code: str, lang: str) -> Text:
    try:
        from pygments import highlight as pyg_highlight
        from pygments.lexers import get_lexer_by_name
        from pygments.formatters import Terminal256Formatter
        lexer = get_lexer_by_name(lang, stripall=True)
        ansi = pyg_highlight(code, lexer, Terminal256Formatter(style="monokai"))
        return Text.from_ansi(ansi.rstrip())
    except Exception:
        return Text(code)


def _make_diff_text(diff_lines: list[str], lang: str) -> Text:
    result = Text()
    for line in diff_lines:
        s = line.rstrip("\n")
        if s.startswith("+"):
            result.append("+ ", style="bold green")
            result.append_text(_hl_line(s[1:], lang))
            result.append("\n")
        elif s.startswith("-"):
            result.append("- ", style="bold red")
            result.append(s[1:] + "\n", style="red")
        elif s.startswith("@@"):
            result.append(s + "\n", style="cyan dim")
        else:
            result.append("  ")
            result.append_text(_hl_line(s[1:] if s.startswith(" ") else s, lang))
            result.append("\n")
    return result


def _print_diff(path: str, new_content: str) -> bool:
    """Show diff panel and ask for confirmation. Returns True to proceed with write."""
    real = os.path.join(os.getcwd(), path)
    is_new = not os.path.exists(real)

    if is_new:
        old_lines = []
    else:
        try:
            with open(real, "r", errors="replace") as f:
                old_lines = f.read().splitlines(keepends=True)
        except Exception:
            old_lines = []

    new_lines = new_content.splitlines(keepends=True)
    diff = list(difflib.unified_diff(old_lines, new_lines, lineterm=""))

    if not diff:
        return True

    lang = _get_lang(path)
    added = sum(1 for l in diff if l.startswith("+") and not l.startswith("+++"))
    removed = sum(1 for l in diff if l.startswith("-") and not l.startswith("---"))
    diff_lines = diff[2:]  # skip --- +++ headers

    if is_new:
        stat = f"[dim]new[/dim]  [green]+{added}[/green]"
    else:
        stat = f"[green]+{added}[/green] [red]-{removed}[/red]"
    title = f"[bold cyan]{path}[/bold cyan]  {stat}"

    # Feature 2: collapse large diffs
    if len(diff_lines) > DIFF_COLLAPSE_THRESHOLD:
        console.print(Panel(
            f"[dim]{len(diff_lines)} lines changed[/dim]",
            title=title, border_style="dim",
        ))
        try:
            ans = input("  Show full diff? (y/N) > ").strip().lower()
        except (EOFError, KeyboardInterrupt):
            ans = "n"
        if ans in ("y", "yes"):
            console.print(Panel(_make_diff_text(diff_lines, lang), title=title, border_style="dim"))
    else:
        # Features 3 + 4: panel with syntax-highlighted diff
        console.print(Panel(_make_diff_text(diff_lines, lang), title=title, border_style="dim"))

    # Feature 1: confirm before writing
    try:
        ans = input("  Apply? (Y/n) > ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False
    return ans in ("y", "yes", "")


def _print_session_summary() -> None:
    if not _session_written:
        return
    created = [p for p, k in _session_written if k == "created"]
    modified = [p for p, k in _session_written if k == "modified"]
    parts = []
    if created:
        parts.append(f"  [green]Created:[/green] {', '.join(f'[cyan]{p}[/cyan]' for p in created)}")
    if modified:
        parts.append(f"  [yellow]Modified:[/yellow] {', '.join(f'[cyan]{p}[/cyan]' for p in modified)}")
    console.print("\n" + "\n".join(parts))


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

        for path, content in TOOL_WRITE.findall(response):
            stripped = content.lstrip("\n")
            real = os.path.join(os.getcwd(), path)
            is_new = not os.path.exists(real)
            console.print(f"  [dim]⚙ writing {path}[/dim]")
            if _print_diff(path, stripped):
                result_msg = tools.write_file(path, stripped)
                _session_written.append((path, "created" if is_new else "modified"))
            else:
                result_msg = f"User rejected changes to {path}. Do not attempt to rewrite this file unless asked."
            tool_results.append(result_msg)

        for path, content in CREATE_NOTE.findall(response):
            console.print(f"  [dim]⚙ creating note {path}[/dim]")
            result_msg = tools.write_file(path, content.lstrip("\n"))
            console.print(f"  [green]◉ note saved →[/green] [cyan]{path}[/cyan]")
            tool_results.append(result_msg)

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


def _run_with_autofix(code: str, lang: str, history: list[dict], max_retries: int = 3) -> bool:
    """Returns True if a follow-up model response is needed, False otherwise."""
    for attempt in range(max_retries):
        output_lines = []
        import time as _time
        start = _time.monotonic()

        try:
            with Live("  [dim]running...[/dim]", console=console, refresh_per_second=15, transient=False) as live:
                def on_line(line: str):
                    output_lines.append(line)
                    elapsed = _time.monotonic() - start
                    timer = f"[dim]{elapsed:.1f}s[/dim]"
                    body = "\n".join(f"  [dim]│[/dim] {l}" for l in output_lines[-20:])
                    live.update(f"  {timer}\n{body}")
                result = tools.run_script(code, lang, on_line=on_line)
        except KeyboardInterrupt:
            console.print("\n  [dim]interrupted[/dim]\n")
            return False

        elapsed = _time.monotonic() - start
        if result["ok"]:
            console.print(f"  [green]✓ done[/green]  [dim]{elapsed:.1f}s[/dim]\n")
            history.append({"role": "user", "content": f"Script ran successfully. Output:\n```\n{result['output']}\n```"})
            return True

        if result.get("is_server"):
            console.print(f"  [cyan]⚙ server detected, starting in background...[/cyan]  [dim]{elapsed:.1f}s[/dim]\n")
            tools.run_server_background(code, on_line=_server_print)
            return False

        console.print(f"  [red]✗ error (exit {result['returncode']}):[/red]\n{result['output']}\n")

        if attempt == max_retries - 1:
            history.append({"role": "user", "content": f"Script failed:\n```\n{result['output']}\n```"})
            return True

        console.print("  [dim]asking AI to fix...[/dim]", end="\r")
        history.append({"role": "user", "content": (
            f"The following {lang} script exited with code {result['returncode']}:\n"
            f"```{lang}\n{code}\n```\n"
            f"Output:\n```\n{result['output']}\n```\n"
            f"Note: if exit code 1 came from grep/find finding no results, that may mean the "
            f"previous step succeeded — in that case, rewrite without the grep verification, "
            f"or use `|| true` to suppress the exit code. Otherwise fix the real error. "
            f"Provide only the corrected script."
        )})
        fix_result = llm.chat(history)
        fix_reply = fix_result["content"]
        console.print("           \r", end="")
        console.print(Markdown(fix_reply))
        history.append({"role": "assistant", "content": fix_reply})

        fix_match = CODE_BLOCK.search(fix_reply)
        if not fix_match:
            return True

        lang = LANG_NORM.get(fix_match.group(1), fix_match.group(1))
        code = fix_match.group(2).strip()
        console.print(f"  [yellow]retrying (attempt {attempt + 2}/{max_retries})...[/yellow]")
        _print_script(lang, code)
    return True


def _is_truncated(text: str) -> bool:
    if text.count("```") % 2 != 0:
        return True
    for tag in ("read_file", "write_file", "list_dir"):
        if text.count(f"<{tag}") > text.count(f"</{tag}>"):
            return True
    return False


def _stream_chat(history: list[dict]) -> tuple[str, dict] | tuple[None, None]:
    text_parts: list[str] = []
    error = None
    result = None

    with Live("[dim]thinking...[/dim]", console=console, refresh_per_second=12, transient=True) as live:
        def on_token(t: str):
            text_parts.append(t)
            current = "".join(text_parts)
            lines = current.splitlines()
            display = "\n".join(lines[-25:])
            live.update(Text(display))

        try:
            result = llm.chat(history, on_token=on_token)
        except KeyboardInterrupt:
            llm.stop()
            console.print("\n  [dim]interrupted[/dim]")
            return None, None
        except Exception as e:
            error = e

    if error:
        console.print(f"  [red]error: {error}[/red]")
        return None, None
    return result["content"], result


def _stream_think(history: list[dict]) -> tuple[str, dict] | tuple[None, None]:
    text_parts: list[str] = []
    error = None
    result = None

    with Live("[dim]investigating...[/dim]", console=console, refresh_per_second=12, transient=True) as live:
        def on_token(t: str):
            text_parts.append(t)
            current = "".join(text_parts)
            lines = current.splitlines()
            display = "\n".join(lines[-25:])
            live.update(Text(display))

        try:
            result = llm.think_chat(history, on_token=on_token)
        except KeyboardInterrupt:
            llm.stop()
            console.print("\n  [dim]interrupted[/dim]")
            return None, None
        except Exception as e:
            error = e

    if error:
        console.print(f"  [red]error: {error}[/red]")
        return None, None
    return result["content"], result


def _run_think(prompt: str) -> None:
    think_name = llm.get_think_model_name()
    console.print(f"\n  [dim]◉ thinking with {think_name}...[/dim]")
    history = _make_think_history()
    history.append({"role": "user", "content": prompt})

    reply, result = _stream_think(history)
    if reply is None:
        return

    # process tool calls (read_file, list_dir, create_note) in a loop
    while True:
        tool_results = []

        for path in TOOL_LIST.findall(reply):
            console.print(f"  [dim]⚙ listing {path}[/dim]")
            tool_results.append(f"Directory listing of {path}:\n{tools.list_dir(path)}")

        for path in TOOL_READ.findall(reply):
            console.print(f"  [dim]⚙ reading {path}[/dim]")
            content = tools.read_file(path)
            tool_results.append(f"Contents of {path}:\n```\n{content}\n```" if content else f"Could not read {path}.")

        for path, content in CREATE_NOTE.findall(reply):
            console.print(f"  [dim]⚙ creating note {path}[/dim]")
            tools.write_file(path, content.lstrip("\n"))
            console.print(f"  [green]◉ note saved →[/green] [cyan]{path}[/cyan]")

        if not tool_results:
            break

        history.append({"role": "assistant", "content": reply})
        history.append({"role": "user", "content": "\n\n".join(tool_results)})
        next_reply, result = _stream_think(history)
        if next_reply is None:
            break
        reply = next_reply

    # strip create_note blocks from display output
    display = CREATE_NOTE.sub(
        lambda m: f"*→ note saved: `{m.group(1)}`*",
        reply,
    ).strip()

    think_match = THINK_BLOCK.search(display)
    if think_match:
        thinking = think_match.group(1).strip()
        display = THINK_BLOCK.sub("", display).strip()
        think_tokens = len(thinking.split())
        console.print(f"\n[dim]  ◦ thought for ~{think_tokens} words[/dim]\n")

    console.print(Markdown(display))
    elapsed = result.get("elapsed", 0)
    comp = result.get("completion_tokens", 0)
    tps = comp / elapsed if elapsed > 0 else 0
    console.print(f"\n[dim]  ⏱ {elapsed:.1f}s · {comp} tok · {tps:.0f} tok/s[/dim]")


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
    ctx_used, ctx_max = llm.estimate_context_usage(history)
    ctx_info = f"  ctx {ctx_used:,}/{ctx_max:,}" if ctx_max else ""
    console.print(f"\n[dim]  ⏱ {elapsed:.1f}s · {comp} tok · {tps:.0f} tok/s{ctx_info}[/dim]")
    return reply


def _save_conversation(history: list[dict], path: str) -> None:
    lines = [f"# atomic session — {time.strftime('%Y-%m-%d %H:%M')}\n"]
    for msg in history:
        if msg["role"] == "system":
            continue
        lines.append(f"\n## {msg['role'].capitalize()}\n")
        lines.append(msg["content"])
        lines.append("\n")
    try:
        with open(path, "w") as f:
            f.write("\n".join(lines))
        console.print(f"  [green]Saved to {path}[/green]\n")
    except Exception as e:
        console.print(f"  [red]Failed to save: {e}[/red]\n")


def run():
    global _script_session_allowed
    from atomic import config as cfg_mod

    model_path = model_picker.pick_model()
    console.print(f"\n  [dim]Loading model...[/dim]", end="\r")
    llm.load_model(model_path)

    think_path = cfg_mod.get_thinking_model()
    if think_path and os.path.exists(think_path):
        llm.load_think_model(think_path)

    model_name = os.path.basename(model_path)
    console.print(Rule(f"[cyan]atomic[/cyan]  [dim]{model_name}[/dim]"))

    history = make_history()

    while True:
        _flush_server_output()
        try:
            user_input = get_input()
        except EOFError:
            llm.stop()
            _print_session_summary()
            console.print("\n[dim]bye.[/dim]")
            break
        except KeyboardInterrupt:
            continue

        _flush_server_output()
        if not user_input:
            continue

        if user_input in ("/exit", "/quit"):
            llm.stop()
            _print_session_summary()
            console.print("[dim]bye.[/dim]")
            break

        if user_input == "/server":
            if tools.stop_server_background():
                console.print("  [dim]server stopped[/dim]\n")
            else:
                console.print("  [dim]no server running[/dim]\n")
            continue

        if user_input == "/clear":
            _print_session_summary()
            _session_written.clear()
            console.clear()
            console.print(Rule(f"[cyan]atomic[/cyan]  [dim]{model_name}[/dim]"))
            history = make_history()
            _script_session_allowed = None
            continue

        if user_input == "/help":
            console.print("""
  [bold]Commands:[/bold]

  [cyan]/read <path>[/cyan]          Load a file into context
  [cyan]/model[/cyan]                Switch main model mid-session
  [cyan]/think <prompt>[/cyan]       Deep investigation → saves a work note
  [cyan]/think-model[/cyan]          Set the thinking model
  [cyan]/server[/cyan]               Stop the background server
  [cyan]/save [path][/cyan]          Save conversation to a markdown file
  [cyan]/clear[/cyan]                Reset conversation history
  [cyan]/help[/cyan]                 Show this help
  [cyan]/exit[/cyan], [cyan]/quit[/cyan]        Exit
""")
            continue

        if user_input.startswith("/save"):
            parts = user_input.split(None, 1)
            save_path = parts[1].strip() if len(parts) > 1 else f"atomic-{int(time.time())}.md"
            _save_conversation(history, save_path)
            continue

        if user_input.startswith("/think "):
            prompt = user_input[7:].strip()
            if prompt:
                _run_think(prompt)
            else:
                console.print("  [dim]Usage: /think <what to investigate>[/dim]\n")
            continue

        if user_input == "/think-model":
            think_path = model_picker.pick_model(force=True)
            console.print("  [dim]Loading thinking model...[/dim]", end="\r")
            from atomic import config as cfg_mod
            cfg_mod.set_thinking_model(think_path)
            llm.load_think_model(think_path)
            console.print(f"  [dim]Thinking model set to {os.path.basename(think_path)}[/dim]\n")
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

        while True:
            scripts_ran = False
            for match in CODE_BLOCK.finditer(reply):
                raw_lang = match.group(1)
                lang = LANG_NORM.get(raw_lang, raw_lang)
                code = match.group(2).strip()

                if raw_lang == "bash-server":
                    _print_script("bash", code)
                    tools.run_server_background(code, on_line=_server_print)
                    console.print("  [cyan]↑ server started in background  (Ctrl+C or /server to stop)[/cyan]\n")
                    history.append({"role": "user", "content": "Server is now running in the background. Output is streaming to the terminal."})
                    continue

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

                needs_followup = _run_with_autofix(code, lang, history)
                if needs_followup:
                    scripts_ran = True

            if not scripts_ran:
                break

            reply = _respond(history)
            if reply is None:
                break
            history.append({"role": "assistant", "content": reply})





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
    console.print("  [dim]Inside chat: /think <prompt>  /think-model  /model  /clear  /read <path>  /exit[/dim]\n")


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
