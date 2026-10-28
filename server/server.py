#!/usr/bin/env python3
# -*- coding: utf-8 -*-
"""
Ubuntu VM Control MCP Server —— 零依赖版

不依赖任何第三方库，也不依赖 Docker：
  * SSH / SCP 直接用 Windows 自带的 OpenSSH（C:\\Windows\\System32\\OpenSSH\\ssh.exe）
  * MCP 协议（stdio，按行分隔的 JSON-RPC 2.0）自己实现

能力：执行命令、开终端、键鼠模拟、窗口管理、截图并保存成本地图片文件。
"""

from __future__ import annotations

import base64
import json
import os
import shlex
import shutil
import socket
import subprocess
import sys
import time
from datetime import datetime
from pathlib import Path
from typing import Any

BASE = Path(__file__).resolve().parent
CONFIG_PATH = BASE / "config.json"
USER_DIR = Path.home() / ".dsh-ubuntu-vm-control"
USER_CONFIG = USER_DIR / "config.json"
SERVER_NAME = "ubuntu-vm-control"
SERVER_VERSION = "0.1.1"
SUPPORTED_PROTOCOLS = ("2025-06-18", "2025-03-26", "2024-11-05")

DEFAULT_CONFIG = {
    "mode": "auto",                # auto | local（跑在被控机器上）| ssh（通过 SSH 控制远端）
    "host": "",                    # 必填：被控机器地址（也可用 VM_HOST 环境变量）
    "port": 22,
    "user": "ubuntu",
    "key_path": str(Path.home() / ".ssh" / "id_ed25519"),
    "display": ":0",
    "xauthority": "",
    "screenshot_dir": str(Path.home() / "vm-shots"),
    "save_remote": False,
    "remote_dir": "~/vm-shots",
    "max_width": 1600,
    "command_timeout": 60,
}

CREATE_NO_WINDOW = 0x08000000 if os.name == "nt" else 0


# --------------------------------------------------------------------------- #
# 配置
# --------------------------------------------------------------------------- #
def log(msg: str) -> None:
    # 绝不能写 stdout（那是 MCP 通道）
    print(f"[{SERVER_NAME}] {msg}", file=sys.stderr, flush=True)


def load_config() -> dict[str, Any]:
    cfg = dict(DEFAULT_CONFIG)
    # 优先级：包内 config.json < 用户级 ~/.dsh-ubuntu-vm-control/config.json < 环境变量
    for path in (CONFIG_PATH, USER_CONFIG):
        if not path.exists():
            continue
        try:
            raw = json.loads(path.read_text(encoding="utf-8"))
            raw.pop("auth", None)              # 旧版嵌套写法，已废弃
            cfg.update(raw)
        except Exception as exc:
            log(f"{path} 解析失败: {exc}")
    for env, key, cast in (
        ("VM_HOST", "host", str),
        ("VM_PORT", "port", int),
        ("VM_USER", "user", str),
        ("VM_SSH_KEY", "key_path", str),
        ("VM_DISPLAY", "display", str),
        ("VM_SCREENSHOT_DIR", "screenshot_dir", str),
        ("VM_MAX_WIDTH", "max_width", int),
    ):
        val = os.environ.get(env)
        if val:
            try:
                cfg[key] = cast(val)
            except ValueError:
                pass
    return cfg


CFG = load_config()
SHOT_DIR = Path(os.path.expandvars(os.path.expanduser(CFG["screenshot_dir"])))
SHOT_DIR.mkdir(parents=True, exist_ok=True)


def find_exe(name: str) -> str:
    found = shutil.which(name)
    if found:
        return found
    for cand in (
        Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / "OpenSSH" / f"{name}.exe",
        Path(os.environ.get("SystemRoot", r"C:\Windows")) / "System32" / f"{name}.exe",
    ):
        if cand.exists():
            return str(cand)
    raise FileNotFoundError(f"找不到 {name}.exe，请确认 Windows 已安装 OpenSSH 客户端")


# local：直接在这台机器上跑（server 部署在 VM 里）
# ssh：通过 ssh 操作远端 VM
# auto：配了 host 就走 ssh；没配 host 就认为服务跑在被控机本身上，走 local
MODE = str(CFG.get("mode") or "auto").lower()
if MODE == "auto":
    MODE = "ssh" if str(CFG.get("host") or "").strip() else "local"
LOCAL = MODE == "local"

SSH = None if LOCAL else find_exe("ssh")
SCP = None if LOCAL else find_exe("scp")

SSH_OPTS = [
    "-o", "BatchMode=yes",
    "-o", "StrictHostKeyChecking=accept-new",
    "-o", "ConnectTimeout=10",
    "-o", "ServerAliveInterval=15",
    "-o", "LogLevel=ERROR",
]


# --------------------------------------------------------------------------- #
# SSH 执行
# --------------------------------------------------------------------------- #
class SSHError(RuntimeError):
    pass


def _q(s: Any) -> str:
    return shlex.quote(str(s))


def _target() -> str:
    return f"{CFG['user']}@{CFG['host']}"


def _key_args() -> list[str]:
    key = str(CFG.get("key_path") or "").strip()
    key = os.path.expanduser(key)
    if key and Path(key).exists():
        return ["-i", key]
    return []


