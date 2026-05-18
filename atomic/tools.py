import os
import signal
import subprocess
import threading
from rich.console import Console
from atomic import permissions

console = Console()

_MAX_FILE_BYTES = 512 * 1024  # 512 KB


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


def write_file(path: str, content: str) -> str:
    real = permissions.resolve(path)
    os.makedirs(os.path.dirname(real) or ".", exist_ok=True)
    with open(real, "w", encoding="utf-8") as f:
        f.write(content)
    return f"Written {len(content)} bytes to {real}"


def read_file(path: str) -> str | None:
    real = permissions.resolve(path)

    if not os.path.exists(real):
        console.print(f"  [red][error][/red] File not found: {real}")
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


def list_dir(path: str = ".") -> str:
    real = permissions.resolve(path)
    try:
        entries = sorted(os.listdir(real))
        lines = []
        for name in entries:
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
