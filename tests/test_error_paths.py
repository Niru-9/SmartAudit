"""Offline error-path tests for the Phase 1 backend.

No live model needed: the Ollama client is monkeypatched to simulate
unavailable / timeout / empty-response conditions. Uses FastAPI TestClient.

Run:  py tests/test_error_paths.py
"""

from __future__ import annotations

import os
import sys
from unittest import mock

sys.path.insert(0, os.path.dirname(os.path.dirname(os.path.abspath(__file__))))

from fastapi.testclient import TestClient  # noqa: E402

from backend import ollama_client  # noqa: E402
from backend.main import app  # noqa: E402

SRC = "pragma solidity ^0.8.20; contract A { function f() external {} }"
results: list[bool] = []


def check(name: str, ok: bool, detail: str = "") -> None:
    results.append(ok)
    print(f"[{'PASS' if ok else 'FAIL'}] {name}{(' — ' + detail) if detail else ''}")


def main() -> int:
    client = TestClient(app, raise_server_exceptions=False)

    # 1. Empty source -> 422 (pydantic min_length).
    r = client.post("/api/review", json={"source": ""})
    check("empty source -> 422", r.status_code == 422, f"got {r.status_code}")

    # 2. Whitespace-only source -> 400 (handler strip check).
    r = client.post("/api/review", json={"source": "   "})
    check("whitespace source -> 400", r.status_code == 400, f"got {r.status_code}")

    # 3. Model unavailable -> 503 (mapped from OllamaUnavailableError).
    with mock.patch.object(
        ollama_client, "chat", side_effect=ollama_client.OllamaUnavailableError("boom")
    ):
        r = client.post("/api/review", json={"source": SRC})
    check("model unavailable -> 503", r.status_code == 503, f"got {r.status_code}")
    check("503 has user-facing detail", "not reachable" in r.json().get("detail", "").lower())

    # 4. Timeout -> 504.
    with mock.patch.object(
        ollama_client, "chat", side_effect=ollama_client.OllamaTimeoutError("slow")
    ):
        r = client.post("/api/review", json={"source": SRC})
    check("timeout -> 504", r.status_code == 504, f"got {r.status_code}")

    # 5. Upstream HTTP error -> 502.
    with mock.patch.object(
        ollama_client, "chat", side_effect=ollama_client.OllamaHttpError("HTTP 500")
    ):
        r = client.post("/api/review", json={"source": SRC})
    check("ollama http error -> 502", r.status_code == 502, f"got {r.status_code}")

    # 6. Empty model response -> 502 (invalid/empty response handling).
    with mock.patch.object(ollama_client, "chat", return_value=("", "mock:1", 5)):
        r = client.post("/api/review", json={"source": SRC})
    check("empty model response -> 502", r.status_code == 502, f"got {r.status_code}")

    # 7. Whitespace-only model response -> 502.
    with mock.patch.object(ollama_client, "chat", return_value=("   \n  ", "mock:1", 5)):
        r = client.post("/api/review", json={"source": SRC})
    check("whitespace model response -> 502", r.status_code == 502, f"got {r.status_code}")

    # 8. Good model response -> 200 with notice + content.
    with mock.patch.object(
        ollama_client, "chat", return_value=("## Review\nLooks ok.", "mock:1", 12)
    ):
        r = client.post("/api/review", json={"source": SRC})
    body = r.json()
    check("good response -> 200", r.status_code == 200, f"got {r.status_code}")
    check("response carries AI notice", "AI-assisted" in body.get("notice", ""))

    # 9. Suggest: no code block -> suggested_code null (does not error).
    with mock.patch.object(
        ollama_client, "chat", return_value=("Only prose, no code.", "mock:1", 12)
    ):
        r = client.post("/api/suggest", json={"source": SRC})
    b = r.json()
    check("suggest without code -> 200", r.status_code == 200, f"got {r.status_code}")
    check("suggest without code -> null code", b.get("suggested_code") is None)

    # 10. Suggest: with code block -> extracted.
    fenced = "## Notes\nchanged x\n```solidity\ncontract A { function f() external {} }\n```"
    with mock.patch.object(ollama_client, "chat", return_value=(fenced, "mock:1", 12)):
        r = client.post("/api/suggest", json={"source": SRC})
    b = r.json()
    check("suggest with code -> extracted", b.get("suggested_code") == "contract A { function f() external {} }")
    check("suggest notes exclude code block", "```" not in (b.get("notes") or ""))

    # 11. Chat: empty question -> 422.
    r = client.post("/api/chat", json={"source": SRC, "question": ""})
    check("chat empty question -> 422", r.status_code == 422, f"got {r.status_code}")

    # 12. Chat: empty source -> 422.
    r = client.post("/api/chat", json={"source": "", "question": "why?"})
    check("chat empty source -> 422", r.status_code == 422, f"got {r.status_code}")

    # 13. Chat: good -> 200.
    with mock.patch.object(ollama_client, "chat", return_value=("Reentrancy risk.", "mock:1", 12)):
        r = client.post("/api/chat", json={"source": SRC, "question": "risk?"})
    check("chat good -> 200", r.status_code == 200, f"got {r.status_code}")

    # 14. Health with unreachable model -> model_unavailable.
    with mock.patch.object(ollama_client, "list_models", return_value=[]):
        r = client.get("/api/health")
    check("health unreachable -> model_unavailable", r.json().get("status") == "model_unavailable")

    # 15. Health reports real analyzer status: slither+solc integrated,
    #     no OTHER analyzer is claimed integrated (Phase 3).
    with mock.patch.object(ollama_client, "list_models", return_value=["mock:1"]):
        r = client.get("/api/health")
    tools = r.json().get("tools", {})
    check(
        "health reports slither/solc availability",
        tools.get("slither") in ("available", "unavailable")
        and tools.get("solc") in ("available", "unavailable"),
        str(tools),
    )
    check(
        "health claims no other analyzer integrated",
        all(
            v == "not_integrated"
            for k, v in tools.items()
            if k not in ("slither", "solc", "solhint")
        ),
        str(tools),
    )

    print()
    passed = sum(results)
    print(f"{passed}/{len(results)} checks passed")
    return 0 if all(results) else 1


if __name__ == "__main__":
    sys.exit(main())