def ssh_run(command: str, timeout: float | None = None, gui: bool = True) -> tuple[int, str, str]:
    timeout = float(timeout or CFG.get("command_timeout") or 60)
    prefix = "export LANG=C.UTF-8; "
    if gui:
        prefix += f"export DISPLAY={_q(CFG.get('display') or ':0')}; "
        xauth = CFG.get("xauthority") or ""
        if xauth:
            prefix += f"export XAUTHORITY={_q(xauth)}; "
    if LOCAL:
        args = ["bash", "-lc", prefix + command]
    else:
        remote = "bash -lc " + _q(prefix + command)
        args = [SSH, *SSH_OPTS, "-p", str(CFG["port"]), *_key_args(), _target(), remote]
    try:
        proc = subprocess.run(
            args,
            capture_output=True,
            timeout=timeout + 15,
            encoding="utf-8",
            errors="replace",
            creationflags=CREATE_NO_WINDOW,
        )
    except subprocess.TimeoutExpired:
        raise SSHError(f"命令执行超时（{timeout:.0f}s）：{command[:80]}")
    return proc.returncode, proc.stdout or "", proc.stderr or ""


def run_text(command: str, timeout: float | None = None) -> str:
    code, out, err = ssh_run(command, timeout=timeout)
    parts = [f"exit_code: {code}"]
    if out.strip():
        parts.append("stdout:\n" + out.rstrip())
    if err.strip():
        parts.append("stderr:\n" + err.rstrip())
    return "\n".join(parts)


def scp_get(remote: str, local: Path) -> None:
    local.parent.mkdir(parents=True, exist_ok=True)
    if LOCAL:
        try:
            shutil.copyfile(os.path.expanduser(remote), str(local))
        except Exception as exc:
            raise SSHError(f"复制文件失败：{exc}")
        return
    args = [SCP, *SSH_OPTS, "-P", str(CFG["port"]), *_key_args(), f"{_target()}:{remote}", str(local)]
    proc = subprocess.run(
        args, capture_output=True, timeout=120, encoding="utf-8", errors="replace",
        creationflags=CREATE_NO_WINDOW,
    )
    if proc.returncode != 0 or not local.exists():
        raise SSHError(f"scp 下载失败：{(proc.stderr or '').strip()}")


def scp_put(local: Path, remote: str) -> None:
    if LOCAL:
        dest = Path(os.path.expanduser(remote))
        dest.parent.mkdir(parents=True, exist_ok=True)
        try:
            shutil.copyfile(str(local), str(dest))
        except Exception as exc:
            raise SSHError(f"复制文件失败：{exc}")
        return
    args = [SCP, *SSH_OPTS, "-P", str(CFG["port"]), *_key_args(), str(local), f"{_target()}:{remote}"]
    proc = subprocess.run(
        args, capture_output=True, timeout=180, encoding="utf-8", errors="replace",
        creationflags=CREATE_NO_WINDOW,
    )
    if proc.returncode != 0:
        raise SSHError(f"scp 上传失败：{(proc.stderr or '').strip()}")


def ensure_ssh() -> str | None:
    """连接自检，返回错误信息（None 表示正常）。"""
    if not LOCAL and not str(CFG.get("host") or "").strip():
        return (
            "还没有配置被控主机。任选一种方式：\n"
            "  1) 环境变量：VM_HOST=192.168.1.10（可选 VM_USER / VM_PORT / VM_SSH_KEY）\n"
            f"  2) 配置文件：{USER_CONFIG}\n"
            '     内容示例：{"host": "192.168.1.10", "user": "ubuntu"}\n'
            "配好后重启 dsh。"
        )
    try:
        code, out, err = ssh_run("echo __OK__", timeout=20, gui=False)
    except Exception as exc:
        return str(exc)
    if code != 0 or "__OK__" not in out:
        hint = (err or out).strip()
        if LOCAL:
            return f"本机命令执行失败（local 模式）：{hint or 'exit ' + str(code)}"
        if "Permission denied" in hint or "no such identity" in hint:
            return (
                "SSH 认证失败。本服务只支持密钥登录（BatchMode）。请在 Windows 上执行一次：\n"
                f'  type %USERPROFILE%\\.ssh\\id_ed25519.pub | ssh {_target()} '
                '"mkdir -p ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"'
            )
        if "timed out" in hint.lower() or "Connection refused" in hint:
            return f"连不上 {_target()}:{CFG['port']} —— VM 是否开机？IP 是否变了？"
        return hint or f"ssh 退出码 {code}"
    return None


# --------------------------------------------------------------------------- #
# 工具实现
# --------------------------------------------------------------------------- #
def _text(t: str) -> dict[str, Any]:
    return {"type": "text", "text": t}


def _fail(t: str) -> dict[str, Any]:
    return {"content": [_text("❌ " + t)], "isError": True}


