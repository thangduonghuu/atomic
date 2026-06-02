import json
import os

CONFIG_DIR = os.path.expanduser("~/.atomic")
CONFIG_FILE = os.path.join(CONFIG_DIR, "config.json")

DEFAULT_SEARCH_DIRS = [
    "~/models",
    "~/Downloads",
    "~/.ollama/models",
    "~/Documents/models",
]


_DEFAULT_CONFIG: dict = {"default_model": None, "recent_models": []}


def load() -> dict:
    if not os.path.exists(CONFIG_FILE):
        return dict(_DEFAULT_CONFIG)
    try:
        with open(CONFIG_FILE) as f:
            data = json.load(f)
        if not isinstance(data, dict):
            raise ValueError("config root is not an object")
        return data
    except Exception as e:
        import shutil, sys
        backup = CONFIG_FILE + ".bak"
        try:
            shutil.copy2(CONFIG_FILE, backup)
        except Exception:
            pass
        print(f"  [warning] Config corrupted ({e}) — reset to defaults. Backup: {backup}", file=sys.stderr)
        return dict(_DEFAULT_CONFIG)


def save(cfg: dict):
    os.makedirs(CONFIG_DIR, exist_ok=True)
    with open(CONFIG_FILE, "w") as f:
        json.dump(cfg, f, indent=2)


def set_default(model_path: str):
    cfg = load()
    model_path = os.path.expanduser(model_path)
    cfg["default_model"] = model_path
    recent = cfg.get("recent_models", [])
    if model_path in recent:
        recent.remove(model_path)
    recent.insert(0, model_path)
    cfg["recent_models"] = recent[:10]
    save(cfg)


def get_thinking_model() -> str | None:
    return load().get("thinking_model")


def set_thinking_model(model_path: str):
    cfg = load()
    cfg["thinking_model"] = os.path.expanduser(model_path)
    save(cfg)


def get_telegram() -> dict | None:
    return load().get("telegram")


def set_telegram(token: str, chat_id: str):
    cfg = load()
    cfg["telegram"] = {"token": token, "chat_id": chat_id}
    save(cfg)


def find_gguf_files() -> list[str]:
    cfg = load()
    dirs = cfg.get("search_dirs", DEFAULT_SEARCH_DIRS)
    found = []
    for d in dirs:
        d = os.path.expanduser(d)
        if not os.path.isdir(d):
            continue
        try:
            for f in os.listdir(d):
                if f.endswith(".gguf"):
                    found.append(os.path.join(d, f))
        except PermissionError:
            continue
    return sorted(found)
