#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
把 dsh-ubuntu-vm-control 安装到本机的 DeepSeek Harness profile。

做的事：
  1. 把 server.py 复制到 ~/.dsh-ubuntu-vm-control/（稳定位置，删掉仓库也不影响）
  2. 在 profile 的 cordis.patch.yml 里写入一条 @deepseek-ai/dsh-mcp-client 配置
     （路径是安装时算出来的绝对路径，所以不依赖 npm / npx）
  3. 改文件前自动备份

用法：
    python install.py                    # 装到 desktop profile
    python install.py --profile web      # 装到别的 profile
    python install.py --python /usr/bin/python3
    python install.py --uninstall        # 只删配置，不动文件
"""

import argparse
import os
import shutil
import sys
import time
from pathlib import Path

SRC_DIR = Path(__file__).resolve().parent
TARGET_DIR = Path.home() / ".dsh-ubuntu-vm-control"
TARGET_SERVER = TARGET_DIR / "server.py"
DSH_HOME = Path.home() / ".dsh"

MARK_BEGIN = "# >>> dsh-ubuntu-vm-control >>>"
MARK_END = "# <<< dsh-ubuntu-vm-control <<<"
ENTRY_ID = "ubuntu-vm-control"
LEGACY_IDS = ("mcp-ubuntu-vm", "ubuntu-vm-control")


def patch_path(profile: str) -> Path:
    return DSH_HOME / "profiles" / profile / "cordis.patch.yml"


def strip_managed(text: str) -> str:
    """去掉之前安装留下的块（含标记块和更早的手写块）。"""
    lines = text.splitlines()
    out = []
    skipping = False
    for line in lines:
        if line.strip() == MARK_BEGIN:
            skipping = True
            continue
        if line.strip() == MARK_END:
            skipping = False
            continue
        if skipping:
            continue
        out.append(line)
    text = "\n".join(out)

    # 移除没有标记的旧块：从 "- insert:" 开始，到下一个顶层 "- " 为止，
    # 且块内出现我们已知的 id
    lines = text.splitlines()
    out = []
    i = 0
    while i < len(lines):
        if lines[i].strip() == "- insert:":
            j = i + 1
            block = [lines[i]]
            while j < len(lines) and not (lines[j].startswith("- ") or lines[j].startswith("-")):
                block.append(lines[j])
                j += 1
            body = "\n".join(block)
            if any(f"id: {eid}" in body for eid in LEGACY_IDS) and "dsh-mcp-client" in body:
                i = j
                continue
        out.append(lines[i])
        i += 1
    return "\n".join(out)


def managed_block(text: str) -> str:
    """取出 install.py 写入的那一段（含标记之内）。"""
    inside = False
    out = []
    for line in text.splitlines():
        if line.strip() == MARK_BEGIN:
            inside = True
            continue
        if line.strip() == MARK_END:
            inside = False
            continue
        if inside:
            out.append(line)
    return "\n".join(out)


def build_block(python_exe: str, server_py: Path) -> str:
    def q(p: str) -> str:
        return "'" + str(p).replace("'", "''") + "'"

    return "\n".join(
        [
            MARK_BEGIN,
            "# 由 install.py 生成，改动会在下次安装时被覆盖",
            "- insert:",
            f"    - id: {ENTRY_ID}",
            "      name: '@deepseek-ai/dsh-mcp-client'",
            "      config:",
            "        serverName: ubuntu",
            "        transport: stdio",
            f"        command: {q(python_exe)}",
            "        args:",
            f"          - {q(str(server_py))}",
            "        failOnStartupError: false",
            MARK_END,
        ]
    )


def main() -> int:
    ap = argparse.ArgumentParser()
    ap.add_argument("--profile", default="desktop", help="dsh profile 名，默认 desktop")
    ap.add_argument("--python", default=sys.executable, help="用哪个 Python 解释器跑服务")
    ap.add_argument("--uninstall", action="store_true", help="移除配置")
    ap.add_argument("--dry-run", action="store_true", help="只打印结果，不写文件")
    ap.add_argument("--force", action="store_true", help="即使当前是 npm 包模式，也强制改回 Python 直跑")
    ap.add_argument(
        "--install-dir",
        default=None,
        help=f"server.py 放在哪里，默认 {TARGET_DIR}",
    )
    args = ap.parse_args()

    cfg = patch_path(args.profile)
    if not cfg.exists():
        print(f"找不到 {cfg}")
        print("请确认 dsh 已经启动过一次，profile 名字是否正确（--profile 指定）。")
        return 1

    raw = cfg.read_text(encoding="utf-8")
    cleaned = strip_managed(raw)

    if args.uninstall:
        if cleaned == raw:
            print("配置里没有找到本插件的条目，无需清理。")
            return 0
        backup = cfg.with_suffix(cfg.suffix + f".bak-{time.strftime('%Y%m%d-%H%M%S')}")
        shutil.copy2(cfg, backup)
        cfg.write_text(cleaned.rstrip() + "\n", encoding="utf-8")
        print(f"已移除配置。备份：{backup}")
        print("重启 dsh 生效。")
        return 0

    block = managed_block(raw)
    if block and "cli.js" in block and not args.force:
        print("检测到当前的 dsh 配置走的是 npm 包模式（node + cli.js）。")
        print("继续安装会把它改回 Python 直跑模式，可能和已安装的 npm 包冲突。")
        print("确实要改请加 --force；只想移除配置请用 --uninstall。")
        return 1

    src_server = SRC_DIR / "server" / "server.py"
    if not src_server.exists():
        print(f"仓库不完整，找不到 {src_server}")
        return 1

    target_dir = Path(args.install_dir).expanduser().resolve() if args.install_dir else TARGET_DIR
    target_server = target_dir / "server.py"
    target_dir.mkdir(parents=True, exist_ok=True)

    stamp = time.strftime("%Y%m%d-%H%M%S")
    if target_server.exists() and target_server.read_bytes() != src_server.read_bytes():
        old = target_server.with_suffix(f".py.bak-{stamp}")
        shutil.copy2(target_server, old)
        print(f"目标位置已有不同版本，已备份：{old}")

    shutil.copy2(src_server, target_server)
    print(f"服务已复制到 {target_server}")

    block = build_block(args.python, target_server)
    backup = cfg.with_suffix(cfg.suffix + f".bak-{time.strftime('%Y%m%d-%H%M%S')}")

    if args.dry_run:
        print("=" * 60)
        print(cleaned.rstrip() + "\n\n" + block + "\n")
        print("=" * 60)
        print("（--dry-run，未写入任何文件）")
        return 0

    shutil.copy2(cfg, backup)
    cfg.write_text(cleaned.rstrip() + "\n\n" + block + "\n", encoding="utf-8")

    print(f"配置已写入 {cfg}")
    print(f"  解释器：{args.python}")
    print(f"  备份：  {backup}")
    print()
    print("下一步：")
    print("  1. 完全退出 dsh 再重新打开（不是刷新窗口）")
    print("  2. 在 dsh 里问：调 vm_status 看看虚拟机环境")
    print()
    print("如果提示未配置主机，先设置：")
    print(f"  {target_dir / 'config.json'}  或环境变量 VM_HOST")
    return 0


if __name__ == "__main__":
    sys.exit(main())