def t_vm_status(args: dict[str, Any]) -> dict[str, Any]:
    err = ensure_ssh()
    if err:
        return _fail(err)
    # 注意：SSH 会话里的 $XDG_SESSION_TYPE 是空的；而且 loginctl 里除了桌面会话还有 tty 会话，
    # 必须挑出类型是 x11/wayland 的那个，否则会误判成「没有桌面」
    probe = (
        "echo HOST=$(hostname); echo WHO=$(whoami); "
        "sid=$(for s in $(loginctl list-sessions --no-legend 2>/dev/null | awk -v u=$USER '$3==u{print $1}'); "
        'do t=$(loginctl show-session $s -p Type --value 2>/dev/null); '
        'case "$t" in x11|wayland) echo $s; break;; esac; done); '
        'echo GTYPE=$(loginctl show-session "$sid" -p Type --value 2>/dev/null); '
        "echo XORG=$(pgrep -x Xorg >/dev/null && echo yes || echo no); "
        "echo XWAYLAND=$(pgrep -x Xwayland >/dev/null && echo yes || echo no); "
        "echo SCROT=$(command -v scrot || command -v gnome-screenshot); "
        "echo XDOTOOL=$(command -v xdotool); echo WMCTRL=$(command -v wmctrl); "
        "echo CONVERT=$(command -v convert); "
        "xrandr 2>/dev/null | grep -m1 '\\*'"
    )
    code, out, errout = ssh_run(probe, timeout=30)
    where = "本机（local 模式）" if LOCAL else f"{_target()}:{CFG['port']}（ssh 模式）"
    lines = [f"连接正常：{where}", (out or errout).strip()]
    checks = {}
    for line in out.splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            checks[k.strip()] = v.strip()

    gtype = (checks.get("GTYPE") or "").lower()
    has_xorg = checks.get("XORG") == "yes"

    if not checks.get("XDOTOOL"):
        lines.append("⚠️ 缺少 xdotool：sudo apt install -y xdotool")
    if not checks.get("SCROT"):
        lines.append("⚠️ 缺少截图工具：sudo apt install -y scrot")

    if gtype == "wayland":
        lines.append(
            "⚠️ 桌面是 Wayland 会话：截图仍可用（走 GNOME DBus 接口），"
            "但 xdotool 的鼠标键盘只能作用于 Xwayland 程序，原生 Wayland 程序（如 Firefox、GNOME 终端）收不到。"
            "需要完整键鼠控制请在 VM 里装一个 X11 桌面：sudo apt install -y xfce4，登录时选 “Xfce Session”。"
        )
    elif gtype == "x11" or has_xorg:
        lines.append(f"✅ 桌面是 X11 会话（{gtype or 'Xorg 进程在跑'}），xdotool/scrot 可用。")
    else:
        lines.append(
            "⚠️ 没检测到图形桌面会话（GTYPE 为空且没有 Xorg 进程）。"
            "可能 VM 停在登录界面没登录，或者桌面用的是 Wayland。"
        )
    lines.append(f"本地截图目录：{SHOT_DIR}")
    return {"content": [_text("\n".join(lines))]}


def t_execute_command(args: dict[str, Any]) -> dict[str, Any]:
    cmd = str(args.get("command") or "")
    if not cmd.strip():
        return _fail("command 不能为空")
    timeout = float(args.get("timeout") or CFG.get("command_timeout") or 60)
    cwd = str(args.get("cwd") or "").strip()
    if cwd:
        cmd = f"cd {_q(cwd)} || exit 127; " + cmd
    code, out, err = ssh_run(cmd, timeout=timeout)
    parts = [f"exit_code: {code}"]
    if out.strip():
        parts.append("stdout:\n" + out.rstrip())
    if err.strip():
        parts.append("stderr:\n" + err.rstrip())
    return {"content": [_text("\n".join(parts))], "isError": code != 0}


def t_open_terminal(args: dict[str, Any]) -> dict[str, Any]:
    inner = str(args.get("command") or "")
    shell_cmd = f"bash -c {_q(inner + '; exec bash')}" if inner else "bash"
    attempts = [
        f"nohup dbus-launch gnome-terminal -- {shell_cmd} >/dev/null 2>&1 &",
        f"nohup gnome-terminal -- {shell_cmd} >/dev/null 2>&1 &",
        f"nohup xfce4-terminal --command {_q(inner + '; exec bash') if inner else _q('bash')} >/dev/null 2>&1 &",
        f"nohup x-terminal-emulator -e {shell_cmd} >/dev/null 2>&1 &",
        f"nohup xterm -e {shell_cmd} >/dev/null 2>&1 &",
    ]
    for cmd in attempts:
        ssh_run(cmd, timeout=20)
        time.sleep(1.8)
        _, title, _ = ssh_run("xdotool getactivewindow getwindowname 2>/dev/null", timeout=15)
        if title.strip():
            return {"content": [_text(f"已打开终端，当前活动窗口：{title.strip()}")]}
    return _fail("没能打开终端窗口，请确认桌面已登录且装有 gnome-terminal / xfce4-terminal / xterm。")


def _dbus_prefix() -> str:
    """SSH 会话默认连不上桌面的 session bus，要手动指过去。"""
    return (
        'export XDG_RUNTIME_DIR=/run/user/$(id -u); '
        'export DBUS_SESSION_BUS_ADDRESS=unix:path=/run/user/$(id -u)/bus; '
    )


_ENV_CACHE = {}


def probe_env(force: bool = False) -> dict[str, str]:
    """一次性探明远端环境（会话类型、可用工具、屏幕宽度），之后缓存复用。"""
    if _ENV_CACHE and not force:
        return _ENV_CACHE
    cmd = (
        "echo XORG=$(pgrep -x Xorg >/dev/null && echo yes || echo no); "
        "echo SCROT=$(command -v scrot); "
        "echo CONVERT=$(command -v convert); "
        "echo XDOTOOL=$(command -v xdotool); "
        "echo WMCTRL=$(command -v wmctrl); "
        "echo GDBUS=$(command -v gdbus); "
        "echo XFCE_TERM=$(command -v xfce4-terminal); "
        "echo GNOME_TERM=$(command -v gnome-terminal); "
        "echo WIDTH=$(xrandr 2>/dev/null | awk '/\\*/{print $1; exit}' | cut -dx -f1)"
    )
    code, out, err = ssh_run(cmd, timeout=30)
    env: dict[str, str] = {}
    for line in out.splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            env[k.strip()] = v.strip()
    if not env.get("WIDTH"):
        log(f"探测屏幕宽度失败：{(err or out).strip()[:200]}")
    _ENV_CACHE.update(env)
    return _ENV_CACHE


