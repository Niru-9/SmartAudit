"""Unit tests for backend parsing helpers (no network, no model needed).

Run:  py tests/test_parsing.py
"""

from __future__ import annotations

import os
import sys

# Make the repo root importable when run as `py tests/test_parsing.py`.
sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from backend.main import _extract_solidity_code, _strip_prose_fence  # noqa: E402

FENCE = "```"


def check(name: str, got, expected) -> bool:
    ok = got == expected
    print(f"[{'PASS' if ok else 'FAIL'}] {name}")
    if not ok:
        print(f"   got:      {got!r}")
        print(f"   expected: {expected!r}")
    return ok


def main() -> int:
    results = []

    # 1. Whole answer wrapped in a prose fence -> unwrap.
    wrapped = f"{FENCE}markdown\n## Head\nbody line\n{FENCE}"
    results.append(check("strip prose fence (markdown)", _strip_prose_fence(wrapped), "## Head\nbody line"))

    # 2. A real embedded code block must NOT be unwrapped.
    embedded = f"Text\n{FENCE}solidity\ncontract A {{}}\n{FENCE}\nmore"
    results.append(check("keep embedded code block", _strip_prose_fence(embedded), embedded))

    # 3. Plain text unchanged.
    results.append(check("plain text unchanged", _strip_prose_fence("hello"), "hello"))

    # 4. md fence unwrapped too.
    wrapped_md = f"{FENCE}md\nhello\n{FENCE}"
    results.append(check("strip prose fence (md)", _strip_prose_fence(wrapped_md), "hello"))

    # 4b. Unterminated prose fence (no closing fence) -> strip the opener.
    unterminated = f"{FENCE}markdown\n## Head\nbody with no close"
    results.append(check("strip unterminated prose fence", _strip_prose_fence(unterminated), "## Head\nbody with no close"))

    # 4c. Prose opener + trailing fence + trailing prose -> strip opener and lone closer.
    messy = f"{FENCE}markdown\ncontent here\n{FENCE}\ntrailing note"
    results.append(check("strip messy prose wrapper", _strip_prose_fence(messy), "content here\n```\ntrailing note"))

    # 5. Extract solidity block + notes.
    src = f"## Notes\nchanged x\n{FENCE}solidity\ncontract B {{}}\n{FENCE}"
    code, notes = _extract_solidity_code(src)
    results.append(check("extract code", code, "contract B {}"))
    results.append(check("extract notes", notes, "## Notes\nchanged x"))

    # 6. No code block -> code None, notes = whole text.
    code2, notes2 = _extract_solidity_code("just prose")
    results.append(check("no code -> None", code2, None))
    results.append(check("no code -> prose notes", notes2, "just prose"))

    # 7. Generic fence fallback.
    code3, _ = _extract_solidity_code(f"intro\n{FENCE}\ncontract C {{}}\n{FENCE}")
    results.append(check("generic fence fallback", code3, "contract C {}"))

    print()
    if all(results):
        print("ALL PARSING TESTS PASSED")
        return 0
    print(f"{results.count(False)} TEST(S) FAILED")
    return 1


if __name__ == "__main__":
    sys.exit(main())
