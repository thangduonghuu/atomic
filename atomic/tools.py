import fnmatch
import json
import os
import shutil
import signal
import subprocess
import threading
from rich.console import Console
from atomic import permissions

console = Console()

_MAX_FILE_BYTES = 512 * 1024  # 512 KB

BACKUP_DIR     = os.path.expanduser("~/.atomic/backups")
UNDO_MANIFEST  = os.path.expanduser("~/.atomic/undo.json")

DEFAULT_IGNORE = [
    "node_modules", "venv", ".git", "__pycache__",
    "*.egg-info", ".DS_Store", "*.pyc", "dist", "build",
    ".next", ".nuxt", ".env", "*.lock", "*.part",
]


def _load_ignore_patterns() -> list[str]:
    path = os.path.join(os.getcwd(), ".atomicignore")
    if os.path.exists(path):
        with open(path) as f:
            lines = [l.strip() for l in f if l.strip() and not l.startswith("#")]
        if lines:
            return lines
    return DEFAULT_IGNORE


def _should_ignore(name: str, patterns: list[str]) -> bool:
    return any(fnmatch.fnmatch(name, p) for p in patterns)


def _fmt_size(n: int) -> str:
    if n < 1024:
        return f"{n}B"
    if n < 1024 ** 2:
        return f"{n / 1024:.1f}K"
    return f"{n / 1024 ** 2:.1f}M"

# After producing some output, if the process goes silent for this long while
# still running, it is almost certainly a server — kill it and surface as such.
_SERVER_DETECT_TIMEOUT = 5
# If no output at all for this long, the process is stuck.
_STUCK_TIMEOUT = 60


def run_script(code: str, lang: str = "bash", on_line=None) -> dict:
    """Returns {"output": str, "ok": bool, "returncode": int, "is_server": bool}.
    Inactivity-based timeouts:
      - produced output then went silent → server detected (10s)
      - never produced output and went silent → stuck (60s)
    """
    if lang in ("bash", "sh", "shell"):
        cmd = ["bash", "-c", code]
    elif lang in ("python", "py", "python3"):
        cmd = ["python3", "-c", code]
    else:
        return {"output": f"[unsupported language: {lang}]", "ok": False, "returncode": -1, "is_server": False}
    try:
        if on_line is not None:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                text=True, cwd=os.getcwd(),
                start_new_session=True,
            )
            lines = []
            timed_out = threading.Event()

            def _kill_group():
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except ProcessLookupError:
                    proc.kill()

            def _make_watchdog(has_output: bool):
                timeout = _SERVER_DETECT_TIMEOUT if has_output else _STUCK_TIMEOUT
                def _kill():
                    timed_out.set()
                    _kill_group()
                return threading.Timer(timeout, _kill)

            watchdog = _make_watchdog(has_output=False)
            watchdog.start()
            try:
                for line in proc.stdout:
                    watchdog.cancel()
                    lines.append(line)
                    on_line(line.rstrip())
                    watchdog = _make_watchdog(has_output=True)
                    watchdog.start()
            finally:
                watchdog.cancel()
            proc.wait()

            output = "".join(lines).strip() or "(no output)"
            if timed_out.is_set():
                is_server = bool(lines)
                msg = (
                    "[server] This looks like a long-running server — run it in a separate terminal."
                    if is_server else
                    f"[error] no output for {_STUCK_TIMEOUT}s — process killed (stuck?)"
                )
                return {"output": f"{msg}\n{output}", "ok": False, "returncode": -1, "is_server": is_server}
            return {"output": output, "ok": proc.returncode == 0, "returncode": proc.returncode, "is_server": False}
        else:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                stdin=subprocess.DEVNULL,
                text=True, cwd=os.getcwd(),
                start_new_session=True,
            )
            def _kill_group_sync():
                try:
                    os.killpg(os.getpgid(proc.pid), signal.SIGKILL)
                except ProcessLookupError:
                    proc.kill()
            try:
                stdout, _ = proc.communicate(timeout=_STUCK_TIMEOUT)
            except subprocess.TimeoutExpired:
                _kill_group_sync()
                stdout, _ = proc.communicate()
                return {"output": f"[error] timed out — process killed\n{stdout.strip()}", "ok": False, "returncode": -1, "is_server": False}
            output = stdout.strip() or "(no output)"
            return {"output": output, "ok": proc.returncode == 0, "returncode": proc.returncode, "is_server": False}
    except Exception as e:
        return {"output": f"[error] {e}", "ok": False, "returncode": -1, "is_server": False}