def _window_frame(pattern: str) -> dict[str, str]:
    """按标题/类名找窗口，返回它「含标题栏的外框」几何信息。

    坑：xdotool getwindowgeometry 给的是客户区（不含标题栏），
    直接截会切掉标题栏。要沿父窗口（WM 的 frame）取几何才对。
    """
    p = _q(pattern)
    cmd = (
        f'xid=$(xdotool search --onlyvisible --name {p} 2>/dev/null | tail -1); '
        '[ -n "$xid" ] || { echo "ERR=没找到标题匹配的窗口"; exit 0; }; '
        'xdotool windowactivate "$xid" 2>/dev/null; sleep 0.4; '
        'parent=$(xwininfo -id "$xid" -tree 2>/dev/null | awk \'/Parent window id:/{print $4}\'); '
        'geom=""; '
        'if [ -n "$parent" ]; then geom=$(xdotool getwindowgeometry --shell "$parent" 2>/dev/null); fi; '
        'case "$geom" in *WIDTH*) ;; *) geom=$(xdotool getwindowgeometry --shell "$xid") ;; esac; '
        'eval "$geom"; '
        'echo "XID=$xid"; echo "TITLE=$(xdotool getwindowname "$xid")"; '
        'echo "WX=$X"; echo "WY=$Y"; echo "WW=$WIDTH"; echo "WH=$HEIGHT"'
    )
    code, out, err = ssh_run(cmd, timeout=30)
    info: dict[str, str] = {}
    for line in out.splitlines():
        if "=" in line:
            k, _, v = line.partition("=")
            info[k.strip()] = v.strip()
    if "ERR" in info:
        raise SSHError(f"窗口定位失败：{info['ERR']}（pattern={pattern}）")
    for k in ("WX", "WY", "WW", "WH"):
        if not info.get(k):
            raise SSHError(f"取窗口几何失败：{(err or out).strip()[:200]}")
    return info


def _capture_remote(region: str | None, window: str | None = None) -> tuple[str, str]:
    """在远端截图，返回 (远端文件路径, 使用的方式)。

    按当前会话类型挑最合适的后端：
      * X11 + scrot  → scrot（最快、最稳）
      * 其余（含 Wayland）→ GNOME Shell 的 DBus 接口，X11/Wayland 通吃
      * 再兜底 gnome-screenshot / ImageMagick import

    window 给定时只截那个窗口（自动激活 + 取含标题栏的外框）。
    """
    env = probe_env()
    stamp = int(time.time() * 1000)
    remote = f"/tmp/.vmctl_shot_{stamp}.png"
    how = ""
    if window:
        info = _window_frame(window)
        region = f"{info['WX']},{info['WY']},{info['WW']},{info['WH']}"
        how = f"窗口「{info.get('TITLE') or window}」"
    region_arg = f"-a {region} " if region else ""

    attempts: list[tuple[str, str]] = []
    if env.get("XORG") == "yes" and env.get("SCROT"):
        attempts.append(("scrot", f"scrot -o -q 92 {region_arg}{remote}"))
    if env.get("GDBUS"):
        dbus = _dbus_prefix()
        if region:
            x, y, w, h = [p.strip() for p in region.split(",")]
            gdbus = (
                dbus + "gdbus call --session --dest org.gnome.Shell.Screenshot "
                "--object-path /org/gnome/Shell/Screenshot "
                f"--method org.gnome.Shell.Screenshot.ScreenshotArea {x} {y} {w} {h} false {remote}"
            )
        else:
            gdbus = (
                dbus + "gdbus call --session --dest org.gnome.Shell.Screenshot "
                "--object-path /org/gnome/Shell/Screenshot "
                f"--method org.gnome.Shell.Screenshot.Screenshot false false {remote}"
            )
        attempts.append(("gdbus(GNOME)", gdbus))
    attempts.append(("gnome-screenshot", f"gnome-screenshot -f {remote}"))
    attempts.append(("import", f"import -window root {remote}"))

    last = ""
    for label, cmd in attempts:
        code, out, err = ssh_run(cmd, timeout=40)
        if code == 0:
            chk_code, chk_out, _ = ssh_run(f"[ -s {remote} ] && echo yes", timeout=15, gui=False)
            if "yes" in chk_out:
                return remote, (how + " / " if how else "") + label
            last = f"{label} → 未生成文件"
        else:
            last = f"{label} → {(err or out).strip() or 'exit ' + str(code)}"
    raise SSHError("截图失败：" + last)


def _push_to_vm(local: Path) -> str | None:
    """把截图再传一份到 VM 里。Trae 走 SSH 工作区时，只有 VM 上的文件它才读得到。"""
    if LOCAL:
        return str(local)          # 本来就在 VM 上，不用再传
    if not CFG.get("save_remote"):
        return None
    rdir = str(CFG.get("remote_dir") or "~/vm-shots").rstrip("/")
    try:
        ssh_run(f"mkdir -p {rdir}", timeout=20, gui=False)
        scp_put(local, f"{rdir}/{local.name}")
        _, out, _ = ssh_run(f"cd {rdir} && pwd", timeout=15, gui=False)
        base = out.strip() or rdir
        return f"{base}/{local.name}"
    except Exception as exc:
        log(f"回传 VM 失败：{exc}")
        return None


