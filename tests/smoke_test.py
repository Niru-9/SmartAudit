"""Smoke test for the Phase 1 Auditor backend.

Run with the server already up:
    py tests/smoke_test.py

It hits the local FastAPI backend (default http://127.0.0.1:8000), which in
turn calls the local Ollama model. No blockchain, no hosted model.
"""

from __future__ import annotations

import sys

import httpx

BASE = "http://127.0.0.1:8000"

SAMPLE = """// SPDX-License-Identifier: MIT
pragma solidity ^0.8.20;

contract VulnerableVault {
    mapping(address => uint256) private _balances;

    function deposit() external payable {
        _balances[msg.sender] += msg.value;
    }

    function withdraw(uint256 amount) external {
        require(_balances[msg.sender] >= amount, "insufficient");
        (bool ok, ) = msg.sender.call{value: amount}("");
        require(ok, "transfer failed");
        _balances[msg.sender] -= amount;
    }

    function balanceOf(address who) external view returns (uint256) {
        return _balances[who];
    }
}
"""


def main() -> int:
    with httpx.Client(timeout=240.0) as c:
        print("== /api/health ==")
        h = c.get(f"{BASE}/api/health")
        print(h.status_code, h.json())
        assert h.status_code == 200
        assert h.json()["status"] == "ok", "model should be available"
        tools = h.json().get("tools", {})
        assert tools.get("slither") == "available", f"slither should be available: {tools}"
        assert all(
            v == "not_integrated" for k, v in tools.items() if k not in ("slither", "solc", "solhint")
        ), f"no other analyzer should be integrated: {tools}"

        print("\n== /api/models ==")
        m = c.get(f"{BASE}/api/models")
        print(m.status_code, m.json())
        assert m.status_code == 200 and m.json()

        print("\n== /api/review ==")
        r = c.post(f"{BASE}/api/review", json={"source": SAMPLE, "model": "qwen2.5:3b"})
        print("status:", r.status_code, "elapsed:", r.json().get("elapsed_ms"), "ms")
        body = r.json()
        assert r.status_code == 200, body
        print("review length:", len(body["content"]))
        print("review preview:", body["content"][:300].replace("\n", " "))
        assert body["content"].strip()
        assert not body["content"].lstrip().startswith("```markdown"), (
            "review should not be wrapped in a ```markdown fence"
        )

        print("\n== /api/suggest ==")
        s = c.post(f"{BASE}/api/suggest", json={"source": SAMPLE, "model": "qwen2.5:3b"})
        sb = s.json()
        print("status:", s.status_code, "elapsed:", sb.get("elapsed_ms"), "ms")
        assert s.status_code == 200, sb
        print("notes length:", len(sb["notes"]))
        print("suggested_code present:", bool(sb.get("suggested_code")))
        if sb.get("suggested_code"):
            print("code preview:", sb["suggested_code"][:200].replace("\n", " "))

        print("\n== /api/chat ==")
        cht = c.post(
            f"{BASE}/api/chat",
            json={
                "source": SAMPLE,
                "question": "In one short paragraph, what is the main risk in withdraw()?",
                "model": "qwen2.5:3b",
            },
        )
        cb = cht.json()
        print("status:", cht.status_code, "elapsed:", cb.get("elapsed_ms"), "ms")
        assert cht.status_code == 200, cb
        print("reply preview:", cb["reply"][:300].replace("\n", " "))

        print("\n== /api/slither (local analyzer) ==")
        sl = c.post(f"{BASE}/api/slither", json={"source": SAMPLE})
        sb = sl.json()
        print("status:", sl.status_code, "result:", sb.get("status"),
              "findings:", sb.get("finding_count"), "elapsed:", sb.get("duration_ms"), "ms")
        assert sl.status_code == 200, sb
        assert sb.get("tool") == "slither"
        assert sb.get("status") in ("ok", "no_findings"), sb.get("status")
        if sb.get("status") == "ok":
            assert sb.get("finding_count", 0) >= 1
            first = sb["findings"][0]
            print("first finding:", first.get("severity"), first.get("check"),
                  f"{first.get('file')}:{first.get('start_line')}")
            assert first.get("kind") == "tool"
            assert first.get("status") == "confirmed"
        raw = sb.get("raw_output") or ""
        assert "N:\\Auditor" not in raw and "C:\\Users" not in raw, "raw output must be path-redacted"

        print("\n== /api/slither (empty source should 422) ==")
        se = c.post(f"{BASE}/api/slither", json={"source": ""})
        print("status:", se.status_code, "(expected 422)")
        assert se.status_code == 422

        print("\n== /api/review (empty source should 422) ==")
        e = c.post(f"{BASE}/api/review", json={"source": ""})
        print("status:", e.status_code, "(expected 422)")
        assert e.status_code == 422

    print("\nALL SMOKE CHECKS PASSED")
    return 0


if __name__ == "__main__":
    sys.exit(main())
