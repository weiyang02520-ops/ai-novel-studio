#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
ai_checkpoint.py — AI Agent 自动交接检查点

流程:
  1. 收集 git 状态, 判断是否有实际变化
  2. 若无变化 → 退出(不创建空 Commit)
  3. 若 git identity 缺失 → 检查 .ai-handoff 生成摘要
  4. 安全扫描(Secret 检测) → 命中则 SECURITY BLOCKED PUSH
  5. 更新 HANDOFF/STATUS/CHANGELOG/REVIEW_REQUEST 等文档
  6. git add + commit + push

用法:
  python scripts/ai_checkpoint.py            # 手动
  python scripts/ai_checkpoint.py --no-push  # 只提交不推送(测试)
  python scripts/ai_checkpoint.py --mode auto --since-min 45  # 定时模式

退出码:
  0 成功 / 1 无变化(正常) / 2 安全阻断 / 3 错误
"""
import argparse
import datetime
import os
import re
import subprocess
import sys
import tempfile

ROOT = os.path.dirname(os.path.dirname(os.path.abspath(__file__)))
HANDOFF_DIR = os.path.join(ROOT, ".ai-handoff")
CHECKPOINTS_DIR = os.path.join(HANDOFF_DIR, "checkpoints")


def run(cmd, cwd=ROOT, check=False, text=True, timeout=120):
    """执行命令, 返回 (code, stdout, stderr)。"""
    try:
        p = subprocess.run(
            cmd, cwd=cwd, capture_output=True, text=text, timeout=timeout
        )
        return p.returncode, p.stdout.strip(), p.stderr.strip()
    except subprocess.TimeoutExpired:
        return 124, "", "timeout"
    except Exception as e:
        return 127, "", str(e)


def git(*args, cwd=ROOT, check=False):
    code, out, err = run(["git", *args], cwd=cwd, check=check)
    if code != 0 and check:
        print(f"[git {' '.join(args)}] 失败: {err}")
    return code, out, err


def get_git_user():
    """获取 commit 身份(仓库级优先, 其次全局)。"""
    _, name, _ = git("config", "user.name")
    _, email, _ = git("config", "user.email")
    if not name:
        _, name, _ = git("config", "--global", "user.name")
    if not email:
        _, email, _ = git("config", "--global", "user.email")
    return name, email


def get_project_name():
    """从目录名推断项目名(纯展示用, 不用于远程创建)。"""
    name = os.path.basename(ROOT) or "project"
    # 中文字符直接显示
    return name


def collect_changes():
    """返回 (有变化?, 变更摘要, 变更统计)。"""
    # 未跟踪文件 + 已跟踪修改
    _, untracked, _ = git("status", "--porcelain")
    if not untracked:
        return False, [], {}

    lines = [l for l in untracked.splitlines() if l.strip()]
    stats = {"added": 0, "modified": 0, "deleted": 0}
    for l in lines:
        code = l[:2]
        if code == "??":
            stats["added"] += 1
        elif code.startswith("M"):
            stats["modified"] += 1
        elif code.startswith("D"):
            stats["deleted"] += 1
    return True, lines, stats


def summary_line(changes, stats):
    """根据变更自动生成一句话摘要。"""
    keys = ["reports", "indexes", "scripts", "reconstructed", ".ai-handoff"]
    for k in keys:
        if any(f"/{k}/" in c for c in changes) or any(c.endswith(f"/{k}") for c in changes):
            return f"update {k} analysis artifacts"
    if stats["added"] > stats["modified"]:
        return f"add {stats['added']} new files"
    return f"update {stats['modified']} files"


def write_or_update(path, default, content=None):
    """写入或创建文件(不覆盖已有内容, 除非显式提供 content)。"""
    if os.path.exists(path) and content is None:
        return False
    os.makedirs(os.path.dirname(path), exist_ok=True)
    with open(path, "w", encoding="utf-8") as f:
        f.write(content if content is not None else default)
    return True


def collect_scan_files():
    """构建安全扫描文件集合(机器可读 NUL 分隔, 不解析 porcelain)。

    覆盖: staged + unstaged(含 rename 目标) + untracked(含新目录内文件)。
    保证: 扫描文件集合 ⊇ git add -A 最终提交集合。

    删除文件无需扫描内容(不存在于磁盘, 会被 isfile 过滤)。
    """
    files: list[str] = []
    for cmd in (
        ["git", "diff", "--name-only", "-z"],          # unstaged tracked 修改
        ["git", "diff", "--cached", "--name-only", "-z"],  # staged
        ["git", "ls-files", "--others", "--exclude-standard", "-z"],  # untracked(含新目录内)
    ):
        code, out, _ = run(cmd, cwd=ROOT, timeout=60)
        if code != 0:
            continue
        # NUL 分隔; Windows 下可能 CRLF, 统一去 \r\n
        for p in out.split("\x00"):
            p = p.strip()
            if p:
                files.append(p)
    # 去重(保持顺序)
    seen = set()
    result = []
    for p in files:
        if p not in seen:
            seen.add(p)
            result.append(p)
    return result


def security_scan(force_files=None):
    """
    推送前安全扫描。

    返回 (blocked, findings)
      blocked=True 时禁止 push。
    """
    # 1) 获取将要 add 的文件清单: NUL 分隔三命令合并(不再解析 porcelain)
    if force_files:
        files = force_files
    else:
        files = collect_scan_files()

    patterns = [
        # 环境变量/配置
        (r"(?i)\.env$", "环境文件(.env)"),
        (r"(?i)\.env\..+", "环境文件(.env.*)"),
        # 常见密钥变量赋值(不匹配已发布的默认示例)
        (r"(?i)API_?KEY\s*[=:]\s*['\"][A-Za-z0-9_\-]{12,}", "API Key 赋值"),
        (r"(?i)TOKEN\s*[=:]\s*['\"][A-Za-z0-9_\-]{16,}", "Token 赋值"),
        (r"(?i)SECRET\s*[=:]\s*['\"][A-Za-z0-9_\-]{12,}", "Secret 赋值"),
        (r"(?i)PASSWORD\s*[=:]\s*['\"][^'\"]{6,}", "密码赋值"),
        (r"(?i)BASE64\s*[=:]\s*['\"][A-Za-z0-9+/=]{40,}", "Base64 密钥"),
        # 头部
        (r"(?i)^(Authorization|Bearer|X-Api-Key)\s*[=:]\s*\S+", "Authorization/Bearer 头"),
        # 私钥内容特征
        (r"-----BEGIN (RSA |EC |DSA |OPENSSH )?PRIVATE KEY-----", "私钥内容"),
        (r"-----BEGIN CERTIFICATE-----", "证书内容"),
        # 常见模型提供商 Key(识别 gpt-xxxx / sk- 等)
        (r"sk-[A-Za-z0-9]{20,}", "OpenAI/DeepSeek 风格 Key"),
        (r"(?i)(?:OPENAI|ANTHROPIC|DEEPSEEK|GEMINI|OPENROUTER|QWEN|MOONSHOT|GITHUB)[_A-Z]*_?KEY\s*[=:]\s*['\"][^'\"]{8,}", "模型提供商 KEY 赋值"),
        (r"AIza[A-Za-z0-9_\-]{30,}", "Gemini API Key"),
        (r"ghp_[A-Za-z0-9]{20,}", "GitHub Token"),
        (r"gho_[A-Za-z0-9]{20,}", "GitHub OAuth Token"),
        (r"xox[baprs]-[A-Za-z0-9\-]{20,}", "Slack Token"),
        (r"AKIA[A-Z0-9]{16}", "AWS Access Key"),
        (r"eyJ[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{20,}\.[A-Za-z0-9_\-]{10,}", "JWT"),
        # 账号凭据
        (r"(?im)login\s*[=:]\s*['\"][^'\"]{3,}['\"]\s*\n\s*password", "账号/密码对"),
    ]

    findings = []

    # 路径级敏感文件黑名单: 按文件名/路径判断, 与内容无关
    PATH_BLOCK_RE = re.compile(
        r"(?i)(^|[/\\])(\.env(\..*)?|.*\.key|.*\.pem|.*\.p12|.*\.pfx|"
        r"credentials?|secrets?|.*\.secret|id_rsa|id_ed25519|auth\.json)(\.|$)"
    )
    # 高风险二进制扩展名: 无法扫描内容 → 默认阻止 push(除非显式加入安全白名单)
    HIGH_RISK_BINARY_EXTS = {
        ".exe", ".dll", ".so", ".dylib", ".7z", ".zip", ".rar", ".apk", ".aab",
        ".jar", ".war", ".class", ".bin", ".dat", ".pak", ".node", ".wasm", ".pyc",
    }
    # 允许入库的二进制扩展名(低风险, 如图片/字体) — 作为安全白名单
    ALLOWED_BINARY_EXTS = {".png", ".jpg", ".jpeg", ".gif", ".webp", ".ico", ".ttf", ".otf", ".woff", ".woff2", ".svg"}

    for path in files:
        full = os.path.join(ROOT, path)
        if not os.path.isfile(full):
            continue
        # 跳过扫描器自身与 .gitignore(它们包含检测规则字符串, 属误报源)
        if os.path.abspath(full) == os.path.abspath(__file__):
            continue
        if os.path.basename(full) == ".gitignore":
            continue

        rel = path.replace("\\", "/")
        base = os.path.basename(full)

        # ── 路径级拦截(与内容无关) ──
        if PATH_BLOCK_RE.search(rel) or PATH_BLOCK_RE.search(base):
            findings.append({
                "file": path, "line": 0, "pattern": "敏感文件路径(.env/.key/.pem/credentials/secrets…)",
                "masked": "(按路径拦截)",
            })
            continue

        ext = os.path.splitext(base)[1].lower()
        size = os.path.getsize(full)

        # ── 高风险二进制: 不扫描内容 → 默认阻止 push ──
        if ext in HIGH_RISK_BINARY_EXTS:
            findings.append({
                "file": path, "line": 0,
                "pattern": f"高风险二进制({ext}), 内容不可扫描",
                "masked": "(按扩展名拦截)",
            })
            continue
        # 其他非文本/超大文件: 白名单图片字体等放行; 其余超 2MB 阻止
        if ext and ext not in ALLOWED_BINARY_EXTS and size > 2_000_000:
            findings.append({
                "file": path, "line": 0,
                "pattern": f"大文件({size // 1024}KB, {ext}), 无法完整扫描内容",
                "masked": "(按大小拦截)",
            })
            continue

        # ── 文本内容扫描(分块, 全文件覆盖, 不只看前 512KB) ──
        try:
            with open(full, "r", encoding="utf-8", errors="replace") as f:
                # 分块读取(64KB/块), 逐块匹配, 记录匹配行号
                for chunk_start in range(0, size, 64 * 1024):
                    f.seek(chunk_start)
                    content = f.read(64 * 1024)
                    if not content:
                        break
                    for pat, desc in patterns:
                        m = re.search(pat, content)
                        if m:
                            line_no = content[: m.start()].count("\n") + 1
                            if chunk_start > 0:
                                # 近似行号(分块边界), 标注"约"
                                line_no = f"约{line_no}"
                            secret = m.group(0)
                            masked = secret[:4] + "…" if len(secret) > 8 else secret
                            findings.append({
                                "file": path, "line": line_no, "pattern": desc, "masked": masked,
                            })
                            break  # 每文件每块最多记一条, 避免刷屏
        except (UnicodeDecodeError, OSError):
            # 无法按文本读取: 非白名单二进制且非高风险扩展 → 保守拦截
            if ext not in ALLOWED_BINARY_EXTS:
                findings.append({
                    "file": path, "line": 0,
                    "pattern": "无法按文本读取, 无法确认内容安全",
                    "masked": "(按内容不可读拦截)",
                })

    return len(findings) > 0, findings


HANDOFF_TEMPLATE = """# Project Handoff

