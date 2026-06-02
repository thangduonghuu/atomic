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
from rich.table import Table
from rich.text import Text

from atomic import llm, tools, model_picker

console = Console()

_session_written: list[tuple[str, str]] = []  # (path, "created"|"modified")

DIFF_COLLAPSE_THRESHOLD = 40
AGENT_MAX_STEPS = 20
HISTORY_FILE = os.path.expanduser("~/.atomic/history")

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
TOOL_EDIT    = re.compile(r'<edit_file\s+path=["\']([^"\']+)["\']\s*>\s*<old>(.*?)</old>\s*<new>(.*?)</new>\s*</edit_file>', re.DOTALL)
TOOL_GREP    = re.compile(r'<grep_file\s+pattern=["\']([^"\']+)["\']\s*(?:path=["\']([^"\']+)["\'])?\s*/?>')
TOOL_GIT     = re.compile(r'<git\s+cmd=["\']([^"\']+)["\']\s*/?>')
CREATE_NOTE  = re.compile(r'<create_note\s+path=["\']([^"\']+)["\']\s*>(.*?)</create_note>', re.DOTALL)
CODE_BLOCK   = re.compile(r'```(bash|sh|shell|python|py|bash-server)\n(.*?)```', re.DOTALL)
THINK_BLOCK  = re.compile(r'<think>(.*?)</think>', re.DOTALL)
AGENT_DONE   = re.compile(r'<done>(.*?)</done>', re.DOTALL)
_AGENT_STRIP = re.compile(
    r'<(?:list_dir|read_file|grep_file|git)\s[^>]*/?>|'
    r'<(?:write_file|edit_file|create_note)\s[^>]*>.*?</(?:write_file|edit_file|create_note)>|'
    r'<done>.*?</done>',
    re.DOTALL,
)

LANG_NORM = {"sh": "bash", "shell": "bash", "py": "python", "python3": "python"}

_script_session_allowed: bool | None = None  # None=not asked, True=allowed, False=denied
_write_session_allowed: bool | None = None   # None=not asked, True=always, False=ask each time
_server_queue: queue.Queue = queue.Queue()


def _tg_send(token: str, chat_id: str, text: str) -> None:
    import urllib.request, json as _json
    url = f"https://api.telegram.org/bot{token}/sendMessage"
    data = _json.dumps({"chat_id": chat_id, "text": text[:4096]}).encode()
    req = urllib.request.Request(url, data=data, headers={"Content-Type": "application/json"})
    try:
        urllib.request.urlopen(req, timeout=10)
    except Exception:
        pass


def _tg_get_updates(token: str, offset: int, timeout: int = 30) -> list:
    import urllib.request, json as _json
    url = f"https://api.telegram.org/bot{token}/getUpdates?offset={offset}&timeout={timeout}"
    try:
        with urllib.request.urlopen(url, timeout=timeout + 5) as r:
            return _json.loads(r.read()).get("result", [])
    except Exception:
        return []


def _notify_telegram(message: str) -> None:
    from atomic import config as cfg_mod
    tg = cfg_mod.get_telegram()
    if not tg:
        return
    _tg_send(tg["token"], tg["chat_id"], message)


def _setup_telegram() -> None:
    from atomic import config as cfg_mod

    existing = cfg_mod.get_telegram()

    console.print("\n  [bold]Telegram setup[/bold]\n")

    if existing:
        masked = existing["token"][:8] + "..." + existing["token"][-4:]
        console.print(f"  Current token  : [dim]{masked}[/dim]")
        console.print(f"  Current chat_id: [dim]{existing['chat_id']}[/dim]\n")
        console.print("  Press Enter to keep the current value, or type a new one.\n")

    # Token
    try:
        prompt = f"  Bot token [{existing['token'][:8]}...] > " if existing else "  Bot token > "
        raw_token = input(prompt).strip()
    except (EOFError, KeyboardInterrupt):
        return
    token = raw_token if raw_token else (existing["token"] if existing else "")
    if not token:
        return

    # Chat ID — try auto-fetch first
    console.print("  [dim]Fetching chat_id from recent messages...[/dim]")
    chat_id = None
    try:
        import urllib.request, json as _json
        url = f"https://api.telegram.org/bot{token}/getUpdates?timeout=0"
        with urllib.request.urlopen(url, timeout=10) as r:
            data = _json.loads(r.read())
        updates = data.get("result", [])
        if updates:
            last_msg = next(
                (u.get("message") or u.get("edited_message") or u.get("channel_post")
                 for u in reversed(updates) if u.get("message") or u.get("edited_message") or u.get("channel_post")),
                None,
            )
            if last_msg:
                chat_id = str(last_msg["chat"]["id"])
                name = last_msg["chat"].get("first_name", chat_id)
                console.print(f"  [green]Found chat:[/green] {name}  [dim](id: {chat_id})[/dim]")
        else:
            console.print("  [yellow]No messages found via getUpdates.[/yellow]")
    except Exception as e:
        console.print(f"  [yellow]Could not fetch updates: {e}[/yellow]")

    if not chat_id:
        existing_id = existing["chat_id"] if existing else ""
        prompt = f"  chat_id [{existing_id}] > " if existing_id else "  chat_id > "
        console.print("  [dim]Get your chat_id from @userinfobot on Telegram.[/dim]")
        try:
            raw_id = input(prompt).strip()
        except (EOFError, KeyboardInterrupt):
            return
        chat_id = raw_id if raw_id else existing_id
        if not chat_id:
            return

    cfg_mod.set_telegram(token, chat_id)

    # test message
    _notify_telegram("atomic connected ✓")
    console.print("  [green]Saved.[/green] Test message sent — check Telegram.\n")


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
    console.print(f"[dim]{'─' * w}[/dim]")
    try:
        line = input("  \033[36m◆\033[0m  ")
    except KeyboardInterrupt:
        print()
        raise
    return line.strip()



