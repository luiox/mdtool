"""sitegen 部署/预览层测试：命令计划（纯函数）、产物同步、内置预览服务器。

git 实际推流不做网络测试——执行层只是 subprocess 薄封装，风险在计划与
同步语义，这里锁死。
"""

from __future__ import annotations

import urllib.request
from pathlib import Path

import pytest

from mdtool.core.sitegen.deploy import (
    plan_git_deploy,
    run_git_deploy,
    sync_out_to_deploy,
)
from mdtool.core.sitegen.legacy import load_legacy_deploy
from mdtool.core.sitegen.preview import SitePreview


# ── 计划：命令序列与展示同源 ─────────────────────────────────────────

def test_plan_git_deploy_fresh_clone(tmp_path):
    out = tmp_path / "public-sitegen"
    deploy_dir = tmp_path / ".deploy_git-sitegen"
    plan = plan_git_deploy(out_dir=out, repo_url="https://github.com/x/y.git",
                           branch="main", deploy_dir=deploy_dir,
                           message="Site updated: 2026-09-06")
    assert plan.clone_first is True
    argvs = [s.argv for s in plan.steps]
    assert argvs[0] == ("git", "clone", "https://github.com/x/y.git", str(deploy_dir))
    assert ("git", "checkout", "-B", "main") in argvs
    assert ("git", "add", "-A") in argvs
    assert ("git", "commit", "-m", "Site updated: 2026-09-06") in argvs
    assert ("git", "push", "origin", "main") in argvs
    # 每一步的 cwd 都是部署目录（clone 一步是父目录）
    assert plan.steps[0].cwd == deploy_dir.parent
    assert all(s.cwd == deploy_dir for s in plan.steps[1:])


def test_plan_git_deploy_existing_repo_skips_clone(tmp_path):
    deploy_dir = tmp_path / "d"
    deploy_dir.mkdir()
    (deploy_dir / ".git").mkdir()
    plan = plan_git_deploy(out_dir=tmp_path / "out", repo_url="https://x",
                           deploy_dir=deploy_dir)
    assert plan.clone_first is False
    assert all(s.argv[1] != "clone" for s in plan.steps)
    # message 缺省自动生成
    assert plan.message.startswith("Site updated: ")


# ── 同步：保留 .git、清陈旧、整树拷入 ────────────────────────────────

def test_sync_out_to_deploy(tmp_path):
    out = tmp_path / "out"
    (out / "article").mkdir(parents=True)
    (out / "index.html").write_text("home", encoding="utf-8")
    (out / "article" / "1.html").write_text("a", encoding="utf-8")

    deploy = tmp_path / "deploy"
    (deploy / ".git").mkdir(parents=True)
    (deploy / ".git" / "HEAD").write_text("ref", encoding="utf-8")
    (deploy / "stale.html").write_text("old", encoding="utf-8")
    (deploy / "olddir").mkdir()

    n = sync_out_to_deploy(out, deploy)
    assert n == 2
    assert (deploy / "index.html").read_text(encoding="utf-8") == "home"
    assert (deploy / "article" / "1.html").is_file()
    assert not (deploy / "stale.html").exists()      # 陈旧产物被清
    assert not (deploy / "olddir").exists()
    assert (deploy / ".git" / "HEAD").is_file()      # 版本库原封不动

    with pytest.raises(FileNotFoundError):
        sync_out_to_deploy(tmp_path / "nope", deploy)


# ── 执行层：无变更不推送（本地裸仓即可测，不碰网络） ──────────────────

