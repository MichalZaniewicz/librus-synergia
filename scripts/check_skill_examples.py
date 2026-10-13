"""Type-check the code in the Claude Code skill against the library.

Extracts every complete program (a ```python block that runs
`asyncio.run(...)`) from the skill and runs `mypy --strict` on them, so the
skill can't drift from the real API. Shorter snippets in the prose are
fragments and are skipped.

    python scripts/check_skill_examples.py
"""

from __future__ import annotations

import ast
import re
import subprocess
import sys
import tempfile
from pathlib import Path

ROOT = Path(__file__).resolve().parent.parent
SKILL = ROOT / "plugins" / "librus-synergia" / "skills" / "librus-synergia"
FILES = [SKILL / "SKILL.md", SKILL / "reference" / "examples.md"]


def _is_script(code: str) -> bool:
    """A complete program: it parses, uses the library and runs itself."""
    try:
        ast.parse(code)
    except SyntaxError:
        return False
    return "librus_synergia" in code and "asyncio.run(" in code


def main() -> int:
    blocks = [
        (path, code)
        for path in FILES
        for code in re.findall(r"```python\n(.*?)```", path.read_text(encoding="utf-8"), re.S)
    ]
    scripts = [(path, code) for path, code in blocks if _is_script(code)]
    if not scripts:
        print("No example scripts found in the skill.")
        return 1
    with tempfile.TemporaryDirectory() as tmp:
        files = []
        for number, (path, code) in enumerate(scripts, 1):
            target = Path(tmp) / f"example_{number:02d}_{path.stem.lower()}.py"
            target.write_text(code, encoding="utf-8")
            files.append(str(target))
        print(f"Type-checking {len(files)} example scripts from the skill...")
        result = subprocess.run(
            [sys.executable, "-m", "mypy", "--strict", "--no-incremental", *files],
            cwd=tmp,
            check=False,
        )
    return result.returncode


if __name__ == "__main__":
    raise SystemExit(main())
