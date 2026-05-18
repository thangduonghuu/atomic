import os
from rich.console import Console

_allowed: set[str] = set()
console = Console()


def resolve(path: str) -> str:
    return os.path.realpath(os.path.expanduser(path))

def is_allowed(path: str) -> bool:
    return resolve(path) in _allowed

def ask(path: str) -> bool:
    real = resolve(path)
    console.print(f"\n  [yellow][permission][/yellow] Allow reading: {real}")
    answer = input("  [y]es / [n]o / [a]lways this session: ").strip().lower()
    if answer in ("a", "always"):
        _allowed.add(real)
        return True
    return answer in ("y", "yes")
