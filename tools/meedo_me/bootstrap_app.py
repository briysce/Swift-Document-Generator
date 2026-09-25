"""Build the Meedo-Me app from Jan, reproducibly.

Meedo-Me's app is a fork of Jan (Menlo Research, Apache-2.0). This produces it
from a pinned upstream commit in two reviewable steps:

  1. import  — Jan exactly as published, one commit, provenance in the message.
  2. rename  — the product becomes Meedo-Me: its name, its app identifier and
               the text users read. Nothing else.
  3. park CI — Jan's workflows moved where they do not run: they publish Jan,
               need Jan's secrets, and would spend this repository's minutes.

Why a script, committed here, instead of edits in a working copy: the working
copy lived in an ephemeral scratchpad and would have vanished with the
container. A script is the work in a form that cannot be lost, and anyone can
rerun it against a newer Jan to see exactly what Meedo-Me changes.

What the rename deliberately leaves alone — a blind find-and-replace of "Jan"
breaks the app:

  * strings the code compares against: 'Jan Browser MCP' is filtered by name,
    'What is Jan?' gates onboarding. Renaming one side breaks the other.
  * other people's names: janhq/Jan-Code-4b-Gguf and every model called
    Jan-something are Hugging Face repositories; renaming them breaks downloads.
  * internal package scopes (@janhq/*) and the jan-cli binary: imports and
    tooling reference them everywhere, and users never see them.
  * the Jan logo images: they are Menlo Research's mark, and Apache-2.0 grants
    no trademark rights, so they must be replaced — by a Meedo-Me logo, which
    is a design decision this script does not invent.

Apache-2.0 obligations kept: LICENSE untouched, a NOTICE crediting Menlo
Research, and the rename commit states what changed (section 4b).

Usage:
    python tools/meedo_me/bootstrap_app.py --jan <jan clone> --out <dir>
        [--push <git url>]
"""

from __future__ import annotations

import argparse
import json
import re
import shutil
import subprocess
from pathlib import Path

UPSTREAM = "https://github.com/menloresearch/jan.git"
PINNED = "af0ddc3"
PRODUCT = "Meedo-Me"
IDENTIFIER = "com.briysce.meedome"
TAGLINE = "Meedo's Codified Likeness Utility"
# Left out of the import, and said so in its commit. Jan's documentation site
# and blog are 235 MB of a 262 MB snapshot — the app itself is 27 MB — and
# nothing in the build references them. They are Jan's marketing, about Jan and
# carrying Jan's marks, so they are not Meedo-Me's to ship; left in, they made
# the first push too large for the proxy.
EXCLUDE = ("docs", "demo.gif", "JanBanner.png")

# "Jan" as the product's name, not as part of someone else's model name
# (Jan-Nano, Jan-v2-VL, Jan-Code) and not the browser extension's server name,
# which the code filters on.
_PRODUCT_WORD = re.compile(r"\bJan\b(?![-_]\w)(?! Browser)")


TRAILERS: list[str] = []


def _run(cmd: list[str], cwd: Path | None = None) -> str:
    if cmd[:2] == ["git", "commit"] and TRAILERS and "-m" in cmd:
        i = cmd.index("-m") + 1
        cmd = cmd[:i] + [cmd[i].rstrip() + "\n\n" + "\n".join(TRAILERS)] + cmd[i + 1:]
    return subprocess.run(cmd, cwd=cwd, check=True, capture_output=True, text=True).stdout


def import_upstream(jan: Path, out: Path) -> None:
    """Jan exactly as published at the pinned commit — step one, nothing changed."""
    if out.exists():
        shutil.rmtree(out)
    out.mkdir(parents=True)
    archive = subprocess.run(
        ["git", "archive", "--format=tar", PINNED, "--", "."] + [f":!{x}" for x in EXCLUDE],
        cwd=jan, check=True, capture_output=True,
    ).stdout
    subprocess.run(["tar", "-x", "-C", str(out)], input=archive, check=True)
    _run(["git", "init", "-q", "-b", "main"], cwd=out)
    _run(["git", "add", "-A"], cwd=out)
    _run(["git", "commit", "-q", "-m",
          f"Import Jan {PINNED} by Menlo Research (Apache-2.0)\n\n"
          f"Unmodified snapshot of {UPSTREAM} at {PINNED}, the base Meedo-Me is\n"
          "built on. Every change after this commit is Meedo-Me's.\n\n"
          f"Left out: {', '.join(EXCLUDE)}. Jan's documentation site and blog are\n"
          "235 MB of the snapshot against 27 MB for the app, nothing in the build\n"
          "uses them, and they are Jan's marketing, carrying Jan's marks."], cwd=out)


