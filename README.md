# atomic

Local-first CLI coding assistant powered by any GGUF model via llama-cpp-python. No API keys, no data leaves your machine.

## Features

- **Fully offline** — runs entirely on your machine using a local GGUF model
- **Agentic** — model can list directories and read files autonomously to answer your questions
- **Script execution** — model can suggest and run bash/python scripts; failed scripts are auto-fixed and retried (up to 3 attempts)
- **Context-aware** — injects current directory listing automatically so you can say "review my code" without specifying paths
- **Model management** — download models from HuggingFace, register local files, switch models mid-session
- **Context window safety** — automatically truncates history to fit within the model's context window

## Requirements

- Python 3.10+
- A GGUF model (e.g. `qwen2.5-coder-7b-instruct-q4_k_m.gguf`)
- [llama-cpp-python](https://github.com/abetlen/llama-cpp-python)

## Setup

```bash
git clone https://github.com/thangduonghuu/atomic
cd atomic
```

**Mac (Metal GPU acceleration):**
```bash
CMAKE_ARGS="-DGGML_METAL=on" pip install llama-cpp-python
pip install -e .
```

**Without GPU:**
```bash
pip install llama-cpp-python
pip install -e .
```

On first run, atomic will prompt you to select or download a model. The choice is saved as default for future runs.

## Usage

```bash
atomic
```

## CLI Commands

```
atomic                        Start chat with default model
atomic model                  Re-select default model
atomic model list             List registered models
atomic model add <path>       Register a local .gguf file
atomic model download [repo]  Download a model from HuggingFace
atomic help                   Show help
```

## Chat Commands

| Command | Description |
|---|---|
| `/read <path>` | Load a file into context |
| `/model` | Switch model mid-session |
| `/clear` | Reset conversation history |
| `/exit` or `/quit` | Quit |

## Project Structure

```
atomic/
├── main.py          # CLI entry, chat loop, script execution
├── llm.py           # llama-cpp-python wrapper, context truncation
├── tools.py         # read_file, list_dir, run_script
├── permissions.py   # file access permission gate
├── model_picker.py  # interactive model selection
├── download.py      # HuggingFace model downloader
└── config.py        # saved config (~/.config/atomic/)
```

## License

MIT
