#!/usr/bin/env node
// npm 入口：把 npm 命令转发给 Python 侧的 install.py / server.py
//   dsh-ubuntu-vm-control install [install.py 的参数...]
//   dsh-ubuntu-vm-control serve              （MCP stdio 模式）
"use strict";

const { spawn, spawnSync } = require("node:child_process");
const fs = require("node:fs");
const path = require("node:path");

const ROOT = path.resolve(__dirname, "..");
const INSTALL_PY = path.join(ROOT, "install.py");
const SERVER_PY = path.join(ROOT, "server", "server.py");

function findPython() {
  const candidates =
    process.platform === "win32"
      ? [["py", ["-3"]], ["python", []], ["python3", []]]
      : [["python3", []], ["python", []]];
  for (const [cmd, prefix] of candidates) {
    const r = spawnSync(cmd, [...prefix, "--version"], { stdio: "ignore" });
    if (!r.error && r.status === 0) return { cmd, prefix };
  }
  return null;
}

function usage() {
  console.log(
    [
      "dsh-ubuntu-vm-control —— 通过 SSH 控制 Ubuntu 桌面虚拟机",
      "",
      "用法：",
      "  dsh-ubuntu-vm-control install [--profile web] [--python <路径>] [--dry-run]",
      "  dsh-ubuntu-vm-control install --uninstall",
      "  dsh-ubuntu-vm-control serve",
      "",
      "说明：",
      "  install  把 server.py 装进 dsh 的 profile，并写入 cordis.patch.yml",
      "  serve    以 MCP stdio 方式直接启动服务，供其它 MCP 客户端使用",
      "",
      "需要本机已安装 Python 3.8+。",
    ].join("\n"),
  );
}

const args = process.argv.slice(2);
const sub = args[0];

if (!sub || sub === "-h" || sub === "--help" || sub === "help") {
  usage();
  process.exit(0);
}

if (sub !== "install" && sub !== "serve") {
  console.error(`未知命令：${sub}\n`);
  usage();
  process.exit(1);
}

const isServe = sub === "serve";
const target = isServe ? SERVER_PY : INSTALL_PY;
const rest = args.slice(1);

if (!fs.existsSync(target)) {
  console.error(`文件缺失：${target}（npm 包不完整，请重新安装）`);
  process.exit(1);
}

const py = findPython();
if (!py) {
  console.error("找不到 Python 3，请先安装 Python 3.8+ 并确保在 PATH 里。");
  process.exit(1);
}

const child = spawn(py.cmd, [...py.prefix, target, ...rest], { stdio: "inherit" });
child.on("exit", (code) => process.exit(code === null ? 1 : code));