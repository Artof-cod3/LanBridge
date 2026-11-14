"""Static compatibility audit: what Python version does this tree actually need?

Run it on any interpreter; it parses every module against older grammars and
flags APIs that only exist in newer releases. It is not a substitute for
running the code on that version - it is the cheap half of the answer, and it
fails loudly instead of guessing.

    python scripts/check_pycompat.py            # audit against 3.9, 3.10, 3.11
    python scripts/check_pycompat.py --target 3.9
"""

from __future__ import annotations

import argparse
import ast
import io
import re
import sys
import tokenize
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
PACKAGE = ROOT / "lanbridge"

TARGETS = [(3, 9), (3, 10), (3, 11)]

# (pattern, version that introduced it, what to look for)
SUSPECT_API = [
    (r"\bimport\s+tomllib\b|\bfrom\s+tomllib\b", (3, 11), "tomllib"),
    (r"\bzip\([^)]*strict\s*=", (3, 10), "zip(strict=)"),
    (r"\bExceptionGroup\b", (3, 11), "ExceptionGroup"),
    (r"\bexcept\s*\*", (3, 11), "except*"),
    (r"\btyping\.Self\b|\bfrom\s+typing\s+import\s+.*\bSelf\b", (3, 11), "typing.Self"),
    (r"\bdatetime\.UTC\b", (3, 11), "datetime.UTC"),
    (r"\bitertools\.pairwise\b|\bpairwise\b", (3, 10), "itertools.pairwise"),
    (r"\basyncio\.timeout\b", (3, 11), "asyncio.timeout"),
    (r"\bStrEnum\b", (3, 11), "enum.StrEnum"),
    (r"\bTaskGroup\b", (3, 11), "asyncio.TaskGroup"),
    # API additions the grammar cannot see. The first entry is a real bug this
    # audit missed until a 3.9 interpreter was actually run: Path.write_text's
    # newline= keyword arrived in 3.10 and raised TypeError at runtime.
    (r"\.write_text\([^)]*newline\s*=", (3, 10), "Path.write_text(newline=)"),
    (r"\.read_text\([^)]*newline\s*=", (3, 10), "Path.read_text(newline=)"),
    (r"@dataclass\([^)]*slots\s*=", (3, 10), "@dataclass(slots=)"),
    (r"\bParamSpec\b", (3, 10), "typing.ParamSpec"),
    (r"\bTypeAlias\b", (3, 10), "typing.TypeAlias"),
    (r"\.hardlink_to\(", (3, 10), "Path.hardlink_to"),
]

# `tomllib` is legitimate when the module also handles the tomli fallback.
GUARDED = {
    "lanbridge/settings.py": ["tomllib"],
}


def modules() -> list[Path]:
    return sorted(p for p in PACKAGE.rglob("*.py") if "__pycache__" not in p.parts)


def strip_comments(source: str) -> str:
    """Remove comments so prose about an API is not mistaken for its use."""
    try:
        tokens = list(tokenize.generate_tokens(io.StringIO(source).readline))
    except (tokenize.TokenError, IndentationError):
        return source
    kept = [tok for tok in tokens if tok.type != tokenize.COMMENT]
    try:
        return tokenize.untokenize(kept)
    except (ValueError, IndexError):
        return source


def syntax_floor(path: Path, targets: list[tuple[int, int]]) -> tuple[int, int] | None:
    """Lowest target whose grammar accepts this file; None if none do."""
    source = path.read_text(encoding="utf-8")
    for target in targets:
        try:
            ast.parse(source, filename=str(path), feature_version=target)
        except SyntaxError:
            continue
        return target
    return None


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--target", default=None, help="single version to demand, e.g. 3.9")
    args = parser.parse_args()

    targets = TARGETS
    if args.target:
        major, minor = (int(x) for x in args.target.split("."))
        targets = [(major, minor)]

    print(f"Running interpreter: {sys.version.split()[0]}")
    print(f"Auditing {len(modules())} modules against: "
          + ", ".join(f"{a}.{b}" for a, b in targets))
    print("-" * 68)

    problems: list[str] = []
    worst = targets[0]

    for path in modules():
        rel = path.relative_to(ROOT).as_posix()
        floor = syntax_floor(path, targets)
        source = strip_comments(path.read_text(encoding="utf-8"))

        if floor is None:
            problems.append(f"{rel}: syntax rejected by every target grammar")
            print(f"  FAIL {rel}: syntax needs a newer Python than {targets[0][0]}.{targets[0][1]}")
            continue

        if floor > worst:
            worst = floor

        suspect_hits = []
        guarded = GUARDED.get(rel, [])
        for pattern, version, label in SUSPECT_API:
            if not re.search(pattern, source, re.MULTILINE):
                continue
            if label in guarded:
                continue  # handled explicitly with a fallback import
            suspect_hits.append((version, label))

        status = "ok  "
        if suspect_hits:
            status = "FLAG"
            for version, label in suspect_hits:
                problems.append(f"{rel}: {label} requires {version[0]}.{version[1]}")
        print(f"  {status} {rel:<44} syntax>={floor[0]}.{floor[1]}"
              + ("" if not suspect_hits else "  " + ", ".join(l for _, l in suspect_hits)))

    print("-" * 68)
    print(f"Minimum version implied by syntax: {worst[0]}.{worst[1]}")

    if problems:
        print("\nPROBLEMS:")
        for p in problems:
            print(f"  - {p}")
        print("\nThe claim is NOT supported. Either fix these or raise requires-python.")
        return 1

    print("No grammar or API obstruction found for the requested range.")
    print("Static only and heuristic: it cannot see every API change. The real")
    print("evidence is the container matrix in docs/VERIFIED.md.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