def _resize_local(src: Path, max_width: int) -> Path:
    """缩图并存 JPEG。Windows 用 .NET System.Drawing，Linux 用 PIL。失败就保留原图。"""
    if max_width <= 0:
        return src
    if os.name != "nt":
        try:
            from PIL import Image as _PILImage

            dst = src.with_suffix(".jpg")
            im = _PILImage.open(src)
            if im.width > max_width:
                h = max(1, int(im.height * max_width / im.width))
                im = im.resize((max_width, h), _PILImage.LANCZOS)
            if im.mode != "RGB":
                im = im.convert("RGB")
            im.save(dst, "JPEG", quality=85)
            src.unlink(missing_ok=True)
            return dst
        except Exception as exc:
            log(f"PIL 缩图失败（保留原图）：{exc}")
            return src
    dst = src.with_suffix(".jpg")
    script = (
        "Add-Type -AssemblyName System.Drawing; "
        f"$img=[System.Drawing.Image]::FromFile('{src}'); "
        f"$w={max_width}; "
        "if($img.Width -le $w){$w=$img.Width}; "
        "$h=[int]($img.Height*$w/$img.Width); "
        "$bmp=New-Object System.Drawing.Bitmap $w,$h; "
        "$g=[System.Drawing.Graphics]::FromImage($bmp); "
        "$g.InterpolationMode='HighQualityBicubic'; "
        "$g.DrawImage($img,0,0,$w,$h); "
        "$codec=[System.Drawing.Imaging.ImageCodecInfo]::GetImageEncoders() | "
        "Where-Object {$_.MimeType -eq 'image/jpeg'}; "
        "$ep=New-Object System.Drawing.Imaging.EncoderParameters(1); "
        "$ep.Param[0]=New-Object System.Drawing.Imaging.EncoderParameter("
        "[System.Drawing.Imaging.Encoder]::Quality,85); "
        f"$bmp.Save('{dst}',$codec,$ep); "
        "$g.Dispose(); $bmp.Dispose(); $img.Dispose()"
    )
    try:
        proc = subprocess.run(
            ["powershell", "-NoProfile", "-NonInteractive", "-Command", script],
            capture_output=True, timeout=90, encoding="utf-8", errors="replace",
            creationflags=CREATE_NO_WINDOW,
        )
        if proc.returncode == 0 and dst.exists() and dst.stat().st_size > 0:
            src.unlink(missing_ok=True)
            return dst
        log(f"本地缩放失败：{(proc.stderr or '').strip()[:300]}")
    except Exception as exc:
        log(f"本地缩放异常：{exc}")
    return src


def t_take_screenshot(args: dict[str, Any]) -> dict[str, Any]:
    name = str(args.get("name") or "").strip()
    region = str(args.get("region") or "").strip()
    window = str(args.get("window") or "").strip()
    inline = args.get("inline", True)
    max_width = int(args.get("max_width") or CFG.get("max_width") or 0)

    try:
        remote, how = _capture_remote(region or None, window or None)
    except Exception as exc:
        return _fail(str(exc))

    safe = "".join(c for c in name if c.isalnum() or c in "-_") or datetime.now().strftime("%Y%m%d-%H%M%S")
    ext = ".png"

    out_path = SHOT_DIR / f"{safe}{ext}"
    i = 1
    while out_path.exists():
        out_path = SHOT_DIR / f"{safe}-{i}{ext}"
        i += 1

    try:
        scp_get(remote, out_path)
    except Exception as exc:
        return _fail(str(exc))
    finally:
        ssh_run(f"rm -f {remote}", timeout=15, gui=False)

    # 缩图 + 转 JPEG：2140×1360 的 PNG 往往 1MB+，缩到 1600 宽存 JPEG 只有一两百 KB
    if max_width:
        resized = _resize_local(out_path, max_width)
        if resized != out_path:
            out_path, ext = resized, ".jpg"

    vm_path = _push_to_vm(out_path)

    size_kb = out_path.stat().st_size / 1024
    if LOCAL:
        lines = [f"截图已保存（{size_kb:.0f} KB，方式：{how}）", f"  {out_path}"]
    else:
        lines = [f"截图已保存（{size_kb:.0f} KB，方式：{how}）", f"  Windows：{out_path}"]
        if vm_path:
            lines.append(f"  虚拟机：{vm_path}")
            lines.append("（Trae 走 SSH 连虚拟机时，报告里请用虚拟机那条路径）")
    info = "\n".join(lines)

    if not inline:
        return {"content": [_text(info + "\n已按要求不内联返回图片，需要查看时读取该文件即可。")]}

    mime = "image/jpeg" if ext == ".jpg" else "image/png"
    data = base64.b64encode(out_path.read_bytes()).decode("ascii")
    return {"content": [{"type": "image", "data": data, "mimeType": mime}, _text(info)]}


