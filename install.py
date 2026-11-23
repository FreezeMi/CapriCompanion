"""One-time setup for the desktop companion.

Everything stays inside this folder:
  .venv/          private Python environment with the pinned packages
  engine/         llama.cpp, which runs the language model
  models/         the language model file
  models/voice/   the Kokoro text-to-speech model and its voices

Every download is checked against a pinned SHA-256 checksum before it is used.
Interrupted downloads resume where they stopped when you run this again.

    python install.py           Qwen3.5 4B: better conversation (2.7 GB)
    python install.py --small   Qwen3.5 2B: faster and lighter (1.3 GB)
"""

from __future__ import annotations

import argparse
import hashlib
import json
import os
import platform
import shutil
import subprocess
import sys
import time
import urllib.request
import zipfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent
CHUNK = 1 << 20

ENGINE = {
    "label": "llama.cpp b12317 (Vulkan build, also runs on the CPU)",
    "url": "https://github.com/ggml-org/llama.cpp/releases/download/b12317/llama-b12317-bin-win-vulkan-x64.zip",
    "sha256": "968a487604622f4194d0451d77a92b6a7cff1c29338e88841dd38472c81013ca",
    "size": 38_051_900,
    "folder": "engine/llama-b12317",
}

# Both models are Apache-2.0 licensed. URLs are pinned to a repository commit.
MODELS = {
    "4b": {
        "label": "Qwen3.5 4B, Q4_K_M (unsloth)",
        "url": "https://huggingface.co/unsloth/Qwen3.5-4B-GGUF/resolve/"
        "e87f176479d0855a907a41277aca2f8ee7a09523/Qwen3.5-4B-Q4_K_M.gguf",
        "sha256": "00fe7986ff5f6b463e62455821146049db6f9313603938a70800d1fb69ef11a4",
        "size": 2_740_937_888,
        "file": "models/Qwen3.5-4B-Q4_K_M.gguf",
    },
    "2b": {
        "label": "Qwen3.5 2B, Q4_K_M (unsloth)",
        "url": "https://huggingface.co/unsloth/Qwen3.5-2B-GGUF/resolve/"
        "f6d5376be1edb4d416d56da11e5397a961aca8ae/Qwen3.5-2B-Q4_K_M.gguf",
        "sha256": "aaf42c8b7c3cab2bf3d69c355048d4a0ee9973d48f16c731c0520ee914699223",
        "size": 1_280_835_840,
        "file": "models/Qwen3.5-2B-Q4_K_M.gguf",
    },
}

# Kokoro-82M (Apache-2.0), exported to ONNX by the kokoro-onnx project (MIT).
VOICE_FILES = [
    {
        "url": "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/kokoro-v1.0.onnx",
        "sha256": "beb0d1848dee9a49da392cc3df26958d46cfa35d321edf434f52949153f0df3a",
        "size": 325_505_369,
        "file": "models/voice/kokoro-v1.0.onnx",
    },
    {
        "url": "https://github.com/thewh1teagle/kokoro-onnx/releases/download/model-files-v1.1/voices-v1.0.bin",
        "sha256": "bca610b8308e8d99f32e6fe4197e7ec01679264efed0cac9140fe9c29f1fbf7d",
        "size": 28_214_398,
        "file": "models/voice/voices-v1.0.bin",
    },
]


def sha256_of(path: Path) -> str:
    digest = hashlib.sha256()
    with path.open("rb") as handle:
        while block := handle.read(CHUNK):
            digest.update(block)
    return digest.hexdigest()


def finish_download(partial: Path, target: Path, actual: str, expected: str) -> None:
    if actual != expected:
        partial.unlink()
        raise SystemExit(
            f"{target.name} failed its checksum check and was deleted. Run install.py again."
        )
    os.replace(partial, target)
    print(f"  Verified: {target.name}")


