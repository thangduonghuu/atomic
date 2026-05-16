import time
from llama_cpp import Llama

_model = None
_model_name = ""


def load_model(model_path: str, n_ctx: int = 8192, n_gpu_layers: int = -1):
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


def chat(messages: list[dict]) -> dict:
    if _model is None:
        raise RuntimeError("Model not loaded.")
    t0 = time.time()
    response = _model.create_chat_completion(
        messages=messages,
        temperature=0.2,
        max_tokens=2048,
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
    pass