def t_list_screenshots(args: dict[str, Any]) -> dict[str, Any]:
    files = [p for p in SHOT_DIR.glob("*") if p.suffix.lower() in (".png", ".jpg", ".jpeg")]
    files.sort(key=lambda p: p.stat().st_mtime, reverse=True)
    if not files:
        return {"content": [_text(f"目录 {SHOT_DIR} 下还没有截图。")]}
    lines = [f"截图目录：{SHOT_DIR}"]
    for p in files[:60]:
        st = p.stat()
        lines.append(
            f"- {p.name}  {st.st_size / 1024:.0f} KB  {datetime.fromtimestamp(st.st_mtime):%Y-%m-%d %H:%M:%S}"
        )
    return {"content": [_text("\n".join(lines))]}


def t_click(args: dict[str, Any]) -> dict[str, Any]:
    x, y = int(args.get("x", 0)), int(args.get("y", 0))
    btn = {"left": "1", "middle": "2", "right": "3"}.get(str(args.get("button", "left")).lower(), "1")
    rep = " --repeat 2 --delay 120" if args.get("double") else ""
    return {"content": [_text(run_text(f"xdotool mousemove {x} {y} click{rep} {btn}"))]}


def t_move_mouse(args: dict[str, Any]) -> dict[str, Any]:
    return {"content": [_text(run_text(f"xdotool mousemove {int(args.get('x', 0))} {int(args.get('y', 0))}"))]}


def t_drag_mouse(args: dict[str, Any]) -> dict[str, Any]:
    b = {"left": "1", "middle": "2", "right": "3"}.get(str(args.get("button", "left")).lower(), "1")
    cmd = (
        f"xdotool mousemove {int(args.get('x1', 0))} {int(args.get('y1', 0))} mousedown {b} "
        f"mousemove {int(args.get('x2', 0))} {int(args.get('y2', 0))} mouseup {b}"
    )
    return {"content": [_text(run_text(cmd))]}


def t_type_text(args: dict[str, Any]) -> dict[str, Any]:
    text = str(args.get("text") or "")
    delay = int(args.get("delay_ms") or 12)
    return {"content": [_text(run_text(f"xdotool type --clearmodifiers --delay {delay} {_q(text)}"))]}


def t_press_key(args: dict[str, Any]) -> dict[str, Any]:
    key = str(args.get("key") or "")
    repeat = int(args.get("repeat") or 1)
    rep = f" --repeat {repeat}" if repeat > 1 else ""
    return {"content": [_text(run_text(f"xdotool key{rep} {_q(key)}"))]}


def t_press_hotkey(args: dict[str, Any]) -> dict[str, Any]:
    return {"content": [_text(run_text(f"xdotool key --clearmodifiers {_q(args.get('keys') or '')}"))]}


def t_scroll(args: dict[str, Any]) -> dict[str, Any]:
    btn = "4" if str(args.get("direction", "down")).lower() == "up" else "5"
    amount = int(args.get("amount") or 3)
    return {"content": [_text(run_text(f"xdotool click --repeat {amount} --delay 60 {btn}"))]}


def t_get_active_window_title(args: dict[str, Any]) -> dict[str, Any]:
    code, out, err = ssh_run("xdotool getactivewindow getwindowname 2>&1", timeout=20)
    title = out.strip() or err.strip()
    return {"content": [_text(f"当前活动窗口：{title or '(未取到)'}")]}


def t_list_windows(args: dict[str, Any]) -> dict[str, Any]:
    return {"content": [_text(run_text("wmctrl -lp 2>/dev/null || xdotool search --onlyvisible --name '.'"))]}


def t_focus_window(args: dict[str, Any]) -> dict[str, Any]:
    title = str(args.get("title") or "")
    cmd = (
        f"wid=$(xdotool search --name {_q(title)} | head -1); "
        'if [ -n "$wid" ]; then xdotool windowactivate --sync "$wid" && echo "已激活窗口 $wid"; '
        f'else echo "未找到标题包含 {title} 的窗口"; fi'
    )
    return {"content": [_text(run_text(cmd))]}


def t_get_screen_info(args: dict[str, Any]) -> dict[str, Any]:
    code, out, err = ssh_run(
        "xrandr 2>/dev/null | grep -m3 ' connected\\|\\*'; "
        'echo SESSION=${XDG_SESSION_TYPE:-unknown}; echo DISPLAY=$DISPLAY',
        timeout=20,
    )
    return {"content": [_text(out.strip() or err.strip() or "(无输出)")]}


def t_upload_file(args: dict[str, Any]) -> dict[str, Any]:
    src = Path(os.path.expanduser(str(args.get("local_path") or "")))
    dest = str(args.get("remote_path") or "")
    if not src.exists():
        return _fail(f"本地文件不存在：{src}")
    if not dest:
        return _fail("remote_path 不能为空")
    if dest.endswith("/"):
        dest = dest.rstrip("/") + "/" + src.name
    try:
        scp_put(src, dest)
        return {"content": [_text(f"已上传：{src} → {dest}")]}
    except Exception as exc:
        return _fail(str(exc))


def t_download_file(args: dict[str, Any]) -> dict[str, Any]:
    remote = str(args.get("remote_path") or "")
    local = str(args.get("local_path") or "")
    if not remote:
        return _fail("remote_path 不能为空")
    name = remote.rstrip("/").split("/")[-1]
    dest = Path(os.path.expanduser(local)) if local else Path.home() / "Downloads" / name
    if dest.is_dir():
        dest = dest / name
    try:
        scp_get(remote, dest)
        return {"content": [_text(f"已下载：{remote} → {dest}")]}
    except Exception as exc:
        return _fail(str(exc))


