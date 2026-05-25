# atomic

Local-first CLI coding assistant powered by any GGUF model via llama-cpp-python. No API keys, no data leaves your machine.

## Features

- **Fully offline** — runs entirely on your machine using a local GGUF model
- **Autonomous agent** — `/agent` mode: model plans and implements a task end-to-end, looping through read → edit → run → verify until done
- **Change suggestions** — file edits show a syntax-highlighted diff and require confirmation before writing
- **Smart script execution** — auto-runs bash/python commands; failed scripts are auto-fixed and retried (up to 3 attempts)
- **Deep investigation** — `/think` mode uses a second model to explore the codebase and produce a structured work note
- **Background server** — dev servers run in the background, output streams to terminal, chat stays usable
- **Server detection** — if a server accidentally lands in a `bash` block, atomic detects and moves it to background automatically
- **Interruptible** — Ctrl+C stops thinking or a running script without exiting; Ctrl+D to quit
- **Context-aware** — injects current directory listing so you can say "review my code" without specifying paths
- **Model management** — download from HuggingFace, register local files, switch models mid-session

---

## Setup

### 1. Clone the repo

```bash
git clone https://github.com/thangduonghuu/atomic
cd atomic
```

### 2. Create a virtual environment (recommended)

```bash
python3 -m venv venv
source venv/bin/activate
```

### 3. Install llama-cpp-python

**Mac — Metal GPU acceleration:**
```bash
CMAKE_ARGS="-DGGML_METAL=on" pip install llama-cpp-python
```

**Linux — CUDA:**
```bash
CMAKE_ARGS="-DGGML_CUDA=on" pip install llama-cpp-python
```

**CPU only:**
```bash
pip install llama-cpp-python
```

### 4. Install atomic

```bash
pip install -e .
```

---

## Download a model

Any GGUF model from [HuggingFace](https://huggingface.co/models?library=gguf) works. Recommended starting point: [Qwen2.5-Coder-7B-Instruct-GGUF](https://huggingface.co/Qwen/Qwen2.5-Coder-7B-Instruct-GGUF).

**Interactive (recommended):**
```bash
atomic model download
```
This opens an interactive prompt to search and download any GGUF model from HuggingFace.

**Direct repo:**
```bash
atomic model download Qwen/Qwen2.5-Coder-7B-Instruct-GGUF
```

**Register a model you already have:**
```bash
atomic model add ~/models/qwen2.5-coder-7b-instruct-q4_k_m.gguf
```

The selected model is saved as default for future runs.

---

## Run

```bash
atomic
```

On first run, atomic prompts you to pick or download a model.

---

## CLI Commands

```
atomic                        Start chat
atomic serve                  Listen on Telegram 24/7 (long-poll agent)
atomic model                  Re-select default model
atomic model list             List available models
atomic model add <path>       Register a local .gguf file
atomic model download [repo]  Download from HuggingFace
atomic help                   Show help
```

## Chat Commands

| Command | Description |
|---|---|
| `/agent <task>` | Autonomous agent — plans and implements the task end-to-end |
| `/think <prompt>` | Deep investigation using a thinking model → saves a work note |
| `/telegram` | Set up Telegram notifications |
| `/read <path>` | Load a file into context |
| `/model` | Switch main model mid-session |
| `/think-model` | Set the thinking model |
| `/server` | Stop the background server |
| `/save [path]` | Save conversation to a markdown file |
| `/clear` | Reset conversation history |
| `/exit` or `/quit` | Quit |

### Agent mode

`/agent` runs an autonomous loop where the model plans and executes the task without waiting for input between steps:

```
/agent add unit tests for the tools module
/agent refactor the script runner to support a configurable timeout
/agent fix the bug where an empty diff still triggers a write
```

The agent reads files, writes edits (still shows diff + asks confirmation), runs bash commands, checks results, and keeps going until it signals completion with `<done>`.

### Think mode

`/think` sends the task to a dedicated thinking model for deep investigation. It explores the codebase and produces a structured plan saved to `.notes/`. Useful before a large refactor or when debugging a subtle issue.

Set a different model for thinking:
```
/think-model
```

### Telegram notifications & serve mode

Two ways to use Telegram:

**Session mode** — run tasks in the terminal, get notified when done:
- `/agent` and `/think` automatically send a Telegram message on completion

**Serve mode** — always-on agent that listens for tasks via Telegram:
```bash
atomic serve
```
Send any message to the bot → agent runs and replies with step-by-step updates. File writes are auto-accepted (no terminal to confirm).

**Setup (required for both modes):**
1. Create a bot via [@BotFather](https://t.me/BotFather) and copy the token
2. Send any message to your new bot
3. Run `/telegram` inside atomic — it fetches your chat ID automatically and sends a test message

## Keyboard Shortcuts

| Key | Effect |
|---|---|
| `Enter` | Submit message |
| `Ctrl+C` | Cancel current input / interrupt thinking / kill running script |
| `Ctrl+D` | Exit app |

---

## How script execution works

The model uses two block types:

| Block | Behaviour |
|---|---|
| ` ```bash ` | Auto-executed — one-time commands (install, build, test, scaffold) |
| ` ```bash-server ` | Starts in background — dev servers, watchers, long-running processes |

If the model puts a server command in a `bash` block, atomic detects it at runtime (goes silent for 5s while still running) and moves it to background automatically.

Server output streams to the terminal prefixed with `│ [srv]`. Use `/server` to stop it.

---

## Project Structure

```
atomic/
├── main.py          # CLI entry, chat loop, script execution
├── llm.py           # llama-cpp-python wrapper, context truncation
├── tools.py         # read_file, list_dir, run_script, background server
├── permissions.py   # file access permission gate
├── model_picker.py  # interactive model selection
├── download.py      # HuggingFace model downloader
└── config.py        # saved config (~/.config/atomic/)
```

## License

MIT
