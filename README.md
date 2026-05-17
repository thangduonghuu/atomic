# atomic

Local-first CLI coding assistant built on Qwen (GGUF) and llama-cpp-python. Interactive chat, file reading with permission gates, agentic directory exploration — no API keys, no data leaves your machine.

## Features

- **Offline** — runs entirely on your machine using a local GGUF model
- **File access** — asks permission before reading any file, with a session-wide "always allow" option
- **Agentic** — model can list directories and read files by itself to answer your questions
- **Context-aware** — automatically injects current directory listing so you can say "review my code" without specifying paths

## Requirements

- Python 3.10+
- A Qwen GGUF model (e.g. `qwen2.5-coder-7b-instruct-q4_k_m.gguf`)
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

Set your model path:
```bash
cp .env.example .env
# edit .env and set MODEL_PATH to your GGUF file
```

Now you can run it from anywhere:
```bash
atomic
```

## Usage

```bash
source venv/bin/activate
python3 main.py
```

Or use the launcher script:
```bash
./run.sh
```

## Commands

| Command | Description |
|---|---|
| `chat naturally` | Ask anything, mention filenames and the model reads them |
| `/read <path>` | Force read a file and send to model |
| `/clear` | Clear conversation history |
| `/exit` | Quit |

## Example

```
you> review my code
  [model listing: .]
  [model wants to read: main.py]
  [permission] Allow reading: /your/project/main.py
  y/n/a: a

model> Here's my review of main.py: ...

you> what does utils.py do?
  [model wants to read: utils.py]
model> utils.py contains helper functions for ...
```

## Project Structure

```
atomic/
├── main.py          # CLI entry + chat loop
├── llm.py           # llama-cpp-python model loader
├── permissions.py   # file access permission gate
├── tools.py         # read_file, list_dir
└── run.sh           # launcher script
```

## License

MIT
