#!/usr/bin/env python3
"""Fetch web brand references for logo QA cases via Serper.dev Images API.

Uses the same endpoint and query shape as the Flutter LogoFinder
(`mobile/lib/logo_finder.dart` → POST https://google.serper.dev/images).

    # Put SERPER_API_KEY in gitignored .env (never commit)
    python scripts/serper_logo_brand_refs.py
    python scripts/serper_logo_brand_refs.py --slugs arc,propak,gcm,trialta

Writes under `qa_logos/brand_refs/`:
  - catalog.json   (branding notes + Serper hit metadata)
  - <slug>/serper_*.{png,jpg,webp}  (downloaded candidates)
  - Meedo journal finding when --journal
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
import urllib.error
import urllib.request
from datetime import datetime, timezone
from pathlib import Path

ROOT = Path(__file__).resolve().parents[1]
OUT = ROOT / "qa_logos" / "brand_refs"
CATALOG = OUT / "catalog.json"

# Primary restore/test brands → official identity for Serper queries.
BRANDS: dict[str, dict] = {
    "arc": {
        "name": "ARC Resources Ltd.",
        "domain": "arcresources.com",
        "queries": [
            "ARC Resources Ltd logo high resolution transparent",
            "ARC Resources official brand logo vector",
            "ARC Resources company logo png",
        ],
        "official_urls": [
            "https://www.arcresources.com/wp-content/uploads/2022/02/arc_Logo_PNG_.png",
        ],
        "branding": {
            "wordmark": "ARC (serif, connected A–R) + RESOURCES LTD. (small teal sans)",
            "colors": ["#C92A35 red ARC", "#2D4A4E teal tagline/swoosh", "black field"],
            "must_keep": [
                "RESOURCES LTD. tagline letters to the right of ARC",
                "thin teal swoosh under ARC",
                "serif ARC with joined A–R legs",
            ],
        },
    },
    "propak": {
        "name": "PROPAK Energy Services",
        "domain": "propaksystems.com",
        "queries": [
            "PROPAK Energy Services logo high resolution transparent",
            "Propak Systems official brand logo vector",
            "PROPAK company logo png",
        ],
        "official_urls": [
            "https://www.propaksystems.com/img/logos/Propak-Energy-Services-Logo.webp",
            "https://www.propaksystems.com/img/logos/propak-logo-w240.webp",
        ],
        "branding": {
            "wordmark": "PROPAK (blue italic) over Energy Services; red rule; vessel icon",
            "colors": ["medium slate blue", "red rule + vessel outline"],
            "must_keep": [
                "i-dot on Services when present",
                "thin red horizontal rule under PROPAK",
                "blue arc + red vessel icon to the right",
            ],
        },
    },
    "gcm": {
        "name": "Gulf Coast Modification",
        "domain": "gulfcoastmod.com",
        "queries": [
            "Gulf Coast Modification logo high resolution transparent",
            "GCM Gulf Coast Modification official brand logo",
            "Gulf Coast Modification company logo png",
        ],
        "official_urls": [
            "https://www.gulfcoastmod.com/Themes/Default/Content/images/logo.png",
        ],
        "branding": {
            "wordmark": "TGC monogram + Gulf Coast / Modification serif with white outline",
            "colors": ["navy/blue T+G", "red nested C", "white outlines"],
            "must_keep": [
                "i-dots in Modification",
                "white-filled o counters (Coast / Modification)",
                "red C inside blue G monogram",
            ],
        },
    },
    "trialta": {
        "name": "Trialta Projects",
        "domain": "trialtaprojects.com",
        "queries": [
            "Trialta Projects logo high resolution transparent",
            "TRI ALTA Projects official brand logo",
            "Trialta Projects company logo png",
        ],
        "official_urls": [
            "https://trialtaprojects.com/wp-content/uploads/2012/04/ta-logo-1.png",
        ],
        "branding": {
            "wordmark": "square gray fan mark + TRI (gray) ALTA (lime) / PROJECTS",
            "colors": ["medium gray", "lime green ALTA", "silver PROJECTS"],
            "must_keep": [
                "two-tone TRI|ALTA color split (do not merge to one fill)",
                "square fan emblem left of wordmark",
            ],
        },
    },
    "whitecap": {
        "name": "Whitecap Resources Inc.",
        "domain": "wcap.ca",
        "queries": [
            "Whitecap Resources logo high resolution transparent",
            "Whitecap Resources Inc official brand logo",
            "Whitecap Resources company logo png",
        ],
        "official_urls": [
            "https://www.wcap.ca/application/themes/whitecap/images/whitecapLogo.png",
        ],
        "branding": {
            "wordmark": "wave mark + WHITECAP / RESOURCES INC (white on dark)",
            "colors": ["white on black"],
            "must_keep": ["wave crests as smooth strokes", "condensed sans WHITECAP"],
        },
    },
    "worley": {
        "name": "Worley",
        "domain": "worley.com",
        "queries": [
            "Worley logo high resolution transparent",
            "Worley official brand logo vector",
            "Worley company logo png",
        ],
        "official_urls": [
            "https://www.worley.com/-/media/images/worley/logos/global/header-logo-updated.png",
        ],
        "branding": {
            "wordmark": "multicolor ribbon globe + lowercase worley",
            "colors": ["red", "green", "cyan ribbons", "white wordmark"],
            "must_keep": ["ribbon globe as separate color bands", "lowercase worley"],
        },
    },
    "swift_orange": {
        "name": "Swift Supply",
        "domain": "",
        "queries": [
            "Swift Supply logo high resolution transparent",
            "Swift Supply official brand logo",
        ],
        "official_urls": [],
        "branding": {
            "wordmark": "internal Swift Supply document/orange lockup (repo assets/brand)",
            "colors": ["#CE4E30 orange", "black SUPPLY/bars"],
            "must_keep": ["P counters in SUPPLY", "orange SWIFT + bars"],
            "note": "Prefer assets/brand/swift_supply_logo_* over web search.",
        },
    },
}


def load_env() -> None:
    for path in (ROOT / ".env", ROOT / ".env.local", ROOT / "tools" / "logo_vectorizer" / ".env"):
        if not path.is_file():
            continue
        for raw in path.read_text(encoding="utf-8").splitlines():
            line = raw.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, _, v = line.partition("=")
            k, v = k.strip(), v.strip().strip('"').strip("'")
            if k and k not in os.environ:
                os.environ[k] = v


def _now() -> str:
    return datetime.now(timezone.utc).strftime("%Y-%m-%dT%H:%M:%SZ")


def serper_images(key: str, query: str, *, num: int = 8) -> list[dict]:
    payload = json.dumps({"q": query, "gl": "us", "hl": "en", "num": num}).encode()
    req = urllib.request.Request(
        "https://google.serper.dev/images",
        data=payload,
        headers={"X-API-KEY": key, "Content-Type": "application/json"},
        method="POST",
    )
    try:
        with urllib.request.urlopen(req, timeout=30) as resp:
            data = json.loads(resp.read().decode("utf-8"))
    except urllib.error.HTTPError as exc:
        detail = exc.read().decode("utf-8", errors="replace")[:200]
        print(f"  serper HTTP {exc.code}: {detail}", file=sys.stderr)
        return []
    except Exception as exc:  # noqa: BLE001
        print(f"  serper fail: {exc}", file=sys.stderr)
        return []
    out: list[dict] = []
    for item in data.get("images") or []:
        if not isinstance(item, dict):
            continue
        url = str(item.get("imageUrl") or "").strip()
        if not url.startswith("http"):
            continue
        out.append(
            {
                "imageUrl": url,
                "title": str(item.get("title") or "")[:160],
                "source": str(item.get("source") or "")[:120],
                "link": str(item.get("link") or "")[:200],
            }
        )
    return out


def download(url: str, dest: Path) -> bool:
    try:
        req = urllib.request.Request(
            url, headers={"User-Agent": "SwiftDocumentGeneratorBrandRef/1.0"}
        )
        with urllib.request.urlopen(req, timeout=30) as resp:
            body = resp.read()
            ctype = (resp.headers.get("Content-Type") or "").lower()
        if len(body) < 400:
            return False
        if "svg" in ctype or body[:4] == b"<svg":
            dest = dest.with_suffix(".svg")
        elif "webp" in ctype or url.lower().endswith(".webp"):
            dest = dest.with_suffix(".webp")
        elif "jpeg" in ctype or "jpg" in ctype or url.lower().endswith((".jpg", ".jpeg")):
            dest = dest.with_suffix(".jpg")
        else:
            dest = dest.with_suffix(".png")
        dest.parent.mkdir(parents=True, exist_ok=True)
        dest.write_bytes(body)
        return True
    except Exception:
        return False


def _slug_safe(s: str) -> str:
    return re.sub(r"[^a-z0-9]+", "_", s.lower()).strip("_")[:40]


def process_slug(slug: str, key: str, *, max_downloads: int = 4) -> dict:
    brand = BRANDS[slug]
    dest_dir = OUT / slug
    dest_dir.mkdir(parents=True, exist_ok=True)
    entry: dict = {
        "slug": slug,
        "name": brand["name"],
        "domain": brand.get("domain", ""),
        "branding": brand.get("branding", {}),
        "official": [],
        "serper": [],
        "ts": _now(),
    }

    for i, url in enumerate(brand.get("official_urls") or []):
        path = dest_dir / f"official_{i}"
        ok = download(url, path)
        # download may change suffix
        saved = next(dest_dir.glob(f"official_{i}.*"), None)
        entry["official"].append({"url": url, "ok": ok, "path": str(saved.relative_to(ROOT)) if saved else None})
        print(f"  official[{i}] {'ok' if ok else 'fail'} {url[:70]}", flush=True)

    if not key:
        print("  serper skipped (no SERPER_API_KEY)", flush=True)
        return entry

    seen: set[str] = set()
    hits: list[dict] = []
    for q in brand.get("queries") or []:
        print(f"  serper q={q!r}", flush=True)
        for hit in serper_images(key, q):
            u = hit["imageUrl"]
            if u in seen:
                continue
            seen.add(u)
            hits.append(hit)
        if len(hits) >= max_downloads * 2:
            break

    saved_n = 0
    for i, hit in enumerate(hits):
        if saved_n >= max_downloads:
            break
        path = dest_dir / f"serper_{saved_n}"
        if download(hit["imageUrl"], path):
            saved = next(dest_dir.glob(f"serper_{saved_n}.*"), None)
            hit = {**hit, "saved": str(saved.relative_to(ROOT)) if saved else None}
            entry["serper"].append(hit)
            saved_n += 1
            print(f"  serper saved[{saved_n}] {hit.get('title', '')[:60]}", flush=True)
        else:
            entry["serper"].append({**hit, "saved": None})

    return entry


def merge_catalog(entries: list[dict]) -> dict:
    if CATALOG.is_file():
        try:
            data = json.loads(CATALOG.read_text(encoding="utf-8"))
        except json.JSONDecodeError:
            data = {"version": 1, "brands": {}}
    else:
        data = {"version": 1, "brands": {}}
    data.setdefault("version", 1)
    data.setdefault("brands", {})
    for e in entries:
        data["brands"][e["slug"]] = e
    data["updated_at"] = _now()
    data["source"] = "serper.dev + official site URLs (LogoFinder-compatible)"
    return data


def journal_summary(entries: list[dict]) -> None:
    try:
        from tools.logo_vectorizer.meedo_journal import log as journal_log
    except Exception as exc:  # noqa: BLE001
        print(f"journal skipped: {exc}", file=sys.stderr)
        return
    names = ", ".join(e["slug"] for e in entries)
    serper_n = sum(len(e.get("serper") or []) for e in entries)
    official_n = sum(1 for e in entries for o in e.get("official") or [] if o.get("ok"))
    journal_log(
        agent="cursor",
        kind="finding",
        summary=(
            f"Brand-ref crawl for {names}: {official_n} official assets, "
            f"{serper_n} Serper hits. Catalog → qa_logos/brand_refs/catalog.json "
            "(Arc: keep RESOURCES LTD. teal tagline; Propak: red rule + Services i-dot; "
            "GCM: Modification i-dots + white o counters; Trialta: gray TRI / lime ALTA)."
        ),
        task=8,
        evidence={
            "catalog": "qa_logos/brand_refs/catalog.json",
            "slugs": [e["slug"] for e in entries],
            "serper_hits": serper_n,
            "official_ok": official_n,
        },
        images_looked_at=True,
        source="serper_logo_brand_refs",
    )


def main(argv: list[str] | None = None) -> int:
    p = argparse.ArgumentParser(description=__doc__)
    p.add_argument(
        "--slugs",
        default="arc,propak,gcm,trialta,whitecap,worley",
        help="Comma-separated case slugs (default: main restore brands)",
    )
    p.add_argument("--max-downloads", type=int, default=4)
    p.add_argument("--journal", action="store_true", help="Append Meedo journal finding")
    p.add_argument(
        "--official-only",
        action="store_true",
        help="Skip Serper even if key present (use cached branding + official URLs)",
    )
    args = p.parse_args(argv)

    load_env()
    key = "" if args.official_only else (os.environ.get("SERPER_API_KEY") or "").strip()
    if not key and not args.official_only:
        print(
            "WARN: SERPER_API_KEY not set — downloading official URLs only.\n"
            "Add SERPER_API_KEY to gitignored .env (same key as Flutter LogoFinder).",
            file=sys.stderr,
        )

    OUT.mkdir(parents=True, exist_ok=True)
    entries: list[dict] = []
    for slug in [s.strip() for s in args.slugs.split(",") if s.strip()]:
        if slug not in BRANDS:
            print(f"unknown slug {slug!r}; known={sorted(BRANDS)}", file=sys.stderr)
            continue
        print(f"== {slug} ({BRANDS[slug]['name']})", flush=True)
        entries.append(process_slug(slug, key, max_downloads=args.max_downloads))

    catalog = merge_catalog(entries)
    CATALOG.write_text(json.dumps(catalog, indent=2) + "\n", encoding="utf-8")
    print(f"catalog → {CATALOG}", flush=True)

    if args.journal and entries:
        journal_summary(entries)
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
