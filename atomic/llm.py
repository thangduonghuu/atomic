import os
import time
import threading
from llama_cpp import Llama

_model = None
_model_name = ""
_think_model = None
_think_model_name = ""
_stop_event = threading.Event()


def load_model(model_path: str, n_ctx: int = 32768, n_gpu_layers: int = -1):
    global _model, _model_name
    import os
    _model_name = os.path.basename(model_path)
    _model = Llama(
        model_path=model_path,
        n_ctx=n_ctx,
        n_gpu_layers=n_gpu_layers,
        verbose=False,
    )


def get_model_name() -> str:
    return _model_name


def load_think_model(model_path: str, n_ctx: int = 32768, n_gpu_layers: int = -1):
    global _think_model, _think_model_name
    import os
    _think_model_name = os.path.basename(model_path)
    _think_model = Llama(
        model_path=model_path,
        n_ctx=n_ctx,
        n_gpu_layers=n_gpu_layers,
        verbose=False,
    )


def get_think_model_name() -> str:
    return _think_model_name or _model_name


def _count_tokens(msg: dict, model=None) -> int:
    m = model or _model
    try:
        return len(m.tokenize(msg["content"].encode(), add_bos=False))
    except Exception:
        return len(msg["content"]) // 4


def _truncate_messages(messages: list[dict], max_prompt_tokens: int, model=None) -> list[dict]:
    """Truncate messages to fit within max_prompt_tokens.
    model param ensures token counting uses the correct tokenizer (important when
    main model and think model have different vocabularies).
    """
    count = lambda m: _count_tokens(m, model)

    system = [m for m in messages if m["role"] == "system"]
    rest = [m for m in messages if m["role"] != "system"]

    system_tokens = sum(count(m) for m in system)
    counts = [count(m) for m in rest]
    total = system_tokens + sum(counts)

    dropped: list[dict] = []
    while rest and total > max_prompt_tokens:
        total -= counts[0]
        dropped.append(rest[0])
        rest = rest[1:]
        counts = counts[1:]

    if not dropped:
        return system + rest

    lines = [f"[{len(dropped)} earlier messages were compressed to fit context]"]
    for m in dropped:
        preview = m["content"][:120].replace("\n", " ")
        lines.append(f"  {m['role']}: {preview}…")
    summary = {"role": "user", "content": "\n".join(lines)}
    ack = {"role": "assistant", "content": "Understood, continuing with current context."}
    return system + [summary, ack] + rest


