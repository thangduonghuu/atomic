import os
import subprocess
from atomic import permissions

_BLOCKING_CMDS = (
    "npm run dev", "pnpm dev", "yarn dev", "vite", "nodemon",
    "flask run", "uvicorn", "gunicorn", "python -m http.server",
    "serve ", "next dev", "rails s",
)

def _is_blocking(code: str) -> bool:
    lower = code.strip().lower()
    return any(p in lower for p in _BLOCKING_CMDS) and "&" not in code


def run_script(code: str, lang: str = "bash", on_line=None) -> dict:
    """Returns {"output": str, "ok": bool, "returncode": int}.
    If on_line is provided, calls it with each output line as it arrives.
    """
    if lang in ("bash", "sh", "shell") and _is_blocking(code):
        return {
            "output": "[skipped] This looks like a long-running server command. Run it yourself in a separate terminal.",
            "ok": False,
            "returncode": -1,
        }

    if lang in ("bash", "sh", "shell"):
        cmd = ["bash", "-c", code]
    elif lang in ("python", "py", "python3"):
        cmd = ["python3", "-c", code]
    else:
        return {"output": f"[unsupported language: {lang}]", "ok": False, "returncode": -1}
    try:
        if on_line is not None:
            proc = subprocess.Popen(
                cmd, stdout=subprocess.PIPE, stderr=subprocess.STDOUT,
                text=True, cwd=os.getcwd(),
            )
            lines = []
            try:
                for line in proc.stdout:
                    lines.append(line)
                    on_line(line.rstrip())
                proc.wait(timeout=30)
            except subprocess.TimeoutExpired:
                proc.kill()
                return {"output": "[error] script timed out after 30s", "ok": False, "returncode": -1}
            output = "".join(lines).strip() or "(no output)"
            return {"output": output, "ok": proc.returncode == 0, "returncode": proc.returncode}
        else:
            r = subprocess.run(cmd, capture_output=True, text=True, timeout=30, cwd=os.getcwd())
            output = (r.stdout + r.stderr).strip() or "(no output)"
            return {"output": output, "ok": r.returncode == 0, "returncode": r.returncode}
    except subprocess.TimeoutExpired:
        return {"output": "[error] script timed out after 30s", "ok": False, "returncode": -1}
    except Exception as e:
        return {"output": f"[error] {e}", "ok": False, "returncode": -1}

def read_file(path: str) -> str | None:
    real = permissions.resolve(path)

    if not os.path.exists(real):
        print(f"  [error] File not found: {real}")
        return None

    if not permissions.is_allowed(real):
        if not permissions.ask(real):
            print("  [denied] File access denied.")
            return None

    with open(real, "r", errors="replace") as f:
        return f.read()

def list_dir(path: str = ".") -> str:
    real = permissions.resolve(path)
    try:
        entries = os.listdir(real)
        return "\n".join(sorted(entries))
    except PermissionError:
        return "[error] Permission denied."
