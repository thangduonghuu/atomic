from llama_cpp import Llama

_model = None

def load_model(model_path: str, n_ctx: int = 8192, n_gpu_layers: int = -1):
    global _model
    print(f"Loading model from {model_path} ...")
    _model = Llama(
        model_path=model_path,
        n_ctx=n_ctx,
        n_gpu_layers=n_gpu_layers,
        verbose=False,
    )
    print("Model loaded.\n")

def chat(messages: list[dict]) -> str:
    if _model is None:
        raise RuntimeError("Model not loaded. Call load_model() first.")
    response = _model.create_chat_completion(
        messages=messages,
        temperature=0.2,
        max_tokens=2048,
    )
    return response["choices"][0]["message"]["content"]
