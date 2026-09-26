"""Studio-finish existing Gemini restores (no new API call)."""
from pathlib import Path
import importlib.util

ROOT = Path(__file__).resolve().parents[1]
spec = importlib.util.spec_from_file_location(
    "restore_logo_gemini", ROOT / "scripts" / "restore_logo_gemini.py"
)
mod = importlib.util.module_from_spec(spec)
spec.loader.exec_module(mod)

SRC_DIR = ROOT / "customer_logos"
OUT_DIR = SRC_DIR / "gemini_restored"

def main() -> None:
    files = sorted(OUT_DIR.glob("*_gemini.png"))
    for i, gem in enumerate(files, 1):
        stem = gem.name[: -len("_gemini.png")]
        src = SRC_DIR / f"{stem}.png"
        if not src.is_file():
            print(f"[{i}/{len(files)}] missing source for {gem.name}", flush=True)
            continue
        print(f"[{i}/{len(files)}] refinalize {gem.name}", flush=True)
        mod.finalize(gem.read_bytes(), gem, src)


if __name__ == "__main__":
    main()
