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


def load() -> dict:
    if os.path.exists(CONFIG_FILE):
        with open(CONFIG_FILE) as f:
            return json.load(f)
    return {"default_model": None, "recent_models": []}


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
