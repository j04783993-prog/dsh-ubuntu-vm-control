#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""自检：模拟 MCP 客户端与 server.py 握手、列工具、调用工具。"""
import json
import subprocess
import sys
import time
from pathlib import Path

HERE = Path(__file__).resolve().parent
PY = HERE / ".venv" / "Scripts" / "python.exe"
if not PY.exists():
    PY = Path(sys.executable)


def send(p, obj):
    p.stdin.write((json.dumps(obj, ensure_ascii=False) + "\n").encode("utf-8"))
    p.stdin.flush()


def recv(p, timeout=120):
    line = p.stdout.readline()
    if not line:
        return None
    return json.loads(line.decode("utf-8", "replace"))


proc = subprocess.Popen(
    [str(PY), str(HERE / "server.py")],
    stdin=subprocess.PIPE,
    stdout=subprocess.PIPE,
    stderr=subprocess.PIPE,
)
try:
    send(proc, {"jsonrpc": "2.0", "id": 1, "method": "initialize",
                "params": {"protocolVersion": "2025-06-18", "capabilities": {},
                           "clientInfo": {"name": "selftest", "version": "0.1"}}})
    init = recv(proc)
    print("initialize ->", json.dumps(init, ensure_ascii=False)[:300])

    send(proc, {"jsonrpc": "2.0", "method": "notifications/initialized", "params": {}})

    send(proc, {"jsonrpc": "2.0", "id": 2, "method": "tools/list", "params": {}})
    tools = recv(proc)
    names = [t["name"] for t in tools["result"]["tools"]]
    print(f"tools/list -> {len(names)} 个工具: {', '.join(names)}")

    send(proc, {"jsonrpc": "2.0", "id": 3, "method": "tools/call",
                "params": {"name": "vm_status", "arguments": {}}})
    res = recv(proc)
    text = res["result"]["content"][0].get("text", "")
    print("vm_status ->", text.replace("\n", " | ")[:400])
    print("isError:", res["result"].get("isError"))

    send(proc, {"jsonrpc": "2.0", "id": 4, "method": "tools/call",
                "params": {"name": "list_screenshots", "arguments": {}}})
    res = recv(proc)
    print("list_screenshots ->", res["result"]["content"][0]["text"].replace("\n", " | ")[:200])
finally:
    proc.terminate()
    try:
        err = proc.stderr.read().decode("utf-8", "replace")
        if err.strip():
            print("--- server stderr ---")
            print(err.strip()[:800])
    except Exception:
        pass
