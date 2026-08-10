#!/usr/bin/env python3
"""Standalone 真机审计（只读）——给人类 / coding agent 看「下一步做什么」。

探测本机 xskill standalone 部署状态：config、daemon、API、watch dirs、
skill index、本地 search、dashboard。每条检查输出 PASS / WARN / FAIL，
并附带一条可直接执行的 next action。

用法：
  python scripts/audit_standalone.py
  python scripts/audit_standalone.py --json
  XSKILL_HOME=/tmp/xskill-home python scripts/audit_standalone.py

约束：不改配置、不起服、不装 skill；只读本地文件 + 可选打本机 HTTP。
"""
from __future__ import annotations

import argparse
import json
import os
import sys
import urllib.error
import urllib.request
from dataclasses import asdict, dataclass, field
from pathlib import Path
from typing import Any, Optional


@dataclass
class Check:
    id: str
    title: str
    status: str  # PASS | WARN | FAIL | SKIP
    detail: str
    next_action: str


@dataclass
class Report:
    mode: str
    home: str
    checks: list[Check] = field(default_factory=list)

    def add(self, check: Check) -> None:
        self.checks.append(check)


def _home() -> Path:
    raw = os.environ.get("XSKILL_HOME")
    if raw:
        return Path(raw).expanduser().resolve()
    return (Path.home() / ".xskill").resolve()


def _load_yaml(path: Path) -> dict:
    if not path.is_file():
        return {}
    try:
        import yaml
    except ImportError:
        return {"__error__": "PyYAML not installed"}
    try:
        data = yaml.safe_load(path.read_text(encoding="utf-8")) or {}
        return data if isinstance(data, dict) else {}
    except Exception as exc:  # noqa: BLE001
        return {"__error__": f"{type(exc).__name__}: {exc}"}


def _http_json(url: str, *, method: str = "GET", body: Optional[dict] = None,
               timeout: float = 3.0) -> tuple[Optional[int], Any]:
    data = None
    headers = {"Accept": "application/json"}
    if body is not None:
        data = json.dumps(body).encode("utf-8")
        headers["Content-Type"] = "application/json"
    req = urllib.request.Request(url, data=data, headers=headers, method=method)
    try:
        with urllib.request.urlopen(req, timeout=timeout) as resp:
            raw = resp.read().decode("utf-8", errors="replace")
            try:
                parsed = json.loads(raw) if raw else None
            except json.JSONDecodeError:
                parsed = raw
            return resp.status, parsed
    except urllib.error.HTTPError as exc:
        try:
            payload = exc.read().decode("utf-8", errors="replace")
            parsed: Any = json.loads(payload) if payload else None
        except Exception:  # noqa: BLE001
            parsed = str(exc)
        return exc.code, parsed
    except Exception as exc:  # noqa: BLE001
        return None, f"{type(exc).__name__}: {exc}"


def _pid_alive(pid: Any) -> bool:
    if not isinstance(pid, int) or pid <= 0:
        return False
    try:
        os.kill(pid, 0)
    except ProcessLookupError:
        return False
    except PermissionError:
        return True
    except OSError:
        return False
    return True


def _read_runtime(home: Path) -> dict:
    path = home / "serve_runtime.json"
    if not path.is_file():
        return {}
    try:
        data = json.loads(path.read_text(encoding="utf-8"))
        return data if isinstance(data, dict) else {}
    except Exception:  # noqa: BLE001
        return {}


def _detect_agents() -> list[str]:
    found: list[str] = []
    home = Path.home()
    probes = [
        ("claude_code", home / ".claude" / "projects"),
        ("codex", home / ".codex" / "sessions"),
        ("opencode", home / ".local" / "share" / "opencode" / "opencode.db"),
        ("cursor", home / ".cursor" / "projects"),
        ("trae", home / ".trae" / "projects"),
        ("openclaw", home / ".openclaw"),
    ]
    for name, path in probes:
        if path.exists():
            found.append(name)
    return found


