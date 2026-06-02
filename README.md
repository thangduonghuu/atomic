# atomic

> A fully offline, privacy-first CLI coding agent powered by local GGUF models.  
> No API keys. No data leaves your machine.

![Python 3.10+](https://img.shields.io/badge/python-3.10%2B-blue)
![License MIT](https://img.shields.io/badge/license-MIT-green)
![Platform macOS Linux](https://img.shields.io/badge/platform-macOS%20%7C%20Linux-lightgrey)

---

## Why atomic?

Most AI coding tools send your code to a remote server. atomic runs entirely on your hardware — the model, the file system access, everything. It is designed for developers who work in air-gapped environments, care about code privacy, or simply want a capable coding agent that works offline.

The standout feature: **`atomic serve`** — an always-on agent you control via Telegram. Text your machine a task from your phone, get step-by-step updates, and receive a notification when it's done.

---

## Features

### Agent & Reasoning
- **Autonomous agent** (`/agent`) — plans and implements a task end-to-end: searches code, reads files, writes edits, runs commands, verifies results, and signals completion
- **Deep investigation** (`/think`) — uses a dedicated thinking model to explore the codebase and produce a structured work note saved to `.notes/`
- **Step limit with visual counter** — agent shows `step 3/20 · 17 left`, warns and stops gracefully at the limit

### Code Intelligence
- **Grep tool** — model searches across files before reading them (`<grep_file pattern="..." path="..."/>`)
- **Surgical edits** — model edits exact lines rather than overwriting entire files (`<edit_file>`)
- **Git awareness** — model reads `git status`, `git diff`, `git log` before making changes
- **Auto-run tests** — after agent completes, detects `pytest` / `npm test` / `go test` / `cargo test` and offers to run
- **Auto-commit** — after a successful task, offers to `git commit` with an auto-generated message

### Safety & Control
- **Diff before every write** — syntax-highlighted diff shown; requires `[y]es / [n]o / [a]lways` before any file is touched
- **`/undo`** — restores the last AI-written file from a 20-deep backup history
- **Script auto-fix** — failed bash/python scripts are sent back to the model for fixing, retried up to 3 times
- **Binary file guard** — skips binary files automatically, never injects garbage into context
- **`.atomicignore`** — exclude `node_modules`, `venv`, `.git`, etc. from context (defaults sensible out of the box)

### Remote Control
- **Telegram serve mode** (`atomic serve`) — always-on agent; send tasks via Telegram, receive step-by-step replies
- **Telegram notifications** — `/agent` and `/think` send a message on completion
- **`/status` command** (in serve mode) — reply with current model, directory, and uptime

### Developer Experience
- **Project memory** — place instructions in `.atomic/instructions.md`; injected into every session automatically
- **`/read <path>`** — load a file or an entire directory into context
- **Command history** — persisted across sessions (`↑` to recall previous commands)
- **Context warnings** — warns at 80% context usage; summarizes dropped messages instead of silently deleting them
- **Background server** — dev servers run in the background; atomic detects when a command never exits and moves it automatically
- **Model benchmark** — measures and displays `tok/s` after loading so you know what to expect

---

## Installation

```bash
git clone https://github.com/thangduonghuu/atomic && cd atomic && ./install.sh
```

The installer auto-detects your GPU and configures the build accordingly:

| Platform | Acceleration |
|---|---|
| macOS | Apple Metal (automatic) |
| Linux + NVIDIA | CUDA (automatic) |
| Everything else | CPU |

Everything is installed to `~/.atomic/`. An `atomic` command is added to your PATH — no virtual environment activation needed.

**Requirements:** Python 3.10+

---

## Quickstart

```bash
# 1. Install
git clone https://github.com/thangduonghuu/atomic && cd atomic && ./install.sh

# 2. Download a model
atomic model download

# 3. Start
atomic
```

On first run, atomic prompts you to pick or download a model. The interactive downloader labels each quantization level and marks the recommended choice.

---

## Model Management

```bash
atomic model download                          # Interactive — browse & download from HuggingFace
atomic model download Qwen/Qwen2.5-Coder-7B-Instruct-GGUF  # Direct repo
atomic model add ~/models/my-model.gguf       # Register a local file
atomic model list                             # List available models
atomic model                                  # Re-select default
```

**Recommended starting point:** [Qwen2.5-Coder-7B-Instruct-GGUF](https://huggingface.co/Qwen/Qwen2.5-Coder-7B-Instruct-GGUF) — good balance of speed and code quality on consumer hardware.

The downloader shows each file's size, quantization description, and a `★ recommended` marker for the best quality/size tradeoff (`Q4_K_M` or `Q5_K_M`).

---

## CLI Reference

```
atomic                         Start chat
atomic serve                   Always-on Telegram agent
atomic model                   Re-select default model
atomic model list              List available models
atomic model add <path>        Register a local .gguf file
atomic model download [repo]   Download from HuggingFace
atomic help                    Show help
```

---

## Chat Commands

| Command | Description |
|---|---|
| `/agent <task>` | Autonomous agent — plans and implements the task end-to-end |
| `/think <prompt>` | Deep investigation → saves a structured work note to `.notes/` |
| `/read <path>` | Load a file or directory into context |
| `/undo` | Restore the last file changed by the AI |
| `/telegram` | Configure Telegram notifications |
| `/model` | Switch main model mid-session |
| `/think-model` | Set the dedicated thinking model |
| `/server` | Stop the background dev server |
| `/save [path]` | Save conversation to a markdown file |
| `/clear` | Reset conversation history |
| `/help` | Show command reference |
| `/exit` | Quit |

---

## Agent Mode

`/agent` runs an autonomous loop: the model plans, reads files, searches code, writes edits, runs commands, and verifies results without waiting for input.

```
/agent add unit tests for the auth module
/agent refactor the database layer to use connection pooling
/agent fix the bug where empty diffs still trigger a write confirmation
```

After completion, atomic offers to:
1. Run your project's test suite
2. Commit the changes with an auto-generated message

File writes always show a diff and require confirmation — even in agent mode.

---

## Think Mode

`/think` sends the task to a dedicated reasoning model that explores the codebase and produces a structured plan:

```
/think the payment flow has a race condition — investigate and plan a fix
/think we need to add multi-tenancy — what needs to change?
```

Output is saved to `.notes/` as a markdown document with Problem, Investigation, Approach, and Step-by-step sections. Useful before a large refactor or when debugging a subtle issue.

To use a different (larger) model for thinking:

```
/think-model
```

---

## Telegram Integration

### Notifications (session mode)
Run tasks in your terminal — get a Telegram message when `/agent` or `/think` finishes.

### Serve mode (remote agent)
```bash
atomic serve
```
Keeps the agent running 24/7. Send any message to your bot and it executes as an agent task, sending step-by-step updates back to you.

**Telegram commands in serve mode:**

| Command | Description |
|---|---|
| `/agent <task>` | Run an autonomous agent task |
| `/status` | Show model, working directory, and uptime |
| `/clear` | Reset conversation history |
| `/help` | Show available commands |

**Setup:**
1. Create a bot via [@BotFather](https://t.me/BotFather) and copy the token
2. Send any message to your new bot
3. Run `/telegram` inside atomic — it auto-fetches your chat ID and sends a test message

---

## Project Memory

Create `.atomic/instructions.md` in your project root to give the model persistent context about your codebase:

```markdown
# Project instructions

- This is a Django 4.2 project using PostgreSQL
- Always use `ruff` for linting before committing
- Test files live in `tests/` and use `pytest`
- Never modify `migrations/` directly — use `manage.py makemigrations`
```

This file is automatically injected into every session's system prompt.

---

## Ignoring Files

Create `.atomicignore` in your project root to exclude paths from directory context:

```
node_modules
venv
dist
build
*.lock
*.env
```

Defaults (applied when no `.atomicignore` exists): `node_modules`, `venv`, `.git`, `__pycache__`, `dist`, `build`, `.next`, `*.lock`.

---

## Keyboard Shortcuts

| Key | Effect |
|---|---|
| `Enter` | Submit message |
| `↑ / ↓` | Navigate command history |
| `Ctrl+C` | Cancel input / interrupt generation / kill running script |
| `Ctrl+D` | Exit |

---

## How Script Execution Works

The model uses two block types:

| Block | Behaviour |
|---|---|
| ` ```bash ` | Auto-executed — one-time commands (install, build, test) |
| ` ```bash-server ` | Runs in background — dev servers, watchers, long-running processes |

If a server command ends up in a `bash` block, atomic detects it (silent for 5s while still running) and moves it to background automatically. Server output streams to the terminal prefixed with `│ [srv]`.

---

## Project Structure

```
atomic/
├── main.py          # CLI entry point, chat loop, agent loop, UI
├── llm.py           # llama-cpp-python wrapper, context management, benchmark
├── tools.py         # File I/O, grep, git, script execution, undo, background server
├── permissions.py   # File access permission gate
├── model_picker.py  # Interactive model selection
├── download.py      # HuggingFace model downloader with quantization labels
└── config.py        # Persistent config (~/.atomic/config.json)
```

---

## License

MIT
