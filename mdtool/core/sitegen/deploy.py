"""git 直推产物仓部署（替代 hexo-deployer-git）。

语义与 hexo-deployer-git 一致：本地维护产物仓的一个工作克隆
（``deploy_dir``），把生成产物同步进去后 commit + push。差异仅在同步由
Python 完成（清空工作区、保留 ``.git``、copytree 产物）。提交身份沿用
git 全局配置（工作流文档既定约定，与 hexo-deployer-git 时期相同）。

分层：:func:`plan_git_deploy` 纯函数产出命令序列（确认对话框展示与执行
同源）；:func:`run_git_deploy` 负责同步文件 + subprocess 逐行 report。
"无变更不推送"的判定在执行期做（``git status --porcelain`` 为空 → 跳过
commit/push），计划序列是上界展示。
"""

from __future__ import annotations

import shutil
import subprocess
from dataclasses import dataclass
from datetime import datetime
from pathlib import Path
from typing import Callable


@dataclass(frozen=True)
class DeployStep:
    argv: tuple[str, ...]
    cwd: Path

    @property
    def display(self) -> str:
        return f"$ {' '.join(self.argv)}   (cwd={self.cwd.name}/)"


@dataclass(frozen=True)
class DeployPlan:
    steps: tuple[DeployStep, ...]
    deploy_dir: Path
    message: str
    clone_first: bool


def plan_git_deploy(*, out_dir: Path, repo_url: str, branch: str = "main",
                    deploy_dir: Path, message: str = "") -> DeployPlan:
    """产出部署命令序列。``deploy_dir`` 已是 git 仓则不重复克隆。"""
    out_dir = Path(out_dir)
    deploy_dir = Path(deploy_dir)
    clone_first = not (deploy_dir / ".git").is_dir()
    msg = message or f"Site updated: {datetime.now().strftime('%Y-%m-%d %H:%M:%S')}"
    steps: list[DeployStep] = []
    if clone_first:
        steps.append(DeployStep(("git", "clone", repo_url, str(deploy_dir)),
                                cwd=deploy_dir.parent))
    steps += [
        DeployStep(("git", "checkout", "-B", branch), cwd=deploy_dir),
        DeployStep(("git", "add", "-A"), cwd=deploy_dir),
        DeployStep(("git", "commit", "-m", msg), cwd=deploy_dir),
        DeployStep(("git", "push", "origin", branch), cwd=deploy_dir),
    ]
    return DeployPlan(tuple(steps), deploy_dir, msg, clone_first)


def sync_out_to_deploy(out_dir: Path, deploy_dir: Path) -> int:
    """产物 → 部署克隆：清空工作区（保留 ``.git``）后整树拷入。

    返回拷入条目数；产物树里没有 ``.git``（build_site 全新生成），不会
    误伤部署仓的版本库。
    """
    out_dir, deploy_dir = Path(out_dir), Path(deploy_dir)
    if not out_dir.is_dir():
        raise FileNotFoundError(f"产物目录不存在: {out_dir}")
    deploy_dir.mkdir(parents=True, exist_ok=True)
    for entry in deploy_dir.iterdir():
        if entry.name == ".git":
            continue
        if entry.is_dir():
            shutil.rmtree(entry)
        else:
            entry.unlink()
    count = 0
    for src in out_dir.rglob("*"):
        rel = src.relative_to(out_dir)
        dst = deploy_dir / rel
        if src.is_dir():
            dst.mkdir(parents=True, exist_ok=True)
        else:
            shutil.copy2(src, dst)
            count += 1
    return count


def run_git_deploy(plan: DeployPlan, *,
                   out_dir: Path,
                   report: Callable = lambda *_a, **_k: None) -> list[tuple[str, int]]:
    """同步产物并执行计划；返回已执行命令的 ``(display, rc)`` 列表。

    ``git add -A`` 之后查 ``status --porcelain``：为空说明产物无变化，
    跳过 commit/push（rc 视为 0 记为 no-op 行）。任一命令非零退出抛
    RuntimeError 中止（克隆失败/推送被拒都不该静默继续）。
    """
    done: list[tuple[str, int]] = []
    if plan.clone_first:
        # 克隆目标目录若存在非 git 残留（上次克隆失败），先清掉
        if plan.deploy_dir.exists() and any(plan.deploy_dir.iterdir()):
            shutil.rmtree(plan.deploy_dir)
        plan.deploy_dir.parent.mkdir(parents=True, exist_ok=True)
    synced = sync_out_to_deploy(out_dir, plan.deploy_dir)
    report("log", msg=f"产物同步完成：{synced} 个文件 → {plan.deploy_dir.name}/")

    for step in plan.steps:
        if step.argv[:2] == ("git", "commit"):
            probe = subprocess.run(
                ("git", "status", "--porcelain"), cwd=str(plan.deploy_dir),
                capture_output=True, text=True, encoding="utf-8", errors="replace")
            if probe.returncode == 0 and not probe.stdout.strip():
                report("log", msg="产物无变更，跳过 commit / push")
                done.append(("$ git commit (no changes)", 0))
                break
        report("log", msg=step.display)
        proc = subprocess.run(step.argv, cwd=str(step.cwd), shell=False,
                              capture_output=True, text=True,
                              encoding="utf-8", errors="replace")
        for line in (proc.stdout or "").splitlines():
            report("log", msg=line)
        err = (proc.stderr or "").strip()
        if err:
            report("log", msg=err, level="WARN")
        done.append((step.display, proc.returncode))
        if proc.returncode != 0:
            raise RuntimeError(f"部署命令失败（退出码 {proc.returncode}）: {step.display}")
    return done