# --------------------------------------------------------------------------- #
# 工具注册表
# --------------------------------------------------------------------------- #
TOOLS = [
    {
        "name": "vm_status",
        "description": "检查与 Ubuntu 虚拟机的连接和环境是否就绪（SSH、X11/Wayland、xdotool/scrot 是否安装、分辨率）。做任何截图或操作前先调用它排查问题。",
        "inputSchema": {"type": "object", "properties": {}},
        "handler": t_vm_status,
    },
    {
        "name": "execute_command",
        "description": "在 Ubuntu 虚拟机上执行 shell 命令，返回 stdout/stderr 和退出码。适合跑实验、安装软件、查看文件；要启动图形程序请用 open_terminal。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "command": {"type": "string", "description": "要执行的 shell 命令"},
                "timeout": {"type": "integer", "description": "超时秒数，默认 60"},
                "cwd": {"type": "string", "description": "工作目录，可选"},
            },
            "required": ["command"],
        },
        "handler": t_execute_command,
    },
    {
        "name": "open_terminal",
        "description": "在虚拟机的图形桌面上打开一个新的终端窗口（可带一条要执行的命令），常用于演示命令行操作后截图。",
        "inputSchema": {
            "type": "object",
            "properties": {"command": {"type": "string", "description": "终端打开后要执行的命令，可选"}},
        },
        "handler": t_open_terminal,
    },
    {
        "name": "take_screenshot",
        "description": (
            "截取虚拟机画面并存成图片文件（同时返回图片本身）。"
            "传 window 可只截某个窗口（自动前置、含标题栏，画面干净适合放进报告）；"
            "传 region 可截指定矩形；都不传则截全屏。"
        ),
        "inputSchema": {
            "type": "object",
            "properties": {
                "name": {"type": "string", "description": "文件名（不含扩展名），如 exp1-browser；留空用时间戳"},
                "window": {
                    "type": "string",
                    "description": "窗口标题或其中一段，如 Firefox、Terminal、gedit。只截这个窗口",
                },
                "region": {"type": "string", "description": "可选，只截一块区域，格式 x,y,w,h"},
                "max_width": {"type": "integer", "description": "缩放后最大宽度，0 用配置默认值"},
                "inline": {"type": "boolean", "description": "是否内联返回图片，默认 true；只要路径时传 false"},
            },
        },
        "handler": t_take_screenshot,
    },
    {
        "name": "list_screenshots",
        "description": "列出本地已保存的所有截图（文件名、大小、时间），方便在报告里挑选引用。",
        "inputSchema": {"type": "object", "properties": {}},
        "handler": t_list_screenshots,
    },
    {
        "name": "click",
        "description": "在虚拟机桌面的指定像素坐标点击鼠标。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "x": {"type": "integer"},
                "y": {"type": "integer"},
                "button": {"type": "string", "enum": ["left", "middle", "right"], "default": "left"},
                "double": {"type": "boolean", "description": "是否双击"},
            },
            "required": ["x", "y"],
        },
        "handler": t_click,
    },
    {
        "name": "move_mouse",
        "description": "把鼠标移动到指定像素坐标。",
        "inputSchema": {
            "type": "object",
            "properties": {"x": {"type": "integer"}, "y": {"type": "integer"}},
            "required": ["x", "y"],
        },
        "handler": t_move_mouse,
    },
    {
        "name": "drag_mouse",
        "description": "按住鼠标从 (x1,y1) 拖到 (x2,y2)，用于拖动窗口、选中文本。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "x1": {"type": "integer"}, "y1": {"type": "integer"},
                "x2": {"type": "integer"}, "y2": {"type": "integer"},
                "button": {"type": "string", "enum": ["left", "middle", "right"], "default": "left"},
            },
            "required": ["x1", "y1", "x2", "y2"],
        },
        "handler": t_drag_mouse,
    },
    {
        "name": "type_text",
        "description": "向当前活动窗口输入文本（模拟键盘打字），支持中文。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "text": {"type": "string"},
                "delay_ms": {"type": "integer", "description": "每个字符间隔毫秒，默认 12"},
            },
            "required": ["text"],
        },
        "handler": t_type_text,
    },
    {
        "name": "press_key",
        "description": "按下一个键，如 enter、esc、tab、up、down、backspace、F5。",
        "inputSchema": {
            "type": "object",
            "properties": {"key": {"type": "string"}, "repeat": {"type": "integer", "default": 1}},
            "required": ["key"],
        },
        "handler": t_press_key,
    },
    {
        "name": "press_hotkey",
        "description": "按组合键，如 ctrl+shift+c、alt+tab、ctrl+l（浏览器地址栏）、super（活动概览）。",
        "inputSchema": {
            "type": "object",
            "properties": {"keys": {"type": "string", "description": "用 + 连接的组合键"}},
            "required": ["keys"],
        },
        "handler": t_press_hotkey,
    },
    {
        "name": "scroll",
        "description": "滚动鼠标滚轮。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "direction": {"type": "string", "enum": ["up", "down"], "default": "down"},
                "amount": {"type": "integer", "default": 3},
            },
        },
        "handler": t_scroll,
    },
    {
        "name": "get_active_window_title",
        "description": "获取当前活动窗口标题，确认焦点是否在浏览器/终端上。",
        "inputSchema": {"type": "object", "properties": {}},
        "handler": t_get_active_window_title,
    },
    {
        "name": "list_windows",
        "description": "列出虚拟机上所有可见窗口的 ID、标题和所属程序。",
        "inputSchema": {"type": "object", "properties": {}},
        "handler": t_list_windows,
    },
    {
        "name": "focus_window",
        "description": "按标题（支持部分匹配）激活/前置一个窗口，如 firefox、Terminal。",
        "inputSchema": {
            "type": "object",
            "properties": {"title": {"type": "string"}},
            "required": ["title"],
        },
        "handler": t_focus_window,
    },
    {
        "name": "get_screen_info",
        "description": "获取虚拟机屏幕分辨率与显示服务器类型（X11/Wayland），用于计算点击坐标。",
        "inputSchema": {"type": "object", "properties": {}},
        "handler": t_get_screen_info,
    },
    {
        "name": "upload_file",
        "description": "把本机文件上传到虚拟机（实验脚本、数据等）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "local_path": {"type": "string"},
                "remote_path": {"type": "string", "description": "远端路径，以 / 结尾表示目录"},
            },
            "required": ["local_path", "remote_path"],
        },
        "handler": t_upload_file,
    },
    {
        "name": "download_file",
        "description": "从虚拟机下载文件到本机（实验结果、生成的图片等）。",
        "inputSchema": {
            "type": "object",
            "properties": {
                "remote_path": {"type": "string"},
                "local_path": {"type": "string", "description": "留空存到本机下载目录"},
            },
            "required": ["remote_path"],
        },
        "handler": t_download_file,
    },
]

