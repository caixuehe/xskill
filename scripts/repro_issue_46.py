#!/usr/bin/env python3
"""Reproduce SkillNerds/xskill#46 first-use failures.

Modes:
  current       — import from the checked-out tree / editable install
  legacy-0.5.2  — exercise the CLI/API shapes reported in the issue
"""
from __future__ import annotations

import argparse
import json
import os
import shutil
import sys
import tempfile
import traceback
from pathlib import Path


def _banner(title: str) -> None:
    print(f"\n=== {title} ===", flush=True)


def _home() -> Path:
    raw = os.environ.get("XSKILL_HOME")
    if raw:
        home = Path(raw)
    else:
        home = Path(tempfile.mkdtemp(prefix="xskill-issue46-"))
        os.environ["XSKILL_HOME"] = str(home)
    home.mkdir(parents=True, exist_ok=True)
    skill = home / "skill"
    if skill.exists():
        shutil.rmtree(skill)
    skill.mkdir(parents=True)
    # Ensure uninitialized: no .git, no .skill_index.pkl
    assert not (skill / ".git").exists()
    assert not (skill / ".skill_index.pkl").exists()
    print(f"XSKILL_HOME={home}")
    print(f"skill_dir={skill} (no git, no index)")
    return home


def repro_status_current(skill_dir: Path) -> dict:
    _banner("Bug2: GET /api/v1/status (current)")
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import xskill.api.app as api_app

    api_app._skill_dir = skill_dir
    api_app._config = {"llm": {}, "embedding": {}}
    app = FastAPI()
    app.include_router(api_app.router)
    resp = TestClient(app).get("/api/v1/status")
    body = resp.json()
    print(f"status_code={resp.status_code}")
    print(f"body={body}")
    return {"status_code": resp.status_code, "body": body}


def repro_search_current(skill_dir: Path) -> dict:
    _banner("Bug1-ish: search_skills / skills/search (current)")
    from xskill.skill.repo import search_skill_index
    from xskill.core import XSkill
    from types import SimpleNamespace
    from fastapi import FastAPI
    from fastapi.testclient import TestClient
    import xskill.api.app as api_app

    index_hits = search_skill_index(
        skill_dir=skill_dir, query="heartbeat", embed_client=object(), top_k=2,
    )
    print(f"search_skill_index -> {index_hits!r}")

    obj = SimpleNamespace(
        skill_repo=SimpleNamespace(root=skill_dir, get=lambda name: None),
        embed=object(),
    )
    try:
        core_hits = XSkill.search_skills(obj, "heartbeat", top_k=2)
        print(f"core.search_skills -> {core_hits!r}")
        core_err = None
    except Exception as exc:  # noqa: BLE001 - repro harness
        core_hits = None
        core_err = f"{type(exc).__name__}: {exc}"
        traceback.print_exc()
        print(f"core.search_skills RAISED {core_err}")

    api_app._skill_dir = skill_dir
    api_app._config = {"llm": {}, "embedding": {}}
    app = FastAPI()
    app.include_router(api_app.router)
    resp = TestClient(app).post(
        "/api/v1/skills/search", json={"query": "heartbeat", "top_k": 2},
    )
    print(f"POST /api/v1/skills/search status={resp.status_code} body={resp.text[:400]}")
    return {
        "index_hits": index_hits,
        "core_hits": None if core_hits is None else list(core_hits),
        "core_error": core_err,
        "api_search_status": resp.status_code,
        "api_search_body": resp.text[:400],
    }


def repro_cli_legacy() -> dict:
    """Try the exact CLI shape from the issue: `xskill search skill ...`."""
    _banner("Bug1: CLI `xskill search skill` (legacy)")
    import subprocess

    cmd = [sys.executable, "-m", "xskill", "search", "skill", "heartbeat", "--top-k", "2"]
    print("running:", " ".join(cmd))
    proc = subprocess.run(cmd, capture_output=True, text=True)
    print("exit=", proc.returncode)
    print("stdout=\n", proc.stdout[-2000:])
    print("stderr=\n", proc.stderr[-2000:])
    combined = (proc.stdout or "") + "\n" + (proc.stderr or "")
    return {
        "exit": proc.returncode,
        "attribute_error": "AttributeError" in combined,
        "stdout_tail": proc.stdout[-1000:],
        "stderr_tail": proc.stderr[-1000:],
    }


def repro_status_legacy(skill_dir: Path) -> dict:
    _banner("Bug2: GET /api/v1/status (legacy import)")
    # Best-effort: mirror issue by calling the same helpers if importable.
    try:
        from fastapi import FastAPI
        from fastapi.testclient import TestClient
        import xskill.api.app as api_app
    except Exception as exc:  # noqa: BLE001
        print(f"import failed: {exc}")
        return {"import_error": str(exc)}

    # Older builds may eagerly load config; point HOME-ish vars if present.
    api_app._skill_dir = skill_dir
    if getattr(api_app, "_config", None) is None or not isinstance(api_app._config, dict):
        api_app._config = {"llm": {}, "embedding": {}}
    else:
        api_app._config = {"llm": {}, "embedding": {}}

    app = FastAPI()
    app.include_router(api_app.router)
    try:
        resp = TestClient(app).get("/api/v1/status")
        print(f"status_code={resp.status_code}")
        print(f"body={resp.text[:500]}")
        return {
            "status_code": resp.status_code,
            "body": resp.text[:500],
            "is_500": resp.status_code == 500,
            "mentions_not_git": "No git repository" in resp.text
            or "NotGitRepository" in resp.text,
        }
    except Exception as exc:  # noqa: BLE001
        traceback.print_exc()
        return {"raised": f"{type(exc).__name__}: {exc}"}


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--mode", choices=["current", "legacy-0.5.2"], required=True)
    args = parser.parse_args()

    print(f"python={sys.version}")
    print(f"platform={sys.platform}")
    try:
        import xskill
        print(f"xskill={getattr(xskill, '__version__', '?')} file={xskill.__file__}")
    except Exception as exc:  # noqa: BLE001
        print(f"xskill import failed: {exc}")

    home = _home()
    skill_dir = home / "skill"
    report: dict = {"mode": args.mode, "python": sys.version, "platform": sys.platform}

    if args.mode == "current":
        report["status"] = repro_status_current(skill_dir)
        report["search"] = repro_search_current(skill_dir)
        # Also show what CLI search means now
        _banner("CLI search help (current)")
        import subprocess
        proc = subprocess.run(
            [sys.executable, "-m", "xskill", "search", "--help"],
            capture_output=True, text=True,
        )
        print(proc.stdout or proc.stderr)
    else:
        report["cli_search"] = repro_cli_legacy()
        report["status"] = repro_status_legacy(skill_dir)

    _banner("SUMMARY JSON")
    print(json.dumps(report, ensure_ascii=False, indent=2, default=str))

    # Non-zero only if harness itself crashed earlier; always 0 so Actions
    # uploads the log as the signal. Explicitly print verdict lines.
    _banner("VERDICT")
    if args.mode == "current":
        st = report["status"]
        se = report["search"]
        print(
            "status_fixed=",
            st.get("status_code") == 200 and st.get("body", {}).get("git_branch") is None,
        )
        print("attribute_error_gone=", se.get("core_error") is None)
        print("api_search_status=", se.get("api_search_status"))
    else:
        cli = report.get("cli_search", {})
        st = report.get("status", {})
        print("legacy_cli_attribute_error=", cli.get("attribute_error"))
        print("legacy_status_is_500=", st.get("is_500"))
        print("legacy_status_mentions_not_git=", st.get("mentions_not_git"))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