> 自动生成入口: `scripts/ai_checkpoint.py`
> 本文件在每次 Checkpoint 时由脚本**自动重写**, 内容来自单一事实源 `.ai-handoff/PROJECT_STATE.md` + git 事实。
> 请勿手改本文件, 需修改状态请改 PROJECT_STATE.md。

## 1. 项目目标

{project_goal}

## 2. 当前阶段

{phase}

## 3. 本轮完成内容

{last_round}

## 4. 本轮修改文件

{changes_table}

## 5. 已验证结果

{verified}

## 6. 未验证内容

{unverified}

## 7. 当前架构

{architecture}

## 8. 当前已知问题

{known_issues}

## 9. 本轮关键决策

{key_decisions}

## 10. 下一步建议

{next_steps}

## 11. 希望外部模型重点审查

{review_focus}

## 12. Git 信息

- Branch: {branch}
- checkpoint_base_commit: {head_short} {head_subject}
  (checkpoint 开始前的工作区 HEAD; 最新 checkpoint commit 以 GitHub 仓库 HEAD 为准)
- GitHub 仓库可见性: {repo_visibility}(真实查询; 无法获取时显示 unknown)
- 最近 commit(本文件生成时): {last_commit}
- 时间: {now}

## 13. Critical Files

{critical_files}