def audit(home: Path) -> Report:
    report = Report(mode="standalone", home=str(home))
    config_path = home / "config.yaml"
    skill_dir = home / "skill"
    cfg = _load_yaml(config_path)

    # 1) home / config
    if not home.is_dir():
        report.add(Check(
            "home", "XSKILL_HOME exists", "FAIL",
            f"missing directory: {home}",
            "Run: xskill serve   # first run creates ~/.xskill and config template",
        ))
        return report
    report.add(Check(
        "home", "XSKILL_HOME exists", "PASS",
        str(home),
        "No action.",
    ))

    if cfg.get("__error__"):
        report.add(Check(
            "config_parse", "config.yaml readable", "FAIL",
            str(cfg["__error__"]),
            f"Fix or recreate {config_path}",
        ))
        return report
    if not config_path.is_file():
        report.add(Check(
            "config", "config.yaml present", "FAIL",
            "config.yaml not found",
            "Run: xskill serve   # writes config template, then fill llm/embedding",
        ))
        return report

    llm = cfg.get("llm") or {}
    emb = cfg.get("embedding") or {}
    llm_ok = bool(llm.get("base_url") and llm.get("model"))
    emb_ok = bool(emb.get("base_url") and emb.get("model"))
    if not llm_ok:
        report.add(Check(
            "config_llm", "llm endpoint configured", "FAIL",
            "llm.base_url / llm.model missing",
            f"Edit {config_path}: set llm.base_url, llm.model, llm.api_key",
        ))
    else:
        report.add(Check(
            "config_llm", "llm endpoint configured", "PASS",
            f"model={llm.get('model')}",
            "No action.",
        ))
    if not emb_ok:
        report.add(Check(
            "config_embedding", "embedding endpoint configured", "FAIL",
            "embedding.base_url / embedding.model missing "
            "(DeepSeek has no embeddings — use DashScope/OpenAI/Ollama)",
            f"Edit {config_path}: set embedding.base_url, model, api_key, dim",
        ))
    else:
        report.add(Check(
            "config_embedding", "embedding endpoint configured", "PASS",
            f"model={emb.get('model')}",
            "No action.",
        ))

    # 2) role / team files (warn if not standalone)
    team_server = (home / "team_server.json").is_file()
    team_client = (home / "team_client.json").is_file() or (
        home / "connect_daemon.json"
    ).is_file()
    if team_server:
        report.add(Check(
            "role", "deployment role is standalone", "WARN",
            "team server state present — this machine looks like serve --server",
            "If you meant pure local: stop server mode; use `xskill serve` without --server",
        ))
    elif team_client:
        report.add(Check(
            "role", "deployment role is standalone", "WARN",
            "team client state present — CLI search goes to SkillHub, not local index",
            "For local skill search use API POST /api/v1/skills/search or dashboard; "
            "or disconnect client if auditing standalone only",
        ))
    else:
        report.add(Check(
            "role", "deployment role is standalone", "PASS",
            "no team server/client state detected",
            "No action.",
        ))

    # 3) daemon runtime
    runtime = _read_runtime(home)
    running = bool(runtime) and _pid_alive(runtime.get("pid"))
    port = runtime.get("port") if running else None
    if not running:
        report.add(Check(
            "daemon", "xskill serve running", "FAIL",
            "serve_runtime.json missing or pid dead",
            "Run: xskill serve    # keep it running in a terminal/service",
        ))
    else:
        mode = runtime.get("mode", "?")
        report.add(Check(
            "daemon", "xskill serve running", "PASS",
            f"pid={runtime.get('pid')} port={port} mode={mode}",
            "No action." if mode in ("standalone", None, "?")
            else "Daemon mode is not standalone; confirm you did not pass --server",
        ))

    # 4) agent ecosystems present
    agents = _detect_agents()
    if not agents:
        report.add(Check(
            "agents", "coding agent installs detected", "WARN",
            "no Claude Code / Codex / OpenCode / Cursor / Trae paths found",
            "Install at least one supported agent, or "
            "`xskill registry add /path/to/trajectories` to backfill",
        ))
    else:
        report.add(Check(
            "agents", "coding agent installs detected", "PASS",
            ", ".join(agents),
            "No action. After serve is up, confirm these appear under registry dirs.",
        ))

    # 5) skill dir / index
    if not skill_dir.is_dir():
        report.add(Check(
            "skill_dir", "skill directory exists", "WARN",
            f"missing {skill_dir}",
            "Start serve once with valid config; or POST /api/v1/init after daemon is up",
        ))
    else:
        report.add(Check(
            "skill_dir", "skill directory exists", "PASS",
            str(skill_dir),
            "No action.",
        ))
    index_path = skill_dir / ".skill_index.pkl"
    if index_path.is_file():
        report.add(Check(
            "skill_index", "local .skill_index.pkl present", "PASS",
            str(index_path),
            "No action.",
        ))
    else:
        report.add(Check(
            "skill_index", "local .skill_index.pkl present", "WARN",
            "index missing — local semantic search returns empty (should warn, not 500)",
            "After skills exist + embedding configured: "
            "curl -X POST http://127.0.0.1:<port>/api/v1/reindex "
            "or wait for watcher to build index",
        ))

    # HTTP checks only if daemon up
    if not (running and isinstance(port, int)):
        report.add(Check(
            "http", "local HTTP probes", "SKIP",
            "daemon not running — skipped health/status/search/dashboard",
            "Start `xskill serve`, re-run this script",
        ))
        return report

    base = f"http://127.0.0.1:{port}"

    code, health = _http_json(f"{base}/api/v1/health")
    if code == 200:
        report.add(Check(
            "health", "GET /api/v1/health", "PASS",
            f"{health}",
            "No action.",
        ))
    else:
        report.add(Check(
            "health", "GET /api/v1/health", "FAIL",
            f"status={code} body={health}",
            f"Check daemon logs; confirm port {port} matches serve",
        ))

    code, status = _http_json(f"{base}/api/v1/status")
    if code == 200 and isinstance(status, dict):
        report.add(Check(
            "status", "GET /api/v1/status", "PASS",
            f"skill_count={status.get('skill_count')} git_branch={status.get('git_branch')!r}",
            "If skill_count=0: use an agent so trajs flow in, or registry add old trajs",
        ))
    else:
        report.add(Check(
            "status", "GET /api/v1/status", "FAIL",
            f"status={code} body={status}",
            "Uninitialized skill dir used to 500 here — upgrade to build with #196 fix, "
            "or ensure NotGitRepository is handled",
        ))

    code, dirs = _http_json(f"{base}/api/v1/registry/dirs")
    if code == 200:
        count = None
        if isinstance(dirs, dict):
            count = dirs.get("count")
            if count is None and isinstance(dirs.get("dirs"), list):
                count = len(dirs["dirs"])
            if count is None and isinstance(dirs.get("items"), list):
                count = len(dirs["items"])
        if count == 0:
            report.add(Check(
                "registry", "watch / registry dirs", "WARN",
                "API ok but 0 dirs registered",
                "Run agent once so auto-detect registers sessions, or "
                "`xskill registry add <traj_dir>`",
            ))
        else:
            report.add(Check(
                "registry", "watch / registry dirs", "PASS",
                f"response keys={list(dirs) if isinstance(dirs, dict) else type(dirs).__name__} "
                f"count≈{count}",
                "No action. Spot-check a traj progresses beyond discovered.",
            ))
    else:
        report.add(Check(
            "registry", "watch / registry dirs", "FAIL",
            f"status={code} body={dirs}",
            "Daemon API incomplete — check serve logs / version mismatch",
        ))

    code, search_body = _http_json(
        f"{base}/api/v1/skills/search",
        method="POST",
        body={"query": "heartbeat", "top_k": 2},
    )
    if code == 200:
        report.add(Check(
            "local_search", "POST /api/v1/skills/search (local index path)", "PASS",
            f"body={search_body!r}"[:300],
            "Empty list is OK on first use if index/embedding missing; "
            "check serve logs for 'skill search skipped' warning",
        ))
    elif code == 500:
        report.add(Check(
            "local_search", "POST /api/v1/skills/search (local index path)", "FAIL",
            f"500 body={search_body!r}"[:300],
            "Likely first-use embed crash — need fix from PR #196 "
            "(skip embed when index missing / embedding unset)",
        ))
    else:
        report.add(Check(
            "local_search", "POST /api/v1/skills/search (local index path)", "FAIL",
            f"status={code} body={search_body!r}"[:300],
            "Inspect API error; confirm standalone daemon exposes /api/v1/skills/search",
        ))

    # dashboard static / console
    for path in ("/", "/dashboard", "/api/v1/dashboard/overview"):
        code, body = _http_json(f"{base}{path}")
        if code is not None and code < 500:
            report.add(Check(
                "dashboard", f"dashboard reachable ({path})", "PASS",
                f"HTTP {code}",
                f"Open browser: {base}/  and confirm UI is not an empty shell",
            ))
            break
    else:
        report.add(Check(
            "dashboard", "dashboard reachable", "WARN",
            "no dashboard route answered <500",
            f"Try opening {base}/ manually; check serve logs for static mount errors",
        ))

    return report