SYSTEM_PROMPT = """You are a local coding assistant with direct filesystem access on this machine.
Current working directory: {cwd}

TOOLS:

1. List a directory:
   <list_dir path="."/>

2. Read a file:
   <read_file path="src/app.py"/>

3. Search for a pattern across files (use BEFORE reading to locate code):
   <grep_file pattern="def process_payment" path="src/"/>
   <grep_file pattern="import requests"/>

4. Surgical edit — preferred for changing existing files:
   <edit_file path="src/app.py">
   <old>
   def foo():
       pass
   </old>
   <new>
   def foo():
       return 42
   </new>
   </edit_file>
   The <old> block must be an exact copy of the current file content (including whitespace).
   Use write_file only for NEW files or complete rewrites.

5. Write/overwrite a full file (new files or complete rewrites only):
   <write_file path="src/app.py">
   full file content here
   </write_file>

6. Read git state (use at the start to understand what's already changed):
   <git cmd="status"/>
   <git cmd="diff"/>
   <git cmd="log --oneline -10"/>

7. Run a one-time shell command (auto-executed):
   ```bash
   command here
   ```

8. Long-running server command (NOT auto-executed):
   ```bash-server
   npm run dev
   ```

9. Create a planning note:
   <create_note path=".notes/plan.md">
   content
   </create_note>

WORKFLOW:
1. Check git state with <git cmd="status"/> to see what's already modified.
2. Use <grep_file> to locate relevant code before reading full files.
3. Read specific files with <read_file>.
4. Edit existing files with <edit_file>. Create new files with <write_file>.
5. Run tests or build with ```bash```.
6. Never show corrected code in a markdown block — always use edit_file or write_file."""


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


AGENT_SYSTEM_PROMPT = """You are an autonomous coding agent with direct filesystem access.
Current working directory: {cwd}

Your job: receive a task, plan it, then implement it fully — step by step — without waiting for the user.

TOOLS:

1. <list_dir path="."/>
2. <read_file path="src/app.py"/>
3. <grep_file pattern="className" path="src/"/>   ← find code before reading full files
4. <edit_file path="src/app.py">                  ← preferred for editing existing files
   <old>exact current content</old>
   <new>replacement content</new>
   </edit_file>
5. <write_file path="new_file.py">content</write_file>   ← new files or full rewrites only
6. <git cmd="status"/>  |  <git cmd="diff"/>  |  <git cmd="log --oneline -10"/>
7. ```bash\ncommand\n```   ← one-time commands (auto-executed)
8. <done>Summary of what was accomplished.</done>

WORKFLOW:
1. Run <git cmd="status"/> to see what's already changed.
2. Use <grep_file> to locate relevant code, then <read_file> to read it.
3. Make changes with <edit_file> (existing files) or <write_file> (new files).
4. Verify with ```bash``` (tests, build, lint).
5. End with <done>summary</done>.

RULES:
- Never output corrected code in markdown — always use edit_file or write_file.
- edit_file <old> must be an exact copy from the file including whitespace.
- Each ```bash``` is an independent subprocess; chain with && when order matters.
- Make decisions and proceed — do not stop to ask questions.
"""


def _detect_test_cmd() -> str | None:
    """Return the test command for the current project, or None."""
    cwd = os.getcwd()
    if any(os.path.exists(os.path.join(cwd, f)) for f in ("pytest.ini", "setup.cfg", "pyproject.toml", "setup.py")):
        return "python3 -m pytest -x -q 2>&1 | tail -20"
    if os.path.exists(os.path.join(cwd, "package.json")):
        try:
            import json as _j
            with open(os.path.join(cwd, "package.json")) as f:
                pkg = _j.load(f)
            if "test" in pkg.get("scripts", {}):
                return "npm test"
        except Exception:
            pass
    if os.path.exists(os.path.join(cwd, "go.mod")):
        return "go test ./... 2>&1 | tail -20"
    if os.path.exists(os.path.join(cwd, "Cargo.toml")):
        return "cargo test 2>&1 | tail -20"
    return None