## 14. Recent Important Changes

{recent_changes}
"""


def load_project_state():
    """读取单一事实源 PROJECT_STATE.md, 缺失时给空值(不自动创建模板)。"""
    path = os.path.join(HANDOFF_DIR, "PROJECT_STATE.md")
    defaults = {
        "project_goal": "(待填写: 请更新 .ai-handoff/PROJECT_STATE.md)",
        "phase": "(待填写)",
        "last_round": "- (待填写)",
        "verified": "- (待填写)",
        "unverified": "- (待填写)",
        "architecture": "(待填写)",
        "known_issues": "- (待填写)",
        "key_decisions": "- (待填写)",
        "next_steps": "- (待填写)",
        "review_focus": "- (待填写)",
        "critical_files": "- (待填写)",
        "recent_changes": "- (待填写)",
    }
    if not os.path.exists(path):
        return defaults, path
    text = open(path, encoding="utf-8").read()
    state = dict(defaults)
    for key in defaults:
        m = re.search(
            rf"^## {re.escape(key)}\s*$\n(.*?)(?=^## |\Z)",
            text,
            re.M | re.S,
        )
        if m:
            val = m.group(1).strip()
            if val and val != "(待填写)":
                state[key] = val
    return state, path


def write_project_state(state, path):
    """写回 PROJECT_STATE.md(全量重写, 保持键顺序一致)。"""
    order = [
        "project_goal", "phase", "last_round", "verified", "unverified",
        "architecture", "known_issues", "key_decisions", "next_steps",
        "review_focus", "critical_files", "recent_changes",
    ]
    lines = ["# Project State (单一事实源)", ""]
    lines.append("> 本文件是 HANDOFF.md / STATUS.md 的唯一内容来源。")
    lines.append("> Agent 每轮完成后**必须更新**本文件(阶段/完成/验证/下一步等), 再运行 checkpoint。")
    lines.append("> 脚本每次 checkpoint 会基于本文件 + git 事实重写 HANDOFF.md 和 STATUS.md。")
    lines.append("")
    for key in order:
        lines.append(f"## {key}")
        lines.append("")
        lines.append(state.get(key, "(待填写)"))
        lines.append("")
    open(path, "w", encoding="utf-8").write("\n".join(lines))


def get_repo_owner_repo():
    """从 git remote get-url origin 解析 (owner, repo)。

    兼容:
      https://github.com/owner/repo.git
      https://github.com/owner/repo
      git@github.com:owner/repo.git
      git@github.com:owner/repo
    无法解析 → (None, None), 绝不猜测。
    """
    code, out, _ = git("remote", "get-url", "origin")
    if code != 0 or not out:
        return None, None
    url = out.strip()
    m = re.match(r"(?:https?://[^/]+/|git@[^:]+:)([^/]+)/([^/]+?)(?:\.git)?$", url)
    if not m:
        return None, None
    return m.group(1), m.group(2)


def get_repo_visibility():
    """查询 GitHub 仓库真实可见性(private/public)。

    仓库 owner/repo 从 git remote 自动解析(不写死)。
    优先 gh CLI(用户已登录), 失败则尝试无 token 的公开 API 探测:
    - API 返回 visibility → 采用
    - 无法解析 remote 或查询失败 → 返回 "unknown"(不猜测)
    """
    owner, repo = get_repo_owner_repo()
    if not owner or not repo:
        return "unknown"
    try:
        code, out, _ = run(
            ["gh", "api", f"repos/{owner}/{repo}", "--jq",
             "{private: .private, visibility: .visibility}"],
            timeout=30,
        )
        if code == 0 and out:
            import json as _json
            d = _json.loads(out)
            if "visibility" in d:
                return d["visibility"]
    except Exception:
        pass
    try:
        import urllib.request
        with urllib.request.urlopen(
            f"https://api.github.com/repos/{owner}/{repo}",
            timeout=10,
        ) as resp:
            import json as _json
            d = _json.loads(resp.read().decode())
            return d.get("visibility", "unknown")
    except Exception:
        return "unknown"


def update_handoff(summary, changes, stats):
    """基于 PROJECT_STATE.md + git 事实, 重写 HANDOFF.md 与 STATUS.md。

    不再出现"脚本写模板后用户没更新, 导致交接文档与真实状态脱节"的问题:
    - 文档内容永远由脚本从单一事实源重新生成
    - 事实源缺失字段时文档显示 "(待填写)", 提示 Agent 去补 PROJECT_STATE.md
    """
    now = datetime.datetime.now().strftime("%Y-%m-%d %H:%M")
    os.makedirs(HANDOFF_DIR, exist_ok=True)
    os.makedirs(CHECKPOINTS_DIR, exist_ok=True)

    state, state_path = load_project_state()

    # 本轮事实: 变更摘要
    summary_line_text = f"**{summary}**(新增 {stats['added']} / 修改 {stats['modified']} / 删除 {stats['deleted']})"
    changes_table = "\n".join(
        f"- `{c}`" for c in changes[:30]
    ) if changes else "- (无文件变更)"
    if len(changes) > 30:
        changes_table += f"\n- …等共 {len(changes)} 个变更"

    # git 事实
    _, branch, _ = git("branch", "--show-current")
    _, head_long, _ = git("rev-parse", "HEAD")
    _, head_subject, _ = git("log", "-1", "--format=%s")
    _, last_commit, _ = git("log", "-1", "--format=%h %ci")
    head_short = (head_long or "?")[:12]

    # GitHub 真实可见性(不依赖手写状态)
    repo_visibility = get_repo_visibility()

    # 重写 HANDOFF.md
    handoff = HANDOFF_TEMPLATE.format(
        project_goal=state["project_goal"],
        phase=state["phase"],
        last_round=state["last_round"],
        changes_table=changes_table,
        verified=state["verified"],
        unverified=state["unverified"],
        architecture=state["architecture"],
        known_issues=state["known_issues"],
        key_decisions=state["key_decisions"],
        next_steps=state["next_steps"],
        review_focus=state["review_focus"],
        critical_files=state["critical_files"],
        recent_changes=state["recent_changes"],
        branch=branch or "(detached)",
        head_short=head_short,
        head_subject=head_subject or "?",
        last_commit=last_commit or "?",
        repo_visibility=repo_visibility,
        now=now,
    )
    with open(os.path.join(HANDOFF_DIR, "HANDOFF.md"), "w", encoding="utf-8") as f:
        f.write(handoff)

    # 重写 STATUS.md(同样来自事实源 + 本轮摘要)
    status = f"""# 项目状态