def test_run_git_deploy_no_change_skips_push(tmp_path):
    """真 git 仓全流程（推本地 bare 不涉网）：首轮 commit+push 落库，
    二轮内容无变化短路，三轮改动后恢复推送。"""
    import subprocess
    out = tmp_path / "out"
    out.mkdir()
    (out / "index.html").write_text("same", encoding="utf-8")
    bare = tmp_path / "remote.git"
    subprocess.run(("git", "init", "--bare", "-b", "main", str(bare)), check=True,
                   capture_output=True)
    deploy = tmp_path / "deploy"
    subprocess.run(("git", "clone", "-q", str(bare), str(deploy)), check=True,
                   capture_output=True)
    for key, val in (("user.email", "t@t"), ("user.name", "t")):
        subprocess.run(("git", "-C", str(deploy), "config", key, val),
                       check=True, capture_output=True)
    subprocess.run(("git", "-C", str(deploy), "commit", "--allow-empty",
                    "-m", "init", "-q"), check=True, capture_output=True)

    plan = plan_git_deploy(out_dir=out, repo_url=str(bare), branch="main",
                           deploy_dir=deploy)
    report = lambda _stage, **kw: None  # noqa: E731

    # 首轮：init 提交之后产物仍是新内容 → 真实 commit + push
    done1 = run_git_deploy(plan, out_dir=out, report=report)
    assert any(cmd.startswith("$ git commit") for cmd, _rc in done1)
    assert any(cmd.startswith("$ git push") for cmd, _rc in done1)

    # 二轮：内容与远端 HEAD 无差异 → commit/push 被跳过
    done2 = run_git_deploy(plan, out_dir=out, report=report)
    assert any("no changes" in cmd for cmd, _rc in done2)
    assert not any(cmd.startswith("$ git push") for cmd, _rc in done2)

    # 三轮：改动产物后恢复推送，提交信息来自计划
    (out / "index.html").write_text("changed", encoding="utf-8")
    done3 = run_git_deploy(plan, out_dir=out, report=report)
    assert any(cmd.startswith("$ git push") for cmd, _rc in done3)
    head = subprocess.run(("git", "-C", str(deploy), "log", "-1",
                           "--format=%s"), capture_output=True, text=True)
    assert head.stdout.strip() == plan.message


# ── legacy deploy 配置解析 ───────────────────────────────────────────

def test_load_legacy_deploy(tmp_path):
    cfg = tmp_path / "_config.yml"
    cfg.write_text(
        "# Deployment\ndeploy:\n"
        "  type: git\n"
        "  repo: https://github.com/luiox/luiox.github.io.git\n"
        "  branch: main\n"
        "  commit_message: \"Site updated: {{ now('YYYY-MM-DD HH:mm:ss') }}\"\n"
        "\nfeed:\n  type: atom\n", encoding="utf-8")
    assert load_legacy_deploy(tmp_path) == (
        "https://github.com/luiox/luiox.github.io.git", "main")

    cfg.write_text("title: t\ntheme: jacman\n", encoding="utf-8")
    assert load_legacy_deploy(tmp_path) == ("", "main")


# ── 内置预览服务器 ───────────────────────────────────────────────────

def test_site_preview_start_serve_stop(tmp_path):
    (tmp_path / "index.html").write_text("<p>hello sitegen</p>", encoding="utf-8")
    sub = tmp_path / "article"
    sub.mkdir()
    (sub / "1.html").write_text("post", encoding="utf-8")

    preview = SitePreview(tmp_path)
    assert preview.running is False
    port = preview.start()
    assert isinstance(port, int) and port > 0
    assert preview.running is True
    # 幂等 start：同一端口
    assert preview.start() == port

    with urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5) as r:
        body = r.read().decode("utf-8")
    assert "hello sitegen" in body
    with urllib.request.urlopen(f"http://127.0.0.1:{port}/article/1.html",
                                timeout=5) as r:
        assert r.status == 200

    preview.stop()
    assert preview.running is False
    # 重复 stop 幂等（shutdown 兜底路径）
    preview.stop()
    with pytest.raises(urllib.error.URLError):
        # 服务已停：拒连形态随平台文案不同（Windows 是 WinError 10061）
        urllib.request.urlopen(f"http://127.0.0.1:{port}/", timeout=5)
