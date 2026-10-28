# dsh-ubuntu-vm-control

![version](https://img.shields.io/badge/version-0.1.2-blue)

**Ubuntu 虚拟机控制 MCP server**——让任意支持 MCP 的客户端（dsh / Claude Desktop / Cursor / Trae…）直接操作一台 Ubuntu 桌面虚拟机：跑命令、开终端、点鼠标、敲键盘，**截图存成真实图片文件**。同时提供 DeepSeek Harness 的一键集成。

适合需要图形界面的场景：做安全实验、复现漏洞、点网页表单、给报告配图。纯 SSH 只能拿到文字输出，这个能拿到**屏幕**。

```
MCP 客户端 ──stdio──> server.py (Python) ──SSH──> Ubuntu VM
                                                  ├── xdotool  （键鼠）
                                                  ├── scrot    （截图）
                                                  └── wmctrl   （窗口）
```

## 前置条件

| 项 | 要求 |
| --- | --- |
| **Python** | **3.8 或更高**，在 `PATH` 里 |
| MCP 客户端 | dsh、Claude Desktop、Cursor、Trae 等任意支持 stdio MCP 的客户端 |
| 被控机 | Ubuntu，**X11 会话** |
| 被控机软件 | `sudo apt install -y xdotool scrot wmctrl openssh-server` |
| SSH | **密钥免密登录**（插件用 `BatchMode`，不会弹密码框） |

> 没有 Xorg 会话？Ubuntu 25.10 起 GNOME 移除了 Xorg。装个 Xfce 即可：`sudo apt install -y xfce4`，登录时选 **Xfce Session**。

## 安装

两种方式，按你用的客户端选。

### 方式一：作为 MCP server 接入任意客户端（推荐）

不用克隆仓库，也不依赖 dsh。在客户端配置里加一条 stdio server：

```json
{
  "mcpServers": {
    "ubuntu-vm-control": {
      "command": "npx",
      "args": ["-y", "dsh-ubuntu-vm-control", "serve"],
      "env": { "VM_HOST": "192.168.1.10", "VM_USER": "ubuntu" }
    }
  }
}
```

也可以先 `npm i -g dsh-ubuntu-vm-control`，再把 `command` 换成 `dsh-ubuntu-vm-control`、`args` 换成 `["serve"]`。

### 方式二：装成 dsh 插件

```bash
git clone https://github.com/j04783993-prog/dsh-ubuntu-vm-control.git
cd dsh-ubuntu-vm-control
```

**Windows**：双击 `install.cmd`

**Linux / macOS**：

```bash
python3 install.py
```

安装脚本会做三件事：

1. 把 `server.py` 复制到 `~/.dsh-ubuntu-vm-control/`（稳定位置，之后删掉仓库也不影响）
2. 在 `~/.dsh/profiles/desktop/cordis.patch.yml` 里写入一条 MCP 配置，**路径是安装时算出来的绝对路径**
3. 改文件前自动备份

常用参数：

```bash
python install.py --profile web              # 装到别的 profile
python install.py --python /usr/bin/python3  # 指定解释器
python install.py --dry-run                  # 只看结果，不写文件
python install.py --uninstall                # 移除配置
```

装完**完全退出 dsh 再重新打开**（不是刷新窗口）。

> **注意**：`npm i dsh-ubuntu-vm-control` 只是把包装下来，**不会自动注册**成 dsh 插件。
> 想用 dsh 集成，装完还要执行一次 `npx dsh-ubuntu-vm-control install`（等价于 `python install.py`）。

npm 包的其它子命令：

```bash
npx dsh-ubuntu-vm-control install --profile web   # 装到别的 profile
npx dsh-ubuntu-vm-control install --dry-run       # 只看结果，不写文件
npx dsh-ubuntu-vm-control serve                   # 以 MCP stdio 方式启动服务
```

## 配置被控机

**方式一：配置文件** `~/.dsh-ubuntu-vm-control/config.json`

```json
{
  "host": "192.168.1.10",
  "user": "ubuntu",
  "key_path": "~/.ssh/id_ed25519",
  "display": ":0",
  "screenshot_dir": "~/vm-shots",
  "max_width": 1600
}
```

**方式二：环境变量**

```bash
VM_HOST=192.168.1.10
VM_USER=ubuntu
VM_SSH_KEY=~/.ssh/id_ed25519
```

优先级：环境变量 > 用户配置文件 > 默认值。

`mode` 一般不用填：**配了 `host` 就自动走 `ssh`，没配 `host` 则认为是跑在被控机本身上（`local`）**。
所以在 macOS / Linux 上控制远端虚拟机时，务必确认 `host` 已配置，否则会被当成操作本机。

配好之后让 agent 调一次 `vm_status`，它会报告连接状态、会话类型（X11/Wayland）、缺失的工具和分辨率。

## 提供的工具（18 个）

| 工具 | 说明 |
| --- | --- |
| `vm_status` | 连接与环境自检（**先调这个**） |
| `execute_command` | 在虚拟机上执行 shell 命令 |
| `open_terminal` | 在桌面上打开一个真实终端窗口 |
| `take_screenshot` | 截图并保存为图片文件；`window` 只截某个窗口，`region` 截矩形 |
| `list_screenshots` | 列出已保存的截图 |
| `click` / `move_mouse` / `drag_mouse` | 鼠标操作 |
| `type_text` / `press_key` / `press_hotkey` / `scroll` | 键盘操作 |
| `get_active_window_title` / `list_windows` / `focus_window` | 窗口管理 |
| `get_screen_info` | 分辨率与显示协议 |
| `upload_file` / `download_file` | 与虚拟机互传文件 |

### 截图为什么好用

`take_screenshot(window="Firefox")` 会先激活那个窗口，再沿窗口管理器的外框取几何（**含标题栏**），
只截这一块——画面干净，直接能放进报告。

```text
截图已保存（103 KB，方式：窗口「Firefox」/ scrot）
  C:\Users\you\vm-shots\exp1-browser.jpg
```

## 免密 SSH

```bash
# Linux / macOS
ssh-copy-id -i ~/.ssh/id_ed25519.pub ubuntu@192.168.1.10

# Windows (CMD)
type %USERPROFILE%\.ssh\id_ed25519.pub | ssh ubuntu@192.168.1.10 "mkdir -p ~/.ssh && chmod 700 ~/.ssh && cat >> ~/.ssh/authorized_keys && chmod 600 ~/.ssh/authorized_keys"
```

验证：`ssh -o BatchMode=yes ubuntu@192.168.1.10 echo ok` 直接返回 `ok`。

## 排错

| 现象 | 原因 |
| --- | --- |
| MCP 面板里没有 `ubuntu`，0 个工具 | dsh 没重启；或 `cordis.patch.yml` 里没有本插件的条目（重跑 `install.py`） |
| `SSH 认证失败` | 没配免密，或 `key_path` 指错了 |
| `连不上 ... Connection refused` | IP 变了 / VM 没开机 / 22 端口被拦 |
| 截图全黑或报错 | Wayland 会话（切 Xorg 或装 Xfce）；或桌面锁屏 |
| `xdotool` 无反应 | 同上，Wayland 下不可用 |
| `找不到 Python 3` | 装 Python 3.8+，或 `install.py --python <绝对路径>` 重装一次 |

`vm_status` 的返回会直接告诉你缺什么，先调它。

## 卸载

```bash
python install.py --uninstall
rm -rf ~/.dsh-ubuntu-vm-control
```

## 原理

- MCP 服务是一个**零依赖的 Python 脚本**（只用标准库），SSH 走系统自带的 OpenSSH 客户端
- 安装脚本往 dsh 的 loader 配置里插一条 `@deepseek-ai/dsh-mcp-client` 条目，
  指向 `~/.dsh-ubuntu-vm-control/server.py`，由 dsh 自己负责拉起和注册工具
- 因此升级 dsh 不会影响它，也不需要编译，更没有 npm 依赖

## License

MIT
