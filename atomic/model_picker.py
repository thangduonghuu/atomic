import os
from atomic import config


def pick_model() -> str:
    cfg = config.load()
    default = cfg.get("default_model")
    recent = cfg.get("recent_models", [])
    found = config.find_gguf_files()

    # merge: recent first, then discovered, no duplicates
    seen = set()
    options = []
    for p in recent + found:
        if p not in seen and os.path.exists(p):
            seen.add(p)
            options.append(p)

    # auto-use default, only show picker if no default or forced
    if default and os.path.exists(default):
        return default

    print("\n" + "=" * 50)
    print("  Select a model")
    print("=" * 50)

    if options:
        print("\n  Available models:")
        for i, path in enumerate(options):
            marker = " *" if path == default else "  "
            print(f" {marker} [{i + 1}] {os.path.basename(path)}")
            print(f"       {path}")

    print(f"\n   [m] Enter path manually")
    print()

    while True:
        choice = input("  Choose [1-{}/m]: ".format(len(options))).strip().lower()

        if choice == "m" or (not options and choice == ""):
            path = input("  Model path: ").strip()
            path = os.path.expanduser(path)
            if not os.path.exists(path):
                print(f"  [error] File not found: {path}")
                continue
            break

        if choice.isdigit():
            idx = int(choice) - 1
            if 0 <= idx < len(options):
                path = options[idx]
                break
            print(f"  [error] Pick a number between 1 and {len(options)}")
            continue

        print("  [error] Invalid choice.")

    config.set_default(path)
    print(f"\n  Saved as default: {os.path.basename(path)}\n")
    return path
