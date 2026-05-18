import os
from rich.console import Console
from atomic import config

console = Console()


def pick_model(force: bool = False) -> str:
    cfg = config.load()
    default = cfg.get("default_model")
    recent = cfg.get("recent_models", [])
    found = config.find_gguf_files()

    seen = set()
    options = []
    for p in recent + found:
        if p not in seen and os.path.exists(p):
            seen.add(p)
            options.append(p)

    if not force and default and os.path.exists(default):
        return default

    console.print("\n" + "=" * 50)
    console.print("  Select a model")
    console.print("=" * 50)

    if options:
        console.print("\n  Available models:")
        for i, path in enumerate(options):
            marker = "[green]*[/green]" if path == default else " "
            console.print(f"  {marker} [{i + 1}] {os.path.basename(path)}")
            console.print(f"       [dim]{path}[/dim]")

    console.print("\n   [m] Enter path manually\n")

    while True:
        choice = input("  Choose [1-{}/m]: ".format(len(options))).strip().lower()

        if choice == "m" or (not options and choice == ""):
            path = input("  Model path: ").strip()
            path = os.path.expanduser(path)
            if not os.path.exists(path):
                console.print(f"  [red]File not found: {path}[/red]")
                continue
            break

        if choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(options):
                path = options[idx]
                break
            console.print(f"  [red]Pick a number between 1 and {len(options)}[/red]")
            continue

        console.print("  [red]Invalid choice.[/red]")

    config.set_default(path)
    console.print(f"\n  Saved as default: [cyan]{os.path.basename(path)}[/cyan]\n")
    return path