_KEY_VALUE = re.compile(r'^(\s*"(?:[^"\\]|\\.)*"\s*:\s*)("(?:[^"\\]|\\.)*")(.*)$', re.S)
_ARRAY_ITEM = re.compile(r'^(\s*)("(?:[^"\\]|\\.)*")(\s*,?\s*)$', re.S)


def _rename_value_line(line: str) -> str:
    """Rename the product inside a JSON value on this line, touching nothing else."""
    m = _KEY_VALUE.match(line) or _ARRAY_ITEM.match(line)
    if not m:
        return line
    return m.group(1) + _PRODUCT_WORD.sub(PRODUCT, m.group(2)) + m.group(3)


def _rename_json_values(obj):
    if isinstance(obj, str):
        return _PRODUCT_WORD.sub(PRODUCT, obj)
    if isinstance(obj, list):
        return [_rename_json_values(v) for v in obj]
    if isinstance(obj, dict):
        return {k: _rename_json_values(v) for k, v in obj.items()}
    return obj


def rename(out: Path) -> dict:
    """Make the product Meedo-Me. Returns what changed, for the commit and the report."""
    changed: dict[str, int] = {}

    conf = out / "src-tauri" / "tauri.conf.json"
    c = json.loads(conf.read_text(encoding="utf-8"))
    c["productName"] = PRODUCT
    c["identifier"] = IDENTIFIER
    for w in (c.get("app", {}) or {}).get("windows", []) or []:
        if isinstance(w.get("title"), str):
            w["title"] = _PRODUCT_WORD.sub(PRODUCT, w["title"])
    conf.write_text(json.dumps(c, indent=2) + "\n", encoding="utf-8")
    changed["src-tauri/tauri.conf.json"] = 1

    root_pkg = out / "package.json"
    p = json.loads(root_pkg.read_text(encoding="utf-8"))
    p["name"] = "meedo-me"
    root_pkg.write_text(json.dumps(p, indent=2) + "\n", encoding="utf-8")
    changed["package.json"] = 1

    # Translations: only values, never keys — keys are identifiers the code looks
    # up. Edited in place line by line rather than re-serialized: rewriting the
    # JSON reformatted 20 unrelated lines and buried the rename in noise. The
    # parsed result is then checked to equal the intended rename exactly.
    for f in sorted((out / "web-app" / "src" / "locales").rglob("*.json")):
        raw = f.read_text(encoding="utf-8")
        before = json.loads(raw)
        edited = "".join(_rename_value_line(line) for line in raw.splitlines(keepends=True))
        if edited == raw:
            continue
        if json.loads(edited) != _rename_json_values(before):
            raise RuntimeError(f"in-place rename of {f} diverged from the intended rename")
        f.write_text(edited, encoding="utf-8")
        changed[str(f.relative_to(out))] = 1

    # Alt text on images: read aloud by screen readers, so it is user-facing text.
    for f in sorted((out / "web-app" / "src").rglob("*.tsx")):
        s = f.read_text(encoding="utf-8")
        t = s.replace('alt="Jan Logo"', f'alt="{PRODUCT} Logo"')
        if t != s:
            f.write_text(t, encoding="utf-8")
            changed[str(f.relative_to(out))] = s.count('alt="Jan Logo"')

    (out / "NOTICE").write_text(
        f"{PRODUCT} — {TAGLINE}\n\n"
        "This product includes software developed by Menlo Research\n"
        f"(https://menlo.ai): Jan, {UPSTREAM} at {PINNED},\n"
        "licensed under the Apache License, Version 2.0 (see LICENSE).\n\n"
        f"{PRODUCT} modifies that software. Its changes are the commits after the\n"
        "import commit in this repository's history.\n",
        encoding="utf-8",
    )
    changed["NOTICE"] = 1

    readme = out / "README.md"
    upstream_readme = readme.read_text(encoding="utf-8")
    (out / "README.upstream.md").write_text(upstream_readme, encoding="utf-8")
    readme.write_text(
        f"# {PRODUCT}\n\n*{TAGLINE}* — project manager, memory and reviewer for our products.\n\n"
        f"{PRODUCT} is built on [Jan]({UPSTREAM.removesuffix('.git')}) by Menlo Research "
        "(Apache-2.0); see `NOTICE`. Jan's own README is kept as `README.upstream.md`.\n\n"
        "## What makes it Meedo-Me\n\n"
        "Meedo-Me's memory, reviewer and advisor live in the Swift Document Generator "
        "repository (`tools/logo_vectorizer/meedo_*.py`) and are served to this app over "
        "MCP. The app is the face; that is the mind.\n\n"
        "## Not renamed, on purpose\n\n"
        "- Strings the code compares against (`'Jan Browser MCP'`, `'What is Jan?'`).\n"
        "- Model names that are other people's Hugging Face repositories (`Jan-Nano`, `Jan-Code`, ...).\n"
        "- Internal package scopes (`@janhq/*`) and the `jan-cli` binary.\n"
        "- **Logo images are still Jan's and must be replaced** — Apache-2.0 grants no trademark "
        "rights. Waiting on a Meedo-Me logo.\n",
        encoding="utf-8",
    )
    changed["README.md"] = 1
    return changed