_bg_proc: subprocess.Popen | None = None
_bg_thread: threading.Thread | None = None


def run_server_background(code: str, on_line=None) -> None:
    global _bg_proc, _bg_thread
    stop_server_background()
    cmd = ["bash", "-c", code]
    _bg_proc = subprocess.Popen(
        cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
        stdin=subprocess.DEVNULL,
        text=True, cwd=os.getcwd(),
        start_new_session=True,
    )
    def _stream():
        for line in _bg_proc.stdout:
            if on_line:
                on_line(line.rstrip())
    _bg_thread = threading.Thread(target=_stream, daemon=True)
    _bg_thread.start()


def stop_server_background() -> bool:
    global _bg_proc, _bg_thread
    if _bg_proc is None:
        return False
    try:
        os.killpg(os.getpgid(_bg_proc.pid), signal.SIGKILL)
    except (ProcessLookupError, OSError):
        _bg_proc.kill()
    _bg_proc = None
    _bg_thread = None
    return True


def is_server_running() -> bool:
    return _bg_proc is not None and _bg_proc.poll() is None


def backup_file(path: str) -> None:
    """Back up a file before writing so /undo can restore it."""
    real = permissions.resolve(path)
    if not os.path.exists(real):
        return
    os.makedirs(BACKUP_DIR, exist_ok=True)
    import time
    stamp = int(time.time() * 1000)
    backup_name = f"{stamp}_{os.path.basename(real)}"
    backup_path = os.path.join(BACKUP_DIR, backup_name)
    shutil.copy2(real, backup_path)

    stack: list[dict] = []
    if os.path.exists(UNDO_MANIFEST):
        try:
            with open(UNDO_MANIFEST) as f:
                stack = json.load(f)
        except Exception:
            pass
    stack.append({"original": real, "backup": backup_path})
    stack = stack[-20:]
    os.makedirs(os.path.dirname(UNDO_MANIFEST), exist_ok=True)
    with open(UNDO_MANIFEST, "w") as f:
        json.dump(stack, f)


def undo_last_write() -> tuple[str, str] | None:
    """Restore the last backed-up file. Returns (original_path, backup_path) or None."""
    if not os.path.exists(UNDO_MANIFEST):
        return None
    try:
        with open(UNDO_MANIFEST) as f:
            stack = json.load(f)
    except Exception:
        return None
    if not stack:
        return None
    entry = stack.pop()
    with open(UNDO_MANIFEST, "w") as f:
        json.dump(stack, f)
    if os.path.exists(entry["backup"]):
        shutil.copy2(entry["backup"], entry["original"])
        return entry["original"], entry["backup"]
    return None


def write_file(path: str, content: str) -> str:
    real = permissions.resolve(path)
    os.makedirs(os.path.dirname(real) or ".", exist_ok=True)
    with open(real, "w", encoding="utf-8") as f:
        f.write(content)
    return f"Written {len(content)} bytes to {real}"


def _is_binary(path: str) -> bool:
    """Return True if file appears to be binary (non-text)."""
    try:
        with open(path, "rb") as f:
            chunk = f.read(512)
        if b"\x00" in chunk:
            return True
        chunk.decode("utf-8")
        return False
    except (UnicodeDecodeError, OSError):
        return True


def read_file(path: str) -> str | None:
    real = permissions.resolve(path)

    if not os.path.exists(real):
        console.print(f"  [red][error][/red] File not found: {real}")
        return None

    if _is_binary(real):
        console.print(f"  [yellow]⚠ {path} is a binary file — skipping[/yellow]")
        return None

    size = os.path.getsize(real)
    if size > _MAX_FILE_BYTES:
        console.print(f"  [yellow]⚠ {path} is {_fmt_size(size)} — too large to read into context[/yellow]")
        return None

    if not permissions.is_allowed(real):
        if not permissions.ask(real):
            console.print("  [dim][denied] File access denied.[/dim]")
            return None

    with open(real, "r", errors="replace") as f:
        return f.read()