def download(url: str, target: Path, sha256: str, size: int) -> None:
    """Download over HTTPS with resume support, then verify size and SHA-256."""
    if not url.startswith("https://"):
        raise SystemExit(f"Refusing a download that isn't HTTPS: {url}")
    if target.is_file() and target.stat().st_size == size and sha256_of(target) == sha256:
        print(f"  Already present and verified: {target.name}")
        return
    target.parent.mkdir(parents=True, exist_ok=True)
    partial = target.with_name(target.name + ".part")
    digest = hashlib.sha256()
    received = 0
    if partial.is_file():
        received = partial.stat().st_size
        if received > size:
            partial.unlink()
            received = 0
        else:
            with partial.open("rb") as handle:
                while block := handle.read(CHUNK):
                    digest.update(block)
    if received == size:
        finish_download(partial, target, digest.hexdigest(), sha256)
        return

    request = urllib.request.Request(url, headers={"User-Agent": "desktop-companion-installer"})
    if received:
        request.add_header("Range", f"bytes={received}-")
        print(f"  Resuming {target.name} at {received / 1e6:,.0f} MB")
    with urllib.request.urlopen(request, timeout=60) as response:
        if received and response.status != 206:
            # The server ignored the resume request, so start from the beginning.
            digest, received = hashlib.sha256(), 0
        with partial.open("ab" if received else "wb") as out:
            shown = 0.0
            while block := response.read(CHUNK):
                received += len(block)
                if received > size:
                    raise SystemExit(f"{target.name} is larger than expected. Stopped.")
                digest.update(block)
                out.write(block)
                now = time.monotonic()
                if now - shown >= 3 or received == size:
                    print(
                        f"  {target.name}: {received / 1e6:,.0f} of {size / 1e6:,.0f} MB"
                        f" ({received * 100 // size}%)",
                        flush=True,
                    )
                    shown = now
    if received != size:
        raise SystemExit(
            f"The download of {target.name} stopped early. Run install.py again to resume."
        )
    finish_download(partial, target, digest.hexdigest(), sha256)


def install_python_packages() -> None:
    venv = ROOT / ".venv"
    python = venv / "Scripts" / "python.exe"
    if not python.is_file():
        print("  Creating a private Python environment (.venv)")
        subprocess.run([sys.executable, "-m", "venv", str(venv)], check=True)
    subprocess.run(
        [
            str(python),
            "-m",
            "pip",
            "install",
            "--disable-pip-version-check",
            "--no-input",
            "--require-hashes",
            "--only-binary=:all:",
            "-r",
            str(ROOT / "requirements.txt"),
        ],
        check=True,
    )


def install_engine() -> None:
    folder = ROOT / ENGINE["folder"]
    marker = folder / ".verified"
    if (folder / "llama-server.exe").is_file() and marker.is_file():
        if marker.read_text(encoding="utf-8").strip() == ENGINE["sha256"]:
            print("  Already installed.")
            return
    archive = folder.parent / Path(ENGINE["url"]).name
    download(ENGINE["url"], archive, ENGINE["sha256"], ENGINE["size"])
    staging = folder.with_name(folder.name + ".staging")
    if staging.exists():
        shutil.rmtree(staging)
    with zipfile.ZipFile(archive) as bundle:
        for member in bundle.infolist():
            name = member.filename
            if name.startswith(("/", "\\")) or ":" in name or ".." in Path(name).parts:
                raise SystemExit(f"Unsafe path in the engine archive: {name}")
        bundle.extractall(staging)
    servers = list(staging.rglob("llama-server.exe"))
    if len(servers) != 1:
        raise SystemExit("llama-server.exe was not found in the engine archive.")
    if folder.exists():
        shutil.rmtree(folder)
    shutil.move(str(servers[0].parent), str(folder))
    if staging.exists():
        shutil.rmtree(staging)
    marker.write_text(ENGINE["sha256"] + "\n", encoding="utf-8")
    archive.unlink()
    print("  Installed to", folder.relative_to(ROOT))


def remember_model(model_file: str) -> None:
    settings_file = ROOT / "settings.json"
    settings = {}
    if settings_file.is_file():
        settings = json.loads(settings_file.read_text(encoding="utf-8"))
    settings["model"] = model_file
    settings_file.write_text(json.dumps(settings, indent=2) + "\n", encoding="utf-8")


def main() -> None:
    parser = argparse.ArgumentParser(description="Set up the desktop companion.")
    parser.add_argument(
        "--small", action="store_true", help="use the faster Qwen3.5 2B model instead of 4B"
    )
    args = parser.parse_args()
    if sys.platform != "win32" or platform.machine().lower() not in ("amd64", "x86_64"):
        raise SystemExit("This companion is built for Windows on x64.")
    if sys.version_info[:2] != (3, 13):
        raise SystemExit("Python 3.13 is required; the pinned packages are built for it.")
    model = MODELS["2b" if args.small else "4b"]

    print("1/4 Python packages")
    install_python_packages()
    print("2/4 AI engine:", ENGINE["label"])
    install_engine()
    print("3/4 Language model:", model["label"])
    download(model["url"], ROOT / model["file"], model["sha256"], model["size"])
    remember_model(model["file"])
    print("4/4 Voice: Kokoro-82M")
    for item in VOICE_FILES:
        download(item["url"], ROOT / item["file"], item["sha256"], item["size"])
    print("\nDone. Start the companion with Companion.cmd")


if __name__ == "__main__":
    main()