> 最后更新: {now} (自动生成, 来源: PROJECT_STATE.md)

## 当前阶段

{state['phase']}

## 本轮变更

{summary_line_text}

## 已验证内容

{state['verified']}

## 未验证内容

{state['unverified']}

## 已知问题

{state['known_issues']}

## 下一步

{state['next_steps']}
"""
    with open(os.path.join(HANDOFF_DIR, "STATUS.md"), "w", encoding="utf-8") as f:
        f.write(status)

    # CHANGELOG_AI.md — 追加
    changelog = os.path.join(HANDOFF_DIR, "CHANGELOG_AI.md")
    if not os.path.exists(changelog):
        with open(changelog, "w", encoding="utf-8") as f:
            f.write("# AI 变更日志\n\n")
    with open(changelog, "a", encoding="utf-8") as f:
        f.write(f"\n## {now} — Checkpoint\n\n")
        f.write(f"摘要: {summary}\n\n")
        for c in changes[:50]:
            f.write(f"- `{c}`\n")
        f.write("\n")

    # checkpoint 快照
    snap = os.path.join(CHECKPOINTS_DIR, now.replace(":", "-").replace(" ", "_") + ".md")
    with open(snap, "w", encoding="utf-8") as f:
        f.write(f"# Checkpoint {now}\n\n")
        f.write(f"摘要: {summary}\n\n")
        f.write(f"统计: 新增 {stats['added']}, 修改 {stats['modified']}, 删除 {stats['deleted']}\n\n")
        f.write("## 变更文件\n\n")
        for c in changes[:100]:
            f.write(f"- `{c}`\n")

    return True


def build_commit_message(summary):
    """统一格式的 commit message。"""
    return f"ai-checkpoint: {summary}"


def main():
    parser = argparse.ArgumentParser(description="AI Agent 自动交接检查点")
    parser.add_argument("--no-push", action="store_true", help="只 commit 不 push")
    parser.add_argument("--mode", choices=["manual", "auto"], default="manual",
                        help="manual=主动交接; auto=定时模式(需配合 --since-min)")
    parser.add_argument("--since-min", type=int, default=45,
                        help="auto 模式: 距上次 checkpoint 超过该分钟数才创建")
    args = parser.parse_args()

    if not os.path.isdir(os.path.join(ROOT, ".git")):
        print("⚠ 当前目录还不是 Git 仓库。请先运行: git init")
        print("   (或确认已在正确的项目目录中运行本脚本)")
        return 3

    # ── 1. 检查是否有实际变化 ──
    has_changes, changes, stats = collect_changes()
    if not has_changes:
        print("✓ 工作区干净, 无实际变化, 不创建空 Commit。")
        return 1

    print(f"📋 检测到 {stats['added']} 新增 / {stats['modified']} 修改 / {stats['deleted']} 删除")

    # ── 1.5 定时模式: 有变化但距上次 checkpoint 不足阈值 → 等待积累 ──
    if args.mode == "auto":
        code, out, _ = git("log", "-1", "--format=%ci")
        last_commit_time = out if out else ""
        if last_commit_time:
            try:
                last = datetime.datetime.fromisoformat(last_commit_time.strip())
                now = datetime.datetime.now(last.tzinfo)
                if (now - last).total_seconds() < args.since_min * 60:
                    print(f"⏳ 有新变化, 但距上次 checkpoint 不足 {args.since_min} 分钟, 等待积累。")
                    return 1
            except Exception:
                pass

    # ── 2. Git 身份检查(空提交保护) ──
    name, email = get_git_user()
    if not name or not email:
        print("✋ Git 身份未配置(user.name/user.email), 无法创建 Commit。")
        print("   请运行:")
        print("     git config user.name  \"你的名字\"")
        print("     git config user.email \"you@example.com\"")
        return 3

    # ── 3. 安全扫描(推送前强制) ──
    print("🔍 安全扫描中…")
    blocked, findings = security_scan()
    if blocked:
        print("\n🚨 SECURITY BLOCKED PUSH")
        print("   检测到可能的敏感信息, 已阻止自动推送:\n")
        for f in findings[:10]:
            print(f"   - {f['file']} :{f['line']}  ({f['pattern']}) {f['masked']}")
        if len(findings) > 10:
            print(f"   … 等共 {len(findings)} 处")
        print("\n   请先处理上述文件(删除/脱敏/移出追踪)后再运行。")
        return 2

    # ── 4. 生成摘要并更新交接文档 ──
    summary = summary_line(changes, stats)
    print(f"✍️  摘要: {summary}")
    update_handoff(summary, changes, stats)

    # ── 5. git add + commit ──
    code, _, err = git("add", "-A")
    if code != 0:
        print(f"git add 失败: {err}")
        return 3

    message = build_commit_message(summary)
    code, _, err = git("commit", "-m", message)
    if code != 0:
        if "nothing to commit" in err:
            print("✓ 提交前发现无变化(可能中途被还原), 退出。")
            return 1
        print(f"git commit 失败: {err}")
        return 3

    _, hash_out, _ = git("rev-parse", "HEAD")
    _, branch, _ = git("branch", "--show-current")
    print(f"✅ Checkpoint created: {hash_out[:12]}")
    print(f"   Branch: {branch or '(detached)'}")
    print(f"   Commit: {message}")

    # ── 6. push ──
    if args.no_push:
        print("(跳过推送 --no-push)")
        return 0

    code, _, err = git("push", "origin", "HEAD")
    if code == 0:
        print("✅ Push successful → origin")
        return 0
    elif "No configured push destination" in err or "does not appear to be a git repository" in err:
        print("⚠ Push 失败: 未配置 remote origin。")
        print("   本地 checkpoint 已保存。配置方式见 .ai-handoff/AUTOMATION_SETUP.md")
        return 0
    else:
        print(f"⚠ Push 失败: {err[:200]}")
        print("   本地 checkpoint 已保存。请检查网络/认证后重试: git push origin HEAD")
        return 0


if __name__ == "__main__":
    sys.exit(main())
