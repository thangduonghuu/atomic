#!/usr/bin/env python3
# run with: python3 main.py
import os
import re
import llm
import tools

def get_model_path() -> str:
    # load from .env if exists
    env_file = os.path.join(os.path.dirname(__file__), ".env")
    if os.path.exists(env_file):
        with open(env_file) as f:
            for line in f:
                if line.startswith("MODEL_PATH="):
                    return os.path.expanduser(line.split("=", 1)[1].strip())
    # fall back to env var
    if os.environ.get("MODEL_PATH"):
        return os.path.expanduser(os.environ["MODEL_PATH"])
    # ask user
    path = input("Enter path to your GGUF model: ").strip()
    return os.path.expanduser(path)

MODEL_PATH = get_model_path()

SYSTEM_PROMPT = f"""You are a local coding assistant with direct filesystem access on this machine.
Current working directory: {os.getcwd()}

IMPORTANT: You CAN and MUST read files using these tools. Never say you cannot access files.

TOOLS — output exactly these tags to use them:
  <list_dir path="."/>          → lists files in a directory
  <read_file path="app.py"/>    → reads a file

EXAMPLES:

User: check this project
Assistant: <list_dir path="."/>

User: review my code
Assistant: <list_dir path="."/>

User: what's in main.py?
Assistant: <read_file path="main.py"/>

User: any bugs?
Assistant: <list_dir path="."/>

After receiving file contents, give your review. Never say you cannot access the filesystem."""

COMMANDS = """
Commands:
  /read <path>    — force read a file and send to model
  /clear          — clear conversation history
  /exit           — quit
  or just chat normally and mention a file path
"""

TOOL_READ = re.compile(r'<read_file\s+path=["\']([^"\']+)["\']')
TOOL_LIST = re.compile(r'<list_dir\s+path=["\']([^"\']+)["\']')


def process_tool_calls(response: str, history: list[dict]) -> str:
    """Fulfill file read and list_dir requests from the model, loop until no more tools."""
    while True:
        tool_results = []

        for path in TOOL_LIST.findall(response):
            print(f"\n  [model listing: {path}]")
            result = tools.list_dir(path)
            tool_results.append(f"Directory listing of {path}:\n{result}")

        for path in TOOL_READ.findall(response):
            print(f"\n  [model wants to read: {path}]")
            content = tools.read_file(path)
            if content:
                tool_results.append(f"Contents of {path}:\n```\n{content}\n```")
            else:
                tool_results.append(f"Could not read {path} (denied or not found).")

        if not tool_results:
            return response

        history.append({"role": "assistant", "content": response})
        history.append({"role": "user", "content": "\n\n".join(tool_results)})

        print("model> ", end="", flush=True)
        response = llm.chat(history)
        print(response)
        print()


def run():
    print("=" * 50)
    print("  atomic — local code assistant (Qwen)")
    print("=" * 50)
    print(COMMANDS)

    llm.load_model(MODEL_PATH)

    dir_listing = tools.list_dir(".")
    history: list[dict] = [
        {"role": "system", "content": SYSTEM_PROMPT},
        {"role": "user", "content": f"FYI, current directory contains:\n{dir_listing}"},
        {"role": "assistant", "content": "Got it, I can see the files in your current directory."},
    ]

    while True:
        try:
            user_input = input("you> ").strip()
        except (EOFError, KeyboardInterrupt):
            print("\nbye.")
            break

        if not user_input:
            continue

        if user_input in ("/exit", "/quit"):
            print("bye.")
            break

        if user_input == "/clear":
            history = [{"role": "system", "content": SYSTEM_PROMPT}]
            print("  [cleared]\n")
            continue

        if user_input.startswith("/read "):
            path = user_input[6:].strip()
            content = tools.read_file(path)
            if content is None:
                continue
            history.append({"role": "user", "content": f"Please review this file ({path}):\n```\n{content}\n```"})
        elif any(kw in user_input.lower() for kw in ("project", "directory", "thư mục", "dự án", "code", "review", "check", "bug", "sai")):
            # proactively inject dir listing so model doesn't need to ask
            listing = tools.list_dir(".")
            history.append({"role": "user", "content": f"{user_input}\n\n[Current directory files:\n{listing}]"})
        else:
            history.append({"role": "user", "content": user_input})

        print("model> ", end="", flush=True)
        try:
            reply = llm.chat(history)
        except Exception as e:
            print(f"\n  [error] {e}")
            continue

        print(reply)
        print()

        # agentic loop: fulfill any file read requests from the model
        reply = process_tool_calls(reply, history)
        history.append({"role": "assistant", "content": reply})


if __name__ == "__main__":
    run()
