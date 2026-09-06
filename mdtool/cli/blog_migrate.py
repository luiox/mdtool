"""``mdtool blog-migrate``：hexo 博客仓一次性迁入笔记库博客目录。

计划/执行分离（:mod:`mdtool.core.sitegen.migrate`），``--dry-run`` 打印
计划供人工核对；执行后打印报告（篇数/媒体数/补 id 清单/失联清单）。
"""

import sys
from pathlib import Path

from mdtool.core.sitegen.migrate import apply_hexo_to_kb, plan_hexo_to_kb


def cmd_blog_migrate(src: str, kb: str, dirname: str, dry_run: bool) -> int:
    try:
        plan = plan_hexo_to_kb(Path(src), Path(kb), dirname)
    except ValueError as e:
        print(f"错误: {e}", file=sys.stderr)
        return 2
    print(f"源仓: {plan.src_root}")
    print(f"KB 根: {plan.kb_root}")
    print(f"博客目录 B: {plan.blog_dir}")
    print(f"站点: {plan.spec.title} | {plan.spec.url} | {plan.spec.author}")
    print(f"部署: {plan.deploy_repo or '(未配置)'} branch={plan.deploy_branch}")
    print(f"文章: {len(plan.post_copies)} 篇（清单外补 id {len(plan.unindexed)} 篇）")
    for rel in plan.unindexed:
        print(f"  [补 id] {rel}")
    for rel in plan.drafted:
        print(f"  [退回草稿] {rel}（id 保留，暂不发布）")
    for miss in plan.kept_missing:
        print(f"  [失联保留] {miss}")
    print(f"草稿: {len(plan.draft_copies)} 篇（含退稿；未入册的不进清单）")
    print(f"媒体: {len(plan.asset_copies)} 个 → assets/")
    if dry_run:
        print("dry-run：未写盘")
        return 0
    report = apply_hexo_to_kb(plan)
    print(f"完成：文章 {report.posts}、草稿 {report.drafts}、媒体 {report.assets}")
    print(f"清单: {plan.blog_dir / 'manifest.json'}")
    print(f"配置: {plan.blog_dir / 'site.json'}")
    return 0
