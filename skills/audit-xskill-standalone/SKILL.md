---
name: audit-xskill-standalone
description: >-
  Use when auditing or fixing a pure-local xskill install (standalone `xskill serve`,
  not team server/client): config, daemon, watcher/registry, local skill search API,
  dashboard. Run the audit script first, then execute its next actions in order.
---

# Audit xskill standalone (真机)

目标：弄清本机 **standalone**（`xskill serve`，不连 team）是否健康，并按脚本给出的
`next` **依次动手**。不要先猜；先跑脚本。

## 你必须先做的事

在仓库根目录执行（只读探测，不会改配置/起服）：

```bash
python3.11 scripts/audit_standalone.py          # need Python ≥3.9
# 或
python3.11 scripts/audit_standalone.py --json
```

若用户指定了隔离 home：

```bash
XSKILL_HOME=/path/to/home python3.11 scripts/audit_standalone.py
```

读完输出后的 **`## agent playbook (do in order)`**，从第 1 条开始做。
每做完一条可再次跑脚本，确认对应检查从 FAIL/WARN 变成 PASS。

## 决策规则

| 脚本状态 | 你的行为 |
|----------|----------|
| `FAIL` | **立刻处理**，不要跳过 |
| `WARN` | 处理，除非用户明确说可忽略 |
| `PASS` / `SKIP` | 不要「优化」已通过项；SKIP 通常要等 daemon 起来后再跑脚本 |
| playbook 为空 | 做一次真实 agent 对话产生轨迹，再重跑脚本看 registry/traj 是否前进 |

## 范围（standalone only）

这些是本 skill 覆盖的：

- `~/.xskill/config.yaml`（llm + embedding）
- `xskill serve` 守护进程（非 `--server`）
- `GET /api/v1/health`、`/status`、`/registry/dirs`
- **本地** `POST /api/v1/skills/search`（本机 `.skill_index.pkl`，不是 SkillHub）
- dashboard 是否可达
- 本机是否装了 Claude Code / Codex / OpenCode 等

这些 **不是** 本 skill（别往这边绕）：

- `xskill connect` / `xskill serve --server`
- CLI `xskill search ...`（那是 team SkillHub 路径）

本地搜 skill 用 API 或 dashboard，不要用 CLI `xskill search` 判断 standalone 是否好。

## 常见修复（与脚本 next 对齐）

1. **无 config / llm / embedding**  
   `xskill serve` 生成模板 → 编辑 `config.yaml` → 再 `xskill serve`。  
   Embedding 不要指向 DeepSeek（无 embedding 接口）。

2. **daemon 没在跑**  
   前台或服务方式保持：`xskill serve`。

3. **registry 0 个目录**  
   用已安装的 coding agent 跑一轮任务；或  
   `xskill registry add /path/to/trajectories`。

4. **本地 search 500**  
   缺 index / 未配 embedding 不应 500（见上游 PR #196）。升级或打补丁后再测：  
   `curl -s -X POST http://127.0.0.1:<port>/api/v1/skills/search -H 'content-type: application/json' -d '{"query":"heartbeat","top_k":2}'`

5. **search 200 但 `[]`**  
   首次安装正常；看 serve 日志是否有 `skill search skipped`。有 skill 后重建索引/等 watcher。

## 完成标准

再跑一次 `python3.11 scripts/audit_standalone.py`：

- 无 `FAIL`
- `daemon` / `health` / `status` / `local_search` 为 `PASS`
- 若本机有 agent：`registry` 不为「0 dirs」的 WARN（或已 `registry add`）

向用户汇报：逐条检查结果摘要 + 你已执行的 next actions + 仍未消除的 WARN。
