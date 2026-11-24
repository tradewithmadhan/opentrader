#!/usr/bin/env python3
"""Build scripts.json from the *.js files in this folder.

The JSON is the single file a plugin fetches and sends with `data.scripts` (Plugin API 8):

    {"schema":1, "scripts":[{"id":..., "name":..., "kind":..., "source":...}, ...]}

Each .js file is an OakScript study. The id comes from the file name (stable: do not rename a file
that is already on users' charts), the name and kind from its `indicator(...)` / `strategy(...)` call.

    python generate.py                 # writes ./scripts.json
    python generate.py -o ../plugins/zerodhaenctoken/scripts/scripts.json
"""
import argparse
import json
import re
import shutil
import subprocess
from pathlib import Path

HERE = Path(__file__).resolve().parent
REPO = HERE.parent
CHECKER = REPO / "node_modules" / "oakscriptjs" / "bin" / "oakscript.mjs"
ID_RE = re.compile(r"^[A-Za-z0-9._-]{1,64}$")
TITLE_RE = re.compile(r'\b(?:indicator|strategy)\s*\(\s*["\']([^"\']+)["\']')
MAX_SOURCE = 512 * 1024
MAX_SCRIPTS = 50


def check_scripts(paths):
    """Compile and run every script with the repo's OakScript checker.

    `--count 1500 --timeframe D` gives the studies enough history for their warm-ups and, for the
    NSE calendar studies, real expiry dates. Exit 1 means an error or a failed check; 2 means
    warnings only. With --check, only 0 is accepted so a broken study never reaches the manifest.
    """
    node = shutil.which("node")
    if not node or not CHECKER.exists():
        raise SystemExit("--check needs node and node_modules/oakscriptjs (run npm ci first)")
    for path in paths:
        result = subprocess.run(
            [node, str(CHECKER), "check", str(path), "--count", "1500", "--timeframe", "D"],
            capture_output=True, text=True,
        )
        output = (result.stdout + result.stderr).strip()
        if result.returncode != 0:
            raise SystemExit(f"{path.name}: OakScript check failed (exit {result.returncode}):\n{output}")
        last = next((line for line in reversed(output.splitlines()) if line.startswith("result:")), output)
        print(f"  checked {path.name}: {last}")


def build():
    scripts = []
    for path in sorted(HERE.glob("*.js")):
        sid = path.stem
        if not ID_RE.match(sid):
            raise SystemExit(f"{path.name}: id {sid!r} must be letters, digits, . _ - and at most 64 characters")
        source = path.read_text(encoding="utf-8").strip()
        if not source:
            raise SystemExit(f"{path.name}: empty script")
        if len(source.encode("utf-8")) > MAX_SOURCE:
            raise SystemExit(f"{path.name}: source exceeds {MAX_SOURCE} bytes")
        match = TITLE_RE.search(source)
        name = match.group(1) if match else sid
        kind = "strategy" if re.search(r"\bstrategy\s*\(", source) else "indicator"
        scripts.append({"id": sid, "name": name, "kind": kind, "source": source})
    if len(scripts) > MAX_SCRIPTS:
        raise SystemExit(f"at most {MAX_SCRIPTS} scripts allowed, found {len(scripts)}")
    ids = [s["id"] for s in scripts]
    if len(ids) != len(set(ids)):
        raise SystemExit("duplicate script ids")
    return {"schema": 1, "scripts": scripts}


def main():
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("-o", "--output", type=Path, default=HERE / "scripts.json",
                        help="where to write the manifest (default: ./scripts.json)")
    parser.add_argument("--check", action="store_true",
                        help="compile and run every .js with the OakScript checker before writing the manifest")
    args = parser.parse_args()
    if args.check:
        check_scripts(sorted(HERE.glob("*.js")))
    manifest = build()
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(json.dumps(manifest, indent=2, ensure_ascii=False) + "\n", encoding="utf-8")
    print(f"{args.output}: {len(manifest['scripts'])} scripts")
    for script in manifest["scripts"]:
        print(f"  {script['id']:24} {script['kind']:10} {script['name']}")


if __name__ == "__main__":
    main()