def commit_rename(out: Path, changed: dict) -> None:
    _run(["git", "add", "-A"], cwd=out)
    locales = sum(1 for k in changed if "/locales/" in k)
    _run(["git", "commit", "-q", "-m",
          f"Rename the product to {PRODUCT}\n\n"
          f"productName {PRODUCT}, identifier {IDENTIFIER}, window titles, the\n"
          f"product name in {locales} translation files (values only, never keys),\n"
          "image alt text, NOTICE crediting Menlo Research, and a README.\n\n"
          "Deliberately not renamed: strings the code compares against, other\n"
          "people's model names, internal package scopes, the CLI binary. The Jan\n"
          "logo images remain and must be replaced — Apache-2.0 grants no\n"
          "trademark rights."], cwd=out)


def park_upstream_ci(out: Path) -> int:
    """Stop Jan's CI from running in Meedo-Me's repository.

    Jan ships 32 workflows: 9 run on every push and 22 need Jan's secrets
    (Azure code signing, Cloudflare, AWS). In a private repository, the first
    push would launch them, spend its metered Actions minutes — macOS runners
    at ten times the rate — and fail on credentials it does not have, all to
    build and publish Jan rather than Meedo-Me. They are moved where GitHub does
    not run them, kept as reference for Meedo-Me's own pipeline.
    """
    src = out / ".github" / "workflows"
    if not src.is_dir():
        return 0
    dst = out / ".github" / "workflows-upstream"
    dst.mkdir(parents=True, exist_ok=True)
    moved = 0
    for f in sorted(src.iterdir()):
        if f.suffix in (".yml", ".yaml"):
            f.rename(dst / f.name)
            moved += 1
    (dst / "README.md").write_text(
        "# Jan's CI, parked\n\n"
        "These are Jan's build, test and release workflows, moved out of\n"
        "`.github/workflows/` so they do not run here. They publish Jan, need Jan's\n"
        "signing and hosting secrets, and would spend this repository's Actions\n"
        "minutes failing. Meedo-Me's pipeline is to be written from them.\n",
        encoding="utf-8",
    )
    _run(["git", "add", "-A"], cwd=out)
    _run(["git", "commit", "-q", "-m",
          f"Park Jan's CI workflows\n\n"
          f"Moved {moved} workflows to .github/workflows-upstream/, where GitHub\n"
          "does not run them. Nine ran on every push and 22 need Jan's secrets\n"
          "(Azure signing, Cloudflare, AWS); in a private repository they would\n"
          "spend metered Actions minutes failing, while trying to publish Jan."], cwd=out)
    return moved


def main(argv: list[str] | None = None) -> int:
    ap = argparse.ArgumentParser(description=__doc__.split("\n")[0])
    ap.add_argument("--jan", required=True, type=Path, help="a clone of Jan containing the pinned commit")
    ap.add_argument("--out", required=True, type=Path)
    ap.add_argument("--push", default="", help="git URL of the Meedo-Me repository")
    ap.add_argument("--trailer", action="append", default=[],
                    help="line appended to every commit message (repeatable)")
    a = ap.parse_args(argv)
    TRAILERS[:] = a.trailer
    import_upstream(a.jan, a.out)
    changed = rename(a.out)
    commit_rename(a.out, changed)
    parked = park_upstream_ci(a.out)
    print(f"built {a.out}: {len(changed)} file(s) renamed, {parked} upstream workflow(s) parked")
    if a.push:
        _run(["git", "remote", "add", "origin", a.push], cwd=a.out)
        _run(["git", "push", "-u", "origin", "main"], cwd=a.out)
        print(f"pushed to {a.push}")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