def _run_tests_after_agent() -> None:
    """Offer to run project tests after agent completes."""
    cmd = _detect_test_cmd()
    if not cmd:
        return
    try:
        ans = input(f"  Run tests? [Y/n] > ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return
    if ans in ("n", "no"):
        return
    console.print(f"  [dim]running tests...[/dim]")
    result = tools.run_script(cmd, "bash")
    if result["ok"]:
        console.print(f"  [green]✓ tests passed[/green]\n")
    else:
        console.print(f"  [red]✗ tests failed[/red]\n{result['output']}\n")


def _offer_git_commit(summary: str) -> None:
    """Offer to git commit changes made by the agent."""
    import subprocess
    try:
        r = subprocess.run(["git", "status", "--porcelain"], capture_output=True, text=True, cwd=os.getcwd(), timeout=5)
    except Exception:
        return
    if not r.stdout.strip():
        return
    try:
        ans = input("  Commit changes? [y/N] > ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return
    if ans not in ("y", "yes"):
        return
    msg = f"agent: {summary[:72]}"
    subprocess.run(["git", "add", "-A"], cwd=os.getcwd())
    r = subprocess.run(["git", "commit", "-m", msg], capture_output=True, text=True, cwd=os.getcwd())
    if r.returncode == 0:
        console.print(f"  [green]✓ committed:[/green] [dim]{msg}[/dim]\n")
    else:
        console.print(f"  [red]commit failed:[/red] {r.stderr.strip()}\n")


def _load_project_instructions() -> str | None:
    for name in (".atomic/instructions.md", ".atomic.md"):
        p = os.path.join(os.getcwd(), name)
        if os.path.exists(p):
            try:
                with open(p) as f:
                    return f.read().strip()
            except Exception:
                pass
    return None


def _apply_edit_confirmed(path: str, old_text: str, new_text: str) -> str:
    """Show diff, ask confirmation, then apply surgical edit. Returns result message."""
    ok, new_content, err = tools.apply_edit(path, old_text, new_text)
    if not ok:
        return err
    if _print_diff(path, new_content):
        tools.backup_file(path)
        result = tools.write_file(path, new_content)
        _session_written.append((path, "modified"))
        return result
    return f"User rejected edit to {path}."


def _print_banner(model_name: str, tps: float, model_gb: float, avail_gb: float) -> None:
    speed_color = "green" if tps > 25 else "yellow" if tps > 10 else "red"
    speed_str = f"[{speed_color}]{tps:.0f} tok/s[/{speed_color}]" if tps > 0 else "[dim]—[/dim]"

    ram_str = ""
    if model_gb > 0 and avail_gb > 0:
        warn = model_gb > avail_gb * 0.9
        ram_color = "red" if warn else "dim"
        ram_str = f"  [{ram_color}]{model_gb:.1f} GB model / {avail_gb:.1f} GB free[/{ram_color}]"
    elif model_gb > 0:
        ram_str = f"  [dim]{model_gb:.1f} GB[/dim]"

    t = Table.grid(padding=(0, 2))
    t.add_column(style="dim", width=7)
    t.add_column()
    t.add_row("model", f"[cyan]{model_name}[/cyan]")
    t.add_row("speed", speed_str + ram_str)
    t.add_row("dir", f"[dim]{os.getcwd()}[/dim]")

    console.print(Panel(
        t,
        title="[bold cyan]◆ atomic[/bold cyan]",
        subtitle="[dim]/agent  /think  /read  /undo  /model  /help[/dim]",
        border_style="cyan",
        padding=(1, 2),
    ))


def make_history() -> list[dict]:
    dir_listing = tools.list_dir(".")
    system = SYSTEM_PROMPT.format(cwd=os.getcwd())
    instructions = _load_project_instructions()
    if instructions:
        system += f"\n\n---\nPROJECT INSTRUCTIONS:\n{instructions}"
    return [
        {"role": "system", "content": system},
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
    global _write_session_allowed

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
        console.print(Panel(_make_diff_text(diff_lines, lang), title=title, border_style="dim"))

    if _write_session_allowed:
        console.print(f"  [dim]⚙ writing {path} (allowed this session)[/dim]")
        return True

    try:
        ans = input("  Allow write? [y]es / [n]o / [a]lways this session > ").strip().lower()
    except (EOFError, KeyboardInterrupt):
        return False

    if ans in ("a", "always"):
        _write_session_allowed = True
        return True
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

        for pattern, path in TOOL_GREP.findall(response):
            search_path = path or "."
            console.print(f"  [dim]⚙ grep '{pattern}' in {search_path}[/dim]")
            tool_results.append(tools.grep_files(pattern, search_path))

        for cmd in TOOL_GIT.findall(response):
            console.print(f"  [dim]⚙ git {cmd}[/dim]")
            tool_results.append(f"git {cmd}:\n{tools.git_run(cmd)}")

        for path, old_text, new_text in TOOL_EDIT.findall(response):
            console.print(f"  [dim]⚙ editing {path}[/dim]")
            result_msg = _apply_edit_confirmed(path, old_text.strip("\n"), new_text.strip("\n"))
            tool_results.append(result_msg)

        for path, content in TOOL_WRITE.findall(response):
            stripped = content.lstrip("\n")
            real = os.path.join(os.getcwd(), path)
            is_new = not os.path.exists(real)
            console.print(f"  [dim]⚙ writing {path}[/dim]")
            if _print_diff(path, stripped):
                tools.backup_file(path)
                result_msg = tools.write_file(path, stripped)
                _session_written.append((path, "created" if is_new else "modified"))
            else:
                result_msg = f"User rejected changes to {path}. Do not attempt to rewrite this file unless asked."
            tool_results.append(result_msg)

        for path, content in CREATE_NOTE.findall(response):
            safe = os.path.normpath(path)
            if safe.startswith("..") or (not safe.startswith(".notes") and not safe.startswith("notes")):
                tool_results.append(f"[error] create_note path must be under .notes/ — got: {path}")
                continue
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
    for tag in ("write_file", "edit_file", "create_note", "read_file", "list_dir"):
        opens = text.count(f"<{tag}")
        closes = text.count(f"</{tag}>") + text.count(f"/{tag}>")
        if opens > closes:
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
    _notify_telegram(f"atomic /think done ✓\n{prompt[:80]}")


def _run_agent(task: str) -> None:
    import time as _time

    model_name = llm.get_model_name()
    console.print(f"\n  [dim]◉ agent — {model_name}[/dim]\n")

    history = [
        {"role": "system", "content": AGENT_SYSTEM_PROMPT.format(cwd=os.getcwd())},
        {"role": "user", "content": f"Directory:\n{tools.list_dir('.')}\n\nTask: {task}"},
    ]

    step = 0
    while True:
        step += 1
        if step > AGENT_MAX_STEPS:
            console.print(f"\n  [yellow]⚠ reached {AGENT_MAX_STEPS}-step limit — start a new /agent task to continue[/yellow]\n")
            break
        remaining = AGENT_MAX_STEPS - step
        step_color = "green" if remaining > 10 else "yellow" if remaining > 5 else "red"
        console.print(f"\n[dim]  ── step [/dim][bold]{step}[/bold][dim]/{AGENT_MAX_STEPS}  [/dim][{step_color}][dim]{remaining} left[/dim][/{step_color}]")

        reply, result = _stream_chat(history)
        if reply is None:
            break

        # Strip think block
        think_match = THINK_BLOCK.search(reply)
        if think_match:
            reply = THINK_BLOCK.sub("", reply).strip()
            think_tokens = len(think_match.group(1).split())
            console.print(f"\n[dim]  ◦ thought for ~{think_tokens} words[/dim]\n")

        # Display reply with tool tags removed so it reads naturally
        display = _AGENT_STRIP.sub("", reply).strip()
        if display:
            console.print(Markdown(display))

        # Check for completion signal first
        done_match = AGENT_DONE.search(reply)
        if done_match:
            summary = done_match.group(1).strip()
            console.print(f"\n[green]  ◉ done:[/green] {summary}\n")
            _notify_telegram(f"atomic agent done ✓\n{summary}")
            _run_tests_after_agent()
            _offer_git_commit(summary)
            break

        tool_results: list[str] = []

        for path in TOOL_LIST.findall(reply):
            console.print(f"  [dim]⚙ listing {path}[/dim]")
            tool_results.append(f"Directory listing of {path}:\n{tools.list_dir(path)}")

        for path in TOOL_READ.findall(reply):
            console.print(f"  [dim]⚙ reading {path}[/dim]")
            content = tools.read_file(path)
            tool_results.append(
                f"Contents of {path}:\n```\n{content}\n```" if content else f"Could not read {path}."
            )

        for pattern, path in TOOL_GREP.findall(reply):
            search_path = path or "."
            console.print(f"  [dim]⚙ grep '{pattern}' in {search_path}[/dim]")
            tool_results.append(tools.grep_files(pattern, search_path))

        for cmd in TOOL_GIT.findall(reply):
            console.print(f"  [dim]⚙ git {cmd}[/dim]")
            tool_results.append(f"git {cmd}:\n{tools.git_run(cmd)}")

        for path, old_text, new_text in TOOL_EDIT.findall(reply):
            console.print(f"  [dim]⚙ editing {path}[/dim]")
            tool_results.append(_apply_edit_confirmed(path, old_text.strip("\n"), new_text.strip("\n")))

        for path, content in TOOL_WRITE.findall(reply):
            stripped = content.lstrip("\n")
            real = os.path.join(os.getcwd(), path)
            is_new = not os.path.exists(real)
            if _print_diff(path, stripped):
                tools.backup_file(path)
                result_msg = tools.write_file(path, stripped)
                _session_written.append((path, "created" if is_new else "modified"))
            else:
                result_msg = f"User rejected changes to {path}. Do not rewrite unless asked."
            tool_results.append(result_msg)

        for match in CODE_BLOCK.finditer(reply):
            raw_lang = match.group(1)
            code = match.group(2).strip()

            if raw_lang == "bash-server":
                _print_script("bash", code)
                tools.run_server_background(code, on_line=_server_print)
                tool_results.append("Background server started.")
                continue

            lang = LANG_NORM.get(raw_lang, raw_lang)
            _print_script(lang, code)

            output_lines: list[str] = []
            start = _time.monotonic()
            try:
                with Live("  [dim]running...[/dim]", console=console, refresh_per_second=15, transient=False) as live:
                    def _on_line(line: str, _buf=output_lines, _t=start, _live=live):
                        _buf.append(line)
                        body = "\n".join(f"  [dim]│[/dim] {l}" for l in _buf[-20:])
                        _live.update(f"  [dim]{_time.monotonic() - _t:.1f}s[/dim]\n{body}")
                    script_result = tools.run_script(code, lang, on_line=_on_line)
            except KeyboardInterrupt:
                console.print("\n  [dim]interrupted[/dim]\n")
                return

            elapsed = _time.monotonic() - start
            if script_result["ok"]:
                console.print(f"  [green]✓[/green]  [dim]{elapsed:.1f}s[/dim]")
                tool_results.append(f"Script output:\n```\n{script_result['output']}\n```")
            else:
                console.print(f"  [red]✗ exit {script_result['returncode']}[/red]  [dim]{elapsed:.1f}s[/dim]")
                tool_results.append(
                    f"Script failed (exit {script_result['returncode']}):\n```\n{script_result['output']}\n```"
                )

        if not tool_results:
            console.print("\n  [dim]◉ agent finished.[/dim]\n")
            _notify_telegram("atomic agent finished.")
            break

        history.append({"role": "assistant", "content": reply})
        history.append({"role": "user", "content": "\n\n".join(tool_results)})

    _print_session_summary()


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

    has_tools = bool(
        TOOL_WRITE.search(reply) or TOOL_READ.search(reply)
        or TOOL_LIST.search(reply) or CREATE_NOTE.search(reply)
        or TOOL_EDIT.search(reply) or TOOL_GREP.search(reply)
        or TOOL_GIT.search(reply)
    )

    # Strip think block first (applies to both paths)
    think_match = THINK_BLOCK.search(reply)
    if think_match:
        thinking = think_match.group(1).strip()
        reply = THINK_BLOCK.sub("", reply).strip()
        think_tokens = len(thinking.split())
        console.print(f"\n[dim]  ◦ thought for ~{think_tokens} words[/dim]\n")

    if has_tools:
        # Show model's suggestion text BEFORE applying any tools so the user
        # can read the explanation before being asked to confirm writes.
        pre_text = TOOL_WRITE.sub("", TOOL_READ.sub("", TOOL_LIST.sub(
            "", CREATE_NOTE.sub("", reply)))).strip()
        if pre_text:
            console.print(Markdown(pre_text))

    reply, tool_stats = process_tool_calls(reply, history)
    if tool_stats:
        result = tool_stats

    elapsed = result.get("elapsed", 0)
    comp = result.get("completion_tokens", 0)
    tps = comp / elapsed if elapsed > 0 else 0

    # After tool calls the model may return a follow-up; strip think again.
    think_match2 = THINK_BLOCK.search(reply)
    if think_match2:
        reply = THINK_BLOCK.sub("", reply).strip()

    # If we already printed the pre-text above and there's nothing new to say,
    # avoid reprinting. process_tool_calls returns the follow-up reply from the
    # model (after it sees tool results), so print it unconditionally.
    console.print(Markdown(reply))
    ctx_used, ctx_max = llm.estimate_context_usage(history)
    ctx_info = f"  ctx {ctx_used:,}/{ctx_max:,}" if ctx_max else ""
    console.print(f"\n[dim]  ⏱ {elapsed:.1f}s · {comp} tok · {tps:.0f} tok/s{ctx_info}[/dim]")
    if ctx_max and ctx_used / ctx_max > 0.80:
        console.print(f"  [yellow]⚠ context {ctx_used / ctx_max:.0%} full — /clear to reset[/yellow]")
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


def _save_history() -> None:
    try:
        os.makedirs(os.path.dirname(HISTORY_FILE), exist_ok=True)
        readline.write_history_file(HISTORY_FILE)
    except Exception:
        pass


def run():
    global _script_session_allowed
    from atomic import config as cfg_mod

    try:
        readline.read_history_file(HISTORY_FILE)
        readline.set_history_length(500)
    except FileNotFoundError:
        pass

    model_path = model_picker.pick_model()

    model_gb, avail_gb = llm.check_ram(model_path)
    if avail_gb > 0 and model_gb > avail_gb * 0.9:
        console.print(f"\n  [yellow]⚠ model is {model_gb:.1f} GB but only {avail_gb:.1f} GB RAM free — may be slow[/yellow]")

    console.print(f"\n  [dim]Loading model...[/dim]", end="\r")
    llm.load_model(model_path)

    think_path = cfg_mod.get_thinking_model()
    if think_path and os.path.exists(think_path):
        llm.load_think_model(think_path)

    console.print(f"  [dim]Benchmarking...      [/dim]", end="\r")
    tps = llm.benchmark()

    model_name = os.path.basename(model_path)
    console.print(" " * 30 + "\r", end="")
    _print_banner(model_name, tps, model_gb, avail_gb)

    history = make_history()

    while True:
        _flush_server_output()
        try:
            user_input = get_input()
        except EOFError:
            llm.stop()
            _print_session_summary()
            _save_history()
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
            _save_history()
            console.print("[dim]bye.[/dim]")
            break

        if user_input == "/server":
            if tools.stop_server_background():
                console.print("  [dim]server stopped[/dim]\n")
            else:
                console.print("  [dim]no server running[/dim]\n")
            continue

        if user_input == "/undo":
            result = tools.undo_last_write()
            if result:
                original, _ = result
                console.print(f"  [green]↩ restored[/green] [cyan]{original}[/cyan]\n")
            else:
                console.print("  [dim]nothing to undo[/dim]\n")
            continue

        if user_input == "/clear":
            _print_session_summary()
            _session_written.clear()
            console.clear()
            _print_banner(model_name, 0.0, 0.0, 0.0)
            history = make_history()
            _script_session_allowed = None
            _write_session_allowed = None
            continue

        if user_input == "/help":
            t = Table(show_header=False, box=None, padding=(0, 3))
            t.add_column(style="cyan", no_wrap=True)
            t.add_column(style="dim")
            for cmd, desc in [
                ("/agent <task>",   "autonomous agent — plans and implements end-to-end"),
                ("/think <prompt>", "deep investigation → saves a work note to .notes/"),
                ("/read <path>",    "load a file or directory into context"),
                ("/undo",           "restore the last file changed by the AI"),
                ("/telegram",       "configure Telegram notifications"),
                ("/model",          "switch main model mid-session"),
                ("/think-model",    "set the thinking model"),
                ("/server",         "stop the background dev server"),
                ("/save [path]",    "save conversation to a markdown file"),
                ("/clear",          "reset conversation history"),
                ("/exit",           "quit"),
            ]:
                t.add_row(cmd, desc)
            console.print(Panel(t, title="[bold]commands[/bold]", border_style="dim", padding=(1, 2)))
            continue

        if user_input.startswith("/save"):
            parts = user_input.split(None, 1)
            save_path = parts[1].strip() if len(parts) > 1 else f"atomic-{int(time.time())}.md"
            _save_conversation(history, save_path)
            continue

        if user_input == "/telegram":
            _setup_telegram()
            continue

        if user_input.startswith("/agent "):
            task = user_input[7:].strip()
            if task:
                _run_agent(task)
            else:
                console.print("  [dim]Usage: /agent <task description>[/dim]\n")
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
            if os.path.isdir(path):
                files = tools.read_dir(path)
                if not files:
                    console.print(f"  [dim]no readable files found in {path}[/dim]\n")
                    continue
                console.print(f"  [dim]loading {len(files)} files from {path}[/dim]")
                parts = [f"File: {p}\n```\n{c}\n```" for p, c in files]
                history.append({"role": "user", "content": f"Please review these files from {path}:\n\n" + "\n\n".join(parts)})
            else:
                content = tools.read_file(path)
                if content is None:
                    continue
                history.append({"role": "user", "content": f"Please review this file ({path}):\n```\n{content}\n```"})
        elif any(kw in user_input.lower() for kw in ("project", "directory", "code", "review", "check", "bug", "fix", "error", "issue")):
            listing = tools.list_dir(".")
            console.print("  [dim]↳ injecting directory context[/dim]")
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





def _run_agent_serve(task: str, token: str, chat_id: str) -> None:
    """Agent loop for serve mode — runs on a background thread, no Live display."""

    def send(text: str):
        _tg_send(token, chat_id, text)

    def chat_simple(history: list[dict]) -> str | None:
        try:
            console.print(f"[dim]  model thinking...[/dim]")
            result = llm.chat(history)
            console.print(f"[dim]  model done ({len(result['content'])} chars)[/dim]")
            return result["content"]
        except Exception as e:
            console.print(f"[red]  model error: {e}[/red]")
            send(f"Model error: {e}")
            return None

    send(f"▶ {task[:200]}")

    history = [
        {"role": "system", "content": AGENT_SYSTEM_PROMPT.format(cwd=os.getcwd())},
        {"role": "user", "content": f"Directory:\n{tools.list_dir('.')}\n\nTask: {task}"},
    ]

    step = 0
    while True:
        step += 1
        if step > AGENT_MAX_STEPS:
            send(f"⚠ Reached {AGENT_MAX_STEPS}-step limit. Send a new /agent task to continue.")
            break

        reply = chat_simple(history)
        if reply is None:
            send("Agent interrupted.")
            break

        think_match = THINK_BLOCK.search(reply)
        if think_match:
            reply = THINK_BLOCK.sub("", reply).strip()

        done_match = AGENT_DONE.search(reply)
        display = _AGENT_STRIP.sub("", reply).strip()

        if display:
            send(f"Step {step}: {display[:500]}")

        if done_match:
            send(f"✓ Done\n{done_match.group(1).strip()}")
            break

        tool_results: list[str] = []

        for path in TOOL_LIST.findall(reply):
            tool_results.append(f"Directory listing of {path}:\n{tools.list_dir(path)}")

        for path in TOOL_READ.findall(reply):
            content = tools.read_file(path)
            tool_results.append(
                f"Contents of {path}:\n```\n{content}\n```" if content else f"Could not read {path}."
            )

        for pattern, path in TOOL_GREP.findall(reply):
            tool_results.append(tools.grep_files(pattern, path or "."))

        for cmd in TOOL_GIT.findall(reply):
            tool_results.append(f"git {cmd}:\n{tools.git_run(cmd)}")

        for path, old_text, new_text in TOOL_EDIT.findall(reply):
            ok, new_content, err = tools.apply_edit(path, old_text.strip("\n"), new_text.strip("\n"))
            if ok:
                tools.backup_file(path)
                result_msg = tools.write_file(path, new_content)
                _session_written.append((path, "modified"))
                send(f"Edited: {path}")
            else:
                result_msg = err
            tool_results.append(result_msg)

        for path, content in TOOL_WRITE.findall(reply):
            stripped = content.lstrip("\n")
            real = os.path.join(os.getcwd(), path)
            is_new = not os.path.exists(real)
            if is_new:
                diff_info = f" ({len(stripped.splitlines())} lines)"
            else:
                try:
                    with open(real, "r", errors="replace") as _fh:
                        _old = _fh.read().splitlines(keepends=True)
                    _new = stripped.splitlines(keepends=True)
                    _diff = list(difflib.unified_diff(_old, _new))
                    _added = sum(1 for l in _diff if l.startswith("+") and not l.startswith("+++"))
                    _removed = sum(1 for l in _diff if l.startswith("-") and not l.startswith("---"))
                    diff_info = f" (+{_added} -{_removed} lines)"
                except Exception:
                    diff_info = ""
            send(f"{'Creating' if is_new else 'Modifying'}: {path}{diff_info}")
            tools.backup_file(path)
            result_msg = tools.write_file(path, stripped)
            _session_written.append((path, "created" if is_new else "modified"))
            tool_results.append(result_msg)

        for match in CODE_BLOCK.finditer(reply):
            raw_lang = match.group(1)
            code = match.group(2).strip()

            if raw_lang == "bash-server":
                tools.run_server_background(code, on_line=_server_print)
                tool_results.append("Background server started.")
                continue

            lang = LANG_NORM.get(raw_lang, raw_lang)
            out_lines: list[str] = []
            try:
                script_result = tools.run_script(code, lang, on_line=lambda l: out_lines.append(l))
            except Exception as e:
                tool_results.append(f"Error: {e}")
                continue

            out = script_result["output"]
            if script_result["ok"]:
                tool_results.append(f"Script output:\n```\n{out[-1000:]}\n```")
            else:
                send(f"✗ Script failed (exit {script_result['returncode']})\n{out[-300:]}")
                tool_results.append(f"Script failed (exit {script_result['returncode']}):\n```\n{out}\n```")

        if not tool_results:
            send("Agent finished.")
            break

        history.append({"role": "assistant", "content": reply})
        history.append({"role": "user", "content": "\n\n".join(tool_results)})


def _run_chat_serve(text: str, token: str, chat_id: str, history: list[dict]) -> None:
    """Handle a regular chat message in serve mode, maintaining conversation history."""

    def send(msg: str):
        _tg_send(token, chat_id, msg)

    if any(kw in text.lower() for kw in ("project", "directory", "code", "review", "check", "bug", "fix", "error", "issue")):
        listing = tools.list_dir(".")
        history.append({"role": "user", "content": f"{text}\n\n[Current directory files:\n{listing}]"})
    else:
        history.append({"role": "user", "content": text})

    try:
        result = llm.chat(history)
        reply = result["content"]
    except Exception as e:
        send(f"Error: {e}")
        return

    # process tool calls (read/list/write)
    while True:
        tool_results = []
        for path in TOOL_LIST.findall(reply):
            tool_results.append(f"Directory listing of {path}:\n{tools.list_dir(path)}")
        for path in TOOL_READ.findall(reply):
            content = tools.read_file(path)
            tool_results.append(f"Contents of {path}:\n```\n{content}\n```" if content else f"Could not read {path}.")
        for path, content in TOOL_WRITE.findall(reply):
            tools.backup_file(path)
            result_msg = tools.write_file(path, content.lstrip("\n"))
            send(f"Modified: {path}")
            tool_results.append(result_msg)
        if not tool_results:
            break
        history.append({"role": "assistant", "content": reply})
        history.append({"role": "user", "content": "\n\n".join(tool_results)})
        try:
            result = llm.chat(history)
            reply = result["content"]
        except Exception as e:
            send(f"Error: {e}")
            return

    think_match = THINK_BLOCK.search(reply)
    if think_match:
        reply = THINK_BLOCK.sub("", reply).strip()

    display = _AGENT_STRIP.sub("", reply).strip()
    if display:
        send(display[:4096])
    history.append({"role": "assistant", "content": reply})


def _serve() -> None:
    from atomic import config as cfg_mod

    tg = cfg_mod.get_telegram()
    if not tg:
        console.print("\n  [yellow]Telegram not configured yet — let's set it up now.[/yellow]\n")
        _setup_telegram()
        tg = cfg_mod.get_telegram()
        if not tg:
            console.print("[red]Setup cancelled. Run `atomic serve` again when ready.[/red]")
            sys.exit(1)

    token = tg["token"]
    chat_id = tg["chat_id"]
    cwd = os.getcwd()
    _serve_start = time.time()

    model_path = model_picker.pick_model()
    console.print("  [dim]Loading model...[/dim]", end="\r")
    llm.load_model(model_path)
    model_name = os.path.basename(model_path)
    console.print(Rule(f"[cyan]atomic serve[/cyan]  [dim]{model_name}[/dim]"))
    console.print(f"  [dim]dir: {cwd}[/dim]")
    console.print(f"  [dim]Listening on Telegram... Ctrl+C to stop.[/dim]\n")

    _tg_send(token, chat_id, (
        f"atomic online ✓\nModel: {model_name}\nDir: {cwd}\n\n"
        "Send me a question or use /agent <task> for autonomous coding."
    ))

    # Use a queue so model inference always runs on the main thread (llama-cpp is not thread-safe)
    task_queue: queue.Queue = queue.Queue()
    chat_history: list[dict] = make_history()

    def poll_loop():
        offset = 0
        while True:
            try:
                updates = _tg_get_updates(token, offset, timeout=20)
            except Exception as e:
                console.print(f"[red]  poll error: {e}[/red]")
                time.sleep(3)
                continue
            for update in updates:
                offset = update["update_id"] + 1
                msg = update.get("message")
                if not msg:
                    continue
                incoming_id = str(msg.get("chat", {}).get("id", ""))
                console.print(f"[dim]  poll: msg from {incoming_id} (expected {chat_id})[/dim]")
                if incoming_id != str(chat_id):
                    continue
                text = msg.get("text", "").strip()
                if text:
                    console.print(f"[dim]  queued: {text[:60]}[/dim]")
                    task_queue.put(text)

    poll_thread = threading.Thread(target=poll_loop, daemon=True)
    poll_thread.start()

    try:
        while True:
            try:
                text = task_queue.get(timeout=1)
            except queue.Empty:
                continue
            console.print(f"[dim]  ← {text[:80]}[/dim]")
            try:
                if text.startswith("/agent "):
                    task = text[7:].strip()
                    if task:
                        _run_agent_serve(task, token, chat_id)
                    else:
                        _tg_send(token, chat_id, "Usage: /agent <task description>")
                elif text == "/undo":
                    result = tools.undo_last_write()
                    if result:
                        original, _ = result
                        _tg_send(token, chat_id, f"↩ Restored: {original}")
                    else:
                        _tg_send(token, chat_id, "Nothing to undo.")
                elif text == "/clear":
                    chat_history.clear()
                    chat_history.extend(make_history())
                    _tg_send(token, chat_id, "Conversation cleared.")
                elif text == "/status":
                    uptime_s = int(time.time() - _serve_start)
                    h, m = divmod(uptime_s // 60, 60)
                    uptime_str = f"{h}h {m}m" if h else f"{m}m"
                    _tg_send(token, chat_id, (
                        f"✓ online\n"
                        f"Model: {llm.get_model_name()}\n"
                        f"Dir: {cwd}\n"
                        f"Uptime: {uptime_str}"
                    ))
                elif text == "/help":
                    _tg_send(token, chat_id, (
                        "Commands:\n"
                        "/agent <task> — autonomous coding agent\n"
                        "/undo — restore last file changed by agent\n"
                        "/status — show model, dir, uptime\n"
                        "/clear — reset conversation\n"
                        "/help — show this help\n\n"
                        "Or just ask me anything."
                    ))
                else:
                    _run_chat_serve(text, token, chat_id, chat_history)
            except Exception as e:
                _tg_send(token, chat_id, f"Error: {e}")
                console.print(f"[red]  task error: {e}[/red]")
    except KeyboardInterrupt:
        _tg_send(token, chat_id, "atomic offline.")
        console.print("\n[dim]bye.[/dim]")
        llm.stop()


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
    console.print("    atomic serve                  Listen on Telegram 24/7 (long-poll)")
    console.print("    atomic model                  Re-select default model")
    console.print("    atomic model list             List available models")
    console.print("    atomic model add <path>       Register a local .gguf file")
    console.print("    atomic model download [repo]  Download from HuggingFace\n")
    console.print("  [dim]Inside chat: /agent  /think  /telegram  /model  /clear  /read  /exit[/dim]\n")


def main():
    args = sys.argv[1:]

    if not args:
        run()
        return

    if args[0] in ("-h", "--help", "help"):
        _print_help()
        return

    if args[0] == "serve":
        _serve()
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