HANDLERS = {
    t["name"]: t["handler"] for t in TOOLS
}


# --------------------------------------------------------------------------- #
# MCP 协议（stdio，按行分隔 JSON-RPC）
# --------------------------------------------------------------------------- #
def handle_request(msg: dict[str, Any]) -> dict[str, Any] | None:
    method = msg.get("method")
    msg_id = msg.get("id")
    params = msg.get("params") or {}
    is_notification = "id" not in msg

    def ok(result: Any) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": msg_id, "result": result}

    def err(code: int, message: str) -> dict[str, Any]:
        return {"jsonrpc": "2.0", "id": msg_id, "error": {"code": code, "message": message}}

    if method == "initialize":
        requested = params.get("protocolVersion")
        version = requested if requested in SUPPORTED_PROTOCOLS else SUPPORTED_PROTOCOLS[0]
        return ok(
            {
                "protocolVersion": version,
                "capabilities": {"tools": {"listChanged": False}},
                "serverInfo": {"name": SERVER_NAME, "version": SERVER_VERSION},
                "instructions": (
                    ("直接控制这台 Ubuntu 虚拟机（本机模式）。" if LOCAL
                     else "通过 SSH 控制 Ubuntu 虚拟机（远程模式）。")
                    + "开始操作前先调用 vm_status 确认环境；"
                    "截图用 take_screenshot，可用 window 参数只截某个窗口，"
                    "图片会存成本地文件并返回路径。"
                ),
            }
        )

    if is_notification:
        return None

    if method == "ping":
        return ok({})

    if method == "tools/list":
        return ok(
            {
                "tools": [
                    {"name": t["name"], "description": t["description"], "inputSchema": t["inputSchema"]}
                    for t in TOOLS
                ]
            }
        )

    if method == "tools/call":
        name = params.get("name")
        arguments = params.get("arguments") or {}
        handler = HANDLERS.get(name)
        if handler is None:
            return err(-32602, f"未知工具：{name}")
        try:
            result = handler(arguments)
            result.setdefault("isError", False)
            return ok(result)
        except SSHError as exc:
            return ok({"content": [_text("❌ " + str(exc))], "isError": True})
        except Exception as exc:
            log(f"工具 {name} 异常：{exc!r}")
            return ok({"content": [_text(f"❌ {name} 执行失败：{exc}")], "isError": True})

    if method == "resources/list":
        return ok({"resources": []})
    if method == "prompts/list":
        return ok({"prompts": []})
    if method in ("logging/setLevel", "notifications/cancelled", "notifications/initialized"):
        return None

    return err(-32601, f"不支持的方法：{method}")


def write_message(obj: dict[str, Any]) -> None:
    sys.stdout.buffer.write(json.dumps(obj, ensure_ascii=False).encode("utf-8") + b"\n")
    sys.stdout.buffer.flush()


def main() -> None:
    if LOCAL:
        where = f"本机 {socket.gethostname()}（local）"
    elif str(CFG.get("host") or "").strip():
        where = f"{_target()}:{CFG['port']}（ssh）"
    else:
        where = "未配置主机（ssh）"
    log(f"启动，模式 {MODE}，目标 {where}，截图目录 {SHOT_DIR}")
    stdin = sys.stdin.buffer
    while True:
        line = stdin.readline()
        if not line:
            break
        line = line.strip()
        if not line:
            continue
        try:
            msg = json.loads(line.decode("utf-8", "replace"))
        except json.JSONDecodeError as exc:
            log(f"收到非法 JSON：{exc}")
            continue
        try:
            if isinstance(msg, list):
                for m in msg:
                    resp = handle_request(m)
                    if resp:
                        write_message(resp)
            else:
                resp = handle_request(msg)
                if resp:
                    write_message(resp)
        except Exception as exc:
            log(f"处理消息异常：{exc!r}")
            if isinstance(msg, dict) and "id" in msg:
                write_message(
                    {"jsonrpc": "2.0", "id": msg.get("id"), "error": {"code": -32603, "message": str(exc)}}
                )


if __name__ == "__main__":
    try:
        main()
    except KeyboardInterrupt:
        pass
