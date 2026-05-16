import os
from atomic import permissions

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