def chat(messages: list[dict], on_token=None) -> dict:
    if _model is None:
        raise RuntimeError("Model not loaded.")

    # Reserve ~25% of context for response; at minimum 2048 tokens
    n_ctx = _model.n_ctx()
    max_prompt_tokens = max(512, n_ctx - max(2048, n_ctx // 4) - 64)
    messages = _truncate_messages(messages, max_prompt_tokens, model=_model)

    t0 = time.time()

    if on_token is not None:
        _stop_event.clear()
        stream = _model.create_chat_completion(
            messages=messages,
            temperature=0.6,
            top_p=0.95,
            top_k=20,
            min_p=0.0,
            max_tokens=-1,
            stream=True,
        )
        content = ""
        n_tokens = 0
        try:
            for chunk in stream:
                if _stop_event.is_set():
                    break
                delta = chunk["choices"][0]["delta"].get("content") or ""
                if delta:
                    content += delta
                    n_tokens += 1
                    on_token(delta)
        finally:
            if hasattr(stream, "close"):
                try:
                    stream.close()
                except Exception:
                    pass
        elapsed = time.time() - t0
        return {
            "content": content,
            "prompt_tokens": 0,
            "completion_tokens": n_tokens,
            "total_tokens": n_tokens,
            "elapsed": elapsed,
        }

    response = _model.create_chat_completion(
        messages=messages,
        temperature=0.6,
        top_p=0.95,
        top_k=20,
        min_p=0.0,
        max_tokens=-1,
    )
    elapsed = time.time() - t0
    usage = response.get("usage", {})
    return {
        "content": response["choices"][0]["message"]["content"],
        "prompt_tokens": usage.get("prompt_tokens", 0),
        "completion_tokens": usage.get("completion_tokens", 0),
        "total_tokens": usage.get("total_tokens", 0),
        "elapsed": elapsed,
    }


def think_chat(messages: list[dict], on_token=None) -> dict:
    """Like chat() but uses the thinking model when one is loaded."""
    model = _think_model if _think_model is not None else _model
    if model is None:
        raise RuntimeError("Model not loaded.")

    n_ctx = model.n_ctx()
    max_prompt_tokens = max(512, n_ctx - max(2048, n_ctx // 4) - 64)
    messages = _truncate_messages(messages, max_prompt_tokens, model=model)

    t0 = time.time()

    if on_token is not None:
        _stop_event.clear()
        stream = model.create_chat_completion(
            messages=messages,
            temperature=0.6,
            top_p=0.95,
            top_k=20,
            min_p=0.0,
            max_tokens=-1,
            stream=True,
        )
        content = ""
        n_tokens = 0
        try:
            for chunk in stream:
                if _stop_event.is_set():
                    break
                delta = chunk["choices"][0]["delta"].get("content") or ""
                if delta:
                    content += delta
                    n_tokens += 1
                    on_token(delta)
        finally:
            if hasattr(stream, "close"):
                try:
                    stream.close()
                except Exception:
                    pass
        elapsed = time.time() - t0
        return {
            "content": content,
            "prompt_tokens": 0,
            "completion_tokens": n_tokens,
            "total_tokens": n_tokens,
            "elapsed": elapsed,
        }

    response = model.create_chat_completion(
        messages=messages,
        temperature=0.6,
        top_p=0.95,
        top_k=20,
        min_p=0.0,
        max_tokens=-1,
    )
    elapsed = time.time() - t0
    usage = response.get("usage", {})
    return {
        "content": response["choices"][0]["message"]["content"],
        "prompt_tokens": usage.get("prompt_tokens", 0),
        "completion_tokens": usage.get("completion_tokens", 0),
        "total_tokens": usage.get("total_tokens", 0),
        "elapsed": elapsed,
    }


def stop():
    _stop_event.set()


def estimate_context_usage(messages: list[dict]) -> tuple[int, int]:
    """Returns (used_tokens, max_tokens) after applying the same truncation that
    chat() would apply — so the percentage reflects what actually reaches the model."""
    if _model is None:
        return 0, 0
    n_ctx = _model.n_ctx()
    max_prompt = max(512, n_ctx - max(2048, n_ctx // 4) - 64)
    effective = _truncate_messages(messages, max_prompt, model=_model)
    used = sum(_count_tokens(m, _model) for m in effective)
    return used, n_ctx


def benchmark() -> float:
    """Run a quick 20-token pass. Returns tok/s, 0 if model not loaded."""
    if _model is None:
        return 0.0
    try:
        t0 = time.time()
        n = 0
        stream = _model.create_chat_completion(
            messages=[{"role": "user", "content": "Hi"}],
            max_tokens=20,
            temperature=0.0,
            stream=True,
        )
        for chunk in stream:
            if chunk["choices"][0]["delta"].get("content"):
                n += 1
        elapsed = time.time() - t0
        return n / elapsed if elapsed > 0 and n > 0 else 0.0
    except Exception:
        return 0.0


def check_ram(model_path: str) -> tuple[float, float]:
    """Returns (model_size_gb, available_ram_gb). available = 0 if unknown."""
    import platform
    model_gb = os.path.getsize(model_path) / 1e9 if os.path.exists(model_path) else 0.0
    available_gb = 0.0
    try:
        if platform.system() == "Darwin":
            import subprocess as _sp
            r = _sp.run(["vm_stat"], capture_output=True, text=True, timeout=3)
            page_size = 4096
            free = 0
            for line in r.stdout.splitlines():
                if "Pages free" in line or "Pages inactive" in line:
                    free += int(line.split(":")[1].strip().rstrip("."))
            available_gb = (free * page_size) / 1e9
        elif platform.system() == "Linux":
            with open("/proc/meminfo") as f:
                for line in f:
                    if line.startswith("MemAvailable"):
                        available_gb = int(line.split()[1]) / 1e6
                        break
    except Exception:
        pass
    return model_gb, available_gb
