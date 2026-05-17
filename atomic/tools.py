import os
import subprocess
from atomic import permissions

def run_script(code: str, lang: str = "bash") -> dict:
    """Returns {"output": str, "ok": bool, "returncode": int}."""
    if lang in ("bash", "sh", "shell"):
        cmd = ["bash", "-c", code]
    elif lang in ("python", "py", "python3"):
        cmd = ["python3", "-c", code]
    else:
        return {"output": f"[unsupported language: {lang}]", "ok": False, "returncode": -1}
    try:
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
