import json
import os
import urllib.error
import urllib.request

from rich.console import Console
from rich.progress import BarColumn, DownloadColumn, Progress, TextColumn, TimeRemainingColumn, TransferSpeedColumn

console = Console()

HF_API = "https://huggingface.co/api/models/{repo_id}"
HF_DOWNLOAD = "https://huggingface.co/{repo_id}/resolve/main/{filename}"
DOWNLOAD_DIR = os.path.expanduser("~/Documents/models")

QUANT_LABELS: dict[str, tuple[str, str]] = {
    "q2_k":   ("Q2_K",   "very compressed, noticeable quality loss"),
    "q3_k_m": ("Q3_K_M", "small, some quality loss"),
    "q3_k_s": ("Q3_K_S", "small, some quality loss"),
    "q4_0":   ("Q4_0",   "fast, good for low-RAM machines"),
    "q4_k_m": ("Q4_K_M", "best balance of size and quality"),
    "q4_k_s": ("Q4_K_S", "slightly smaller than Q4_K_M"),
    "q5_k_m": ("Q5_K_M", "high quality, needs more RAM"),
    "q5_k_s": ("Q5_K_S", "high quality, slightly smaller"),
    "q5_0":   ("Q5_0",   "high quality, older format"),
    "q6_k":   ("Q6_K",   "very high quality, ~6 bits/weight"),
    "q8_0":   ("Q8_0",   "near-lossless, largest GGUF size"),
    "f16":    ("F16",    "full precision, very large"),
    "f32":    ("F32",    "full precision, very large"),
}

RECOMMENDED_QUANTS = {"q4_k_m", "q5_k_m"}


def _quant_info(filename: str) -> tuple[str, str, bool]:
    """Returns (label, description, is_recommended) parsed from a GGUF filename."""
    lower = filename.lower()
    for key, (label, desc) in QUANT_LABELS.items():
        if key in lower:
            return label, desc, key in RECOMMENDED_QUANTS
    return "", "", False


def fetch_gguf_files(repo_id: str) -> list[dict]:
    url = HF_API.format(repo_id=repo_id)
    try:
        with urllib.request.urlopen(url, timeout=15) as resp:
            data = json.loads(resp.read())
    except urllib.error.HTTPError as e:
        if e.code == 404:
            raise ValueError(f"Repo not found: {repo_id}")
        raise RuntimeError(f"HuggingFace API error: {e.code}")
    return [s for s in data.get("siblings", []) if s["rfilename"].endswith(".gguf")]


def download_model(repo_id: str, filename: str) -> str:
    os.makedirs(DOWNLOAD_DIR, exist_ok=True)
    dest = os.path.join(DOWNLOAD_DIR, filename)

    if os.path.exists(dest):
        console.print(f"  [yellow]Already exists:[/yellow] {dest}")
        return dest

    url = HF_DOWNLOAD.format(repo_id=repo_id, filename=filename)
    tmp = dest + ".part"

    console.print(f"\n  Downloading [cyan]{filename}[/cyan]")
    console.print(f"  From: [dim]{url}[/dim]")
    console.print(f"  To:   [dim]{dest}[/dim]\n")

    try:
        resp = urllib.request.urlopen(url, timeout=30)
        total = int(resp.headers.get("Content-Length", 0)) or None

        with Progress(
            TextColumn("  [progress.description]{task.description}"),
            BarColumn(),
            DownloadColumn(),
            TransferSpeedColumn(),
            TimeRemainingColumn(),
            console=console,
        ) as progress:
            task = progress.add_task("downloading", total=total)
            with open(tmp, "wb") as f:
                while chunk := resp.read(65536):
                    f.write(chunk)
                    progress.advance(task, len(chunk))

        os.rename(tmp, dest)
        return dest

    except Exception as e:
        if os.path.exists(tmp):
            os.remove(tmp)
        raise RuntimeError(f"Download failed: {e}") from e


def interactive_download(repo_id: str | None = None) -> str:
    if not repo_id:
        console.print("\n  [bold]Download model from HuggingFace[/bold]")
        console.print("  [dim]Example: TheBloke/Mistral-7B-v0.1-GGUF[/dim]\n")
        repo_id = input("  Repo ID: ").strip()
        if not repo_id:
            raise ValueError("No repo ID provided.")

    console.print(f"\n  [dim]Fetching file list...[/dim]", end="\r")
    files = fetch_gguf_files(repo_id)
    console.print(" " * 40 + "\r", end="")

    if not files:
        raise ValueError(f"No GGUF files found in {repo_id}")

    console.print(f"\n  GGUF files in [cyan]{repo_id}[/cyan]:\n")
    for i, f in enumerate(files):
        size = f.get("size", 0)
        size_str = f"  {size / 1e9:.1f} GB" if size else ""
        label, desc, is_rec = _quant_info(f["rfilename"])
        quant_str = f"  [cyan]{label}[/cyan] [dim]— {desc}[/dim]" if label else ""
        rec_str = "  [green bold]★ recommended[/green bold]" if is_rec else ""
        console.print(f"    [{i + 1}] {f['rfilename']}[dim]{size_str}[/dim]{quant_str}{rec_str}")

    console.print()
    while True:
        choice = input(f"  Choose [1-{len(files)}]: ").strip()
        if choice.isdigit() and 0 <= int(choice) - 1 < len(files):
            filename = files[int(choice) - 1]["rfilename"]
            break
        console.print("  [red]Invalid choice.[/red]")

    return download_model(repo_id, filename)
