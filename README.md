# atomic

Local-first CLI coding assistant powered by any GGUF model via llama-cpp-python. No API keys, no data leaves your machine.

## Features

- **Fully offline** — runs entirely on your machine using a local GGUF model
- **Agentic** — model can list directories, read files, and write files autonomously
- **Smart script execution** — model runs bash/python commands; failed scripts are auto-fixed and retried (up to 3 attempts)
- **Server detection** — long-running processes (dev servers, watchers) are automatically detected and shown as manual commands instead of blocking the session
- **Inactivity-based timeout** — scripts run as long as they produce output; killed only after 10s of silence (servers) or 60s with no output at all (stuck)
- **Interruptible** — Ctrl+C stops thinking or a running script without killing the session; Ctrl+D to exit
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

## Keyboard Shortcuts

| Key | During input | During thinking | During script |
|---|---|---|---|
| `Ctrl+C` | Clear current line | Stop generation | Kill script |
| `Ctrl+D` | Exit app | — | — |
| `Enter` | Submit | — | — |

## Script Execution

The model uses two code block types to distinguish one-time commands from servers:

- ` ```bash ` — auto-executed (install, build, scaffold, test)
- ` ```bash-server ` — displayed only, not auto-run (dev servers, watchers)

If the model mistakenly puts a server command in a `bash` block, atomic detects it at runtime: if the process produces output then goes silent for 10 seconds while still running, it is killed and flagged as a server command.

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