def _print_human(report: Report) -> None:
    print(f"# xskill standalone audit")
    print(f"home: {report.home}")
    print(f"mode_target: {report.mode}")
    print()
    order = {"FAIL": 0, "WARN": 1, "SKIP": 2, "PASS": 3}
    counts = {"PASS": 0, "WARN": 0, "FAIL": 0, "SKIP": 0}
    for check in report.checks:
        counts[check.status] = counts.get(check.status, 0) + 1
        print(f"[{check.status}] {check.id}: {check.title}")
        print(f"  detail: {check.detail}")
        print(f"  next:   {check.next_action}")
        print()
    print("## summary")
    print(
        f"PASS={counts['PASS']} WARN={counts['WARN']} "
        f"FAIL={counts['FAIL']} SKIP={counts['SKIP']}"
    )
    print()
    print("## agent playbook (do in order)")
    actions = [
        c for c in sorted(report.checks, key=lambda c: order.get(c.status, 9))
        if c.status in {"FAIL", "WARN"} and c.next_action != "No action."
    ]
    if not actions:
        print("1. All critical checks passed. Optionally drive one real agent turn,")
        print("   then re-run this script and confirm registry/traj progress.")
        return
    for index, check in enumerate(actions, start=1):
        print(f"{index}. ({check.status}/{check.id}) {check.next_action}")


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument(
        "--home",
        type=Path,
        default=None,
        help="Override XSKILL_HOME (default: env or ~/.xskill)",
    )
    parser.add_argument("--json", action="store_true", help="Machine-readable report")
    args = parser.parse_args()
    home = args.home.expanduser().resolve() if args.home else _home()
    report = audit(home)
    if args.json:
        print(json.dumps({
            "mode": report.mode,
            "home": report.home,
            "checks": [asdict(c) for c in report.checks],
        }, ensure_ascii=False, indent=2))
    else:
        _print_human(report)
    failed = any(c.status == "FAIL" for c in report.checks)
    return 1 if failed else 0


if __name__ == "__main__":
    raise SystemExit(main())
