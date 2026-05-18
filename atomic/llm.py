import time
import threading
from llama_cpp import Llama

_model = None
_model_name = ""
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


def _count_tokens(msg: dict) -> int:
    try:
        return len(_model.tokenize(msg["content"].encode(), add_bos=False))
    except Exception:
        return len(msg["content"]) // 4


def _truncate_messages(messages: list[dict], max_prompt_tokens: int) -> list[dict]:
    system = [m for m in messages if m["role"] == "system"]
    rest = [m for m in messages if m["role"] != "system"]

    system_tokens = sum(_count_tokens(m) for m in system)
    counts = [_count_tokens(m) for m in rest]
    total = system_tokens + sum(counts)

    while rest and total > max_prompt_tokens:
        total -= counts[0]
        rest = rest[1:]
        counts = counts[1:]

    return system + rest


def chat(messages: list[dict], on_token=None) -> dict:
    if _model is None:
        raise RuntimeError("Model not loaded.")

    # Reserve ~25% of context for response; at minimum 2048 tokens
    n_ctx = _model.n_ctx()
    max_prompt_tokens = max(512, n_ctx - max(2048, n_ctx // 4) - 64)
    messages = _truncate_messages(messages, max_prompt_tokens)

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


def stop():
    _stop_event.set()


def estimate_context_usage(messages: list[dict]) -> tuple[int, int]:
    """Returns (used_tokens, max_tokens). Both 0 if model not loaded."""
    if _model is None:
        return 0, 0
    used = sum(_count_tokens(m) for m in messages)
    return used, _model.n_ctx()
