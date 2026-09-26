"""Font corpus acquisition — you can only recognize a face you have.

Why
---
`glyph_match` identifies a wordmark by rendering candidate glyphs and comparing
them to the element. That works well: against a 103-font local corpus it read
SUPPLY, SWIFT and CARGO correctly down to 0.18x scale and JPEG q40.

But it is bounded by what is installed. The Swift "SUPPLY" wordmark is set in a
squarish techno face (the Eurostile / Microgramma family), and with only the
system fonts present the best available match scored 0.608 — an honest miss, not
a bug. No amount of matcher tuning fixes a face that is not on disk.

So this module fetches a real corpus. Google Fonts publishes ~1,950 families
under open licences, covering most of the styles customer logos are actually
set in. Families are fetched on demand into a local cache and picked up
automatically by `glyph_match.font_corpus`.

Selection
---------
Downloading everything is slow and mostly wasted — matching cost scales with
corpus size, and handwriting faces almost never appear in a logo lockup. So
fetching is category-aware and ordered by popularity, and callers can ask for
just the categories that matter to them. Sans Serif and Display alone cover the
overwhelming majority of wordmarks.

Everything here is best-effort: network failures, policy denials and malformed
responses all degrade to "fewer fonts cached", never an exception that reaches
the engine.
"""

from __future__ import annotations

import json
import re
import urllib.error
import urllib.request
from dataclasses import dataclass
from pathlib import Path

ROOT = Path(__file__).resolve().parents[2]
CACHE = ROOT / "tools" / "logo_vectorizer" / ".cache" / "fonts"

METADATA_URL = "https://fonts.google.com/metadata/fonts"
CSS_URL = "https://fonts.googleapis.com/css2?family={family}:wght@{weight}"
# Google content-negotiates the font format from the User-Agent. A browser UA
# gets WOFF/WOFF2, which PIL cannot open; an unrecognized UA gets plain TTF,
# which both PIL and fontTools read directly. So we deliberately do NOT
# masquerade as a browser here. WOFF is still accepted and converted below, in
# case the negotiation changes.
UA_TTF = "swift-document-generator-logo-engine/1.0 (+font matching corpus)"

LOGO_CATEGORIES = ("Sans Serif", "Display", "Serif", "Monospace")


@dataclass
class FamilyInfo:
    family: str
    category: str
    popularity: int


def _get(url: str, *, ua: str | None = None, timeout: int = 60) -> bytes | None:
    req = urllib.request.Request(url, headers={"User-Agent": ua or UA_TTF})
    try:
        with urllib.request.urlopen(req, timeout=timeout) as r:
            return r.read()
    except (urllib.error.URLError, OSError, ValueError):
        return None


def list_families() -> list[FamilyInfo]:
    """Every Google Fonts family, most popular first."""
    raw = _get(METADATA_URL, timeout=120)
    if not raw:
        return []
    text = raw.decode("utf-8", errors="replace")
    # The endpoint prefixes its JSON with an anti-JSON-hijacking guard.
    start = text.find("{")
    if start < 0:
        return []
    try:
        data = json.loads(text[start:])
    except json.JSONDecodeError:
        return []
    out: list[FamilyInfo] = []
    for f in data.get("familyMetadataList", []):
        fam = f.get("family")
        if not fam:
            continue
        out.append(
            FamilyInfo(
                family=fam,
                category=f.get("category") or "",
                popularity=int(f.get("popularity") or 10**6),
            )
        )
    out.sort(key=lambda f: f.popularity)
    return out


def fetch_family(family: str, weight: int = 700, dest: Path | None = None) -> Path | None:
    """Download one family at one weight. Returns the cached path, or None.

    Bold is the default because logo wordmarks are overwhelmingly set in a heavy
    weight, and matching the wrong weight of the right family scores worse than
    matching a neighbouring family at the right weight.
    """
    dest = dest or CACHE
    dest.mkdir(parents=True, exist_ok=True)
    safe = re.sub(r"[^A-Za-z0-9]+", "", family)
    out = dest / f"{safe}-{weight}.ttf"
    if out.is_file() and out.stat().st_size > 1024:
        return out

    css = _get(CSS_URL.format(family=family.replace(" ", "+"), weight=weight))
    if not css:
        return None
    m = re.search(r"src:\s*url\((https://[^)]+)\)", css.decode("utf-8", "replace"))
    if not m:
        return None
    blob = _get(m.group(1))
    if not blob or len(blob) < 1024:
        return None
    magic = blob[:4]
    if magic in (b"\x00\x01\x00\x00", b"true", b"ttcf", b"OTTO"):
        try:
            out.write_bytes(blob)
        except OSError:
            return None
        return out
    if magic in (b"wOFF", b"wOF2"):
        # Convert rather than discard: fontTools reads both and can write the
        # plain TTF that PIL needs for rendering candidates.
        try:
            import io as _io

            from fontTools.ttLib import TTFont

            f = TTFont(_io.BytesIO(blob))
            f.flavor = None
            f.save(str(out))
            return out if out.is_file() and out.stat().st_size > 1024 else None
        except Exception:
            return None
    return None


def ensure_corpus(
    limit: int = 400,
    categories: tuple[str, ...] = ("Sans Serif", "Display"),
    weight: int = 700,
    dest: Path | None = None,
    progress: bool = False,
) -> list[Path]:
    """Fetch the top `limit` families in `categories`, skipping what we have."""
    dest = dest or CACHE
    dest.mkdir(parents=True, exist_ok=True)
    fams = list_families()
    if not fams:
        return sorted(dest.glob("*.ttf"))

    wanted = [f for f in fams if not categories or f.category in categories][:limit]
    got: list[Path] = []
    for i, f in enumerate(wanted, 1):
        p = fetch_family(f.family, weight=weight, dest=dest)
        if p:
            got.append(p)
        if progress and i % 25 == 0:
            print(f"  {i}/{len(wanted)} families, {len(got)} cached", flush=True)
    return sorted(dest.glob("*.ttf"))


def cached_fonts(dest: Path | None = None) -> list[Path]:
    dest = dest or CACHE
    return sorted(dest.glob("*.ttf")) if dest.is_dir() else []


def main(argv: list[str] | None = None) -> int:
    import argparse

    p = argparse.ArgumentParser(description="Fetch a Google Fonts matching corpus")
    p.add_argument("--limit", type=int, default=400)
    p.add_argument("--weight", type=int, default=700)
    p.add_argument(
        "--categories",
        default="Sans Serif,Display",
        help="Comma list, or 'all'",
    )
    a = p.parse_args(argv)
    cats = (
        ()
        if a.categories.strip().lower() == "all"
        else tuple(c.strip() for c in a.categories.split(",") if c.strip())
    )
    fonts = ensure_corpus(
        limit=a.limit, categories=cats, weight=a.weight, progress=True
    )
    print(f"{len(fonts)} fonts cached in {CACHE}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())


__all__ = [
    "FamilyInfo",
    "CACHE",
    "list_families",
    "fetch_family",
    "ensure_corpus",
    "cached_fonts",
]