def read_dir(path: str, max_files: int = 15) -> list[tuple[str, str]]:
    """Read all readable text files in a directory tree. Returns (rel_path, content) pairs."""
    real = permissions.resolve(path)
    if not os.path.isdir(real):
        return []
    ignore = _load_ignore_patterns()
    results: list[tuple[str, str]] = []
    for root, dirs, files in os.walk(real):
        dirs[:] = sorted(d for d in dirs if not _should_ignore(d, ignore))
        for fname in sorted(files):
            if _should_ignore(fname, ignore):
                continue
            if len(results) >= max_files:
                return results
            fpath = os.path.join(root, fname)
            try:
                size = os.path.getsize(fpath)
            except OSError:
                continue
            if size > _MAX_FILE_BYTES:
                continue
            rel = os.path.relpath(fpath, os.getcwd())
            try:
                with open(fpath, "r", errors="replace") as f:
                    results.append((rel, f.read()))
            except Exception:
                pass
    return results


def grep_files(pattern: str, path: str = ".", max_results: int = 60) -> str:
    """Search for a regex/string pattern in files. Returns file:line matches."""
    import subprocess
    ignore = _load_ignore_patterns()
    excludes: list[str] = []
    for p in ignore:
        if "*" not in p:
            excludes += ["--exclude-dir", p]
    cmd = ["grep", "-rn", "--include=*"] + excludes + [pattern, path]
    try:
        result = subprocess.run(cmd, capture_output=True, text=True, cwd=os.getcwd(), timeout=10)
        output = result.stdout.strip()
        if not output:
            return f"No matches for '{pattern}' in {path}"
        lines = output.splitlines()
        if len(lines) > max_results:
            return "\n".join(lines[:max_results]) + f"\n... ({len(lines) - max_results} more matches)"
        return output
    except subprocess.TimeoutExpired:
        return "[error] grep timed out"
    except FileNotFoundError:
        return "[error] grep not found"
    except Exception as e:
        return f"[error] {e}"


def apply_edit(path: str, old_text: str, new_text: str) -> tuple[bool, str, str]:
    """Replace old_text with new_text in file.
    Returns (ok, new_full_content, error_message).
    Does NOT write — caller must confirm and call write_file."""
    real = permissions.resolve(path)
    if not os.path.exists(real):
        return False, "", f"[error] File not found: {path}"
    with open(real, "r", errors="replace") as f:
        content = f.read()
    if old_text not in content:
        return False, "", (
            f"[error] Text not found in {path}. "
            "Copy the exact text from the file including whitespace."
        )
    count = content.count(old_text)
    if count > 1:
        return False, "", (
            f"[error] Text appears {count} times in {path}. "
            "Add more surrounding lines to make it unique."
        )
    return True, content.replace(old_text, new_text, 1), ""


def git_run(args: str) -> str:
    """Run a safe read-only git command. Allowed: status, diff, log, show, branch."""
    import subprocess
    ALLOWED = {"status", "diff", "log", "show", "branch"}
    parts = args.strip().split()
    if not parts or parts[0] not in ALLOWED:
        return f"[error] Only read-only git commands allowed: {', '.join(sorted(ALLOWED))}"
    try:
        result = subprocess.run(
            ["git"] + parts,
            capture_output=True, text=True, cwd=os.getcwd(), timeout=10,
        )
        output = (result.stdout + result.stderr).strip()
        if len(output) > 3000:
            output = output[:3000] + "\n... (truncated)"
        return output or "(no output)"
    except subprocess.TimeoutExpired:
        return "[error] git timed out"
    except FileNotFoundError:
        return "[error] git not found"
    except Exception as e:
        return f"[error] {e}"


def list_dir(path: str = ".") -> str:
    real = permissions.resolve(path)
    ignore = _load_ignore_patterns()
    try:
        entries = sorted(os.listdir(real))
        lines = []
        for name in entries:
            if _should_ignore(name, ignore):
                continue
            full = os.path.join(real, name)
            if os.path.isdir(full):
                lines.append(name + "/")
            else:
                try:
                    lines.append(f"{name}  ({_fmt_size(os.path.getsize(full))})")
                except OSError:
                    lines.append(name)
        return "\n".join(lines)
    except PermissionError:
        return "[error] Permission denied."
