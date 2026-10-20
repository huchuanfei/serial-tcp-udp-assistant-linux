# 网络 / 串口调试工具集

一组基于 Python 3 + tkinter 的图形化调试工具，用于 **TCP / UDP / 串口** 联调。纯 Python 标准库实现（串口助手额外依赖 `pyserial`），无编译依赖、无框架、单文件即可运行。

配套提供 PyInstaller 打包脚本，可生成不依赖 Python 环境的独立可执行文件（x86_64 本机版、aarch64 / Orin NX 版均已提供）。

---
<img width="2160" height="1440" alt="8a54cd5c2212946d2b109bf343a4bdc4" src="https://github.com/user-attachments/assets/a540df77-8ccb-4507-b04a-dac105de05be" />

## 工具一览

| 工具 | 源文件 | 预编译产物 | 用途与主要能力 |
|---|---|---|---|
| TCP 客户端 | [tcp_client.py](tcp_client.py) | `dist/tcp_client_debug` | 连接 TCP 服务器；可指定**本地绑定地址（网卡 IP:端口，端口 0 随机）**；发送尾部可追加 `\r`(0x0D) / `\n`(0x0A)；收到数据自动回复相同内容 |
| TCP 服务端 | [tcp_server.py](tcp_server.py) | `dist/tcp_server_debug` | 监听端口，**支持多客户端**；状态栏实时显示在线客户端数；指定目标客户端（可一键填入“最近客户端”）发送 |
| UDP 客户端 | [udp_client.py](udp_client.py) | `dist/udp_client_debug` | 面向指定服务器地址收发；可指定本地端口（0 随机）；收到数据自动回复相同内容 |
| UDP 服务端 | [udp_server.py](udp_server.py) | `dist/udp_server_debug` | 绑定地址/端口监听；向指定目标地址发送（可一键填入“最近客户端”） |
| 串口调试助手 | [serial_assistant.py](serial_assistant.py) | `dist/serial_assistant` | 串口收发，可选**端口/波特率/数据位/停止位/校验位**；按“帧超时(ms)”自动分帧；自动回复；**定时发送**（间隔 ≥ 10 ms） |

### 通用能力

- **显示模式**：接收区支持 ASCII / HEX 勾选切换（可同时勾选）。
- **发送模式**：发送区支持 ASCII / HEX；HEX 输入兼容 `01 A2 3F`、`0x01,0xA2` 等多种写法，非法输入会弹框提示。
- **日志**：清空接收区、保存接收日志到文件（可选 `.log` / `.txt`）。
- **状态栏**：显示连接/监听状态、对端地址与实际本地绑定地址。
- **界面**：接收区自动扩展、发送区固定高度，均带横竖滚动条；长内容不抖动。

---

## 目录结构

```
.
├── tcp_client.py            # TCP 客户端
├── tcp_server.py            # TCP 服务端
├── udp_client.py            # UDP 客户端
├── udp_server.py            # UDP 服务端
├── serial_assistant.py      # 串口调试助手
├── build_local.sh           # 本机 x86_64 一键打包（输出到 dist/）
├── build_orin.sh            # 在 Orin NX (aarch64) 设备上一键打包（输出到 dist_orin/）
├── docker-build-arm64.sh    # 在 x86 主机用 Docker 交叉编译 arm64（输出到 dist_orin/）
├── Dockerfile               # 单个工具的 arm64 镜像定义
├── Dockerfile.export        # 一次导出 5 个 arm64 二进制的定义
├── dist/                    # Linux x86_64 预编译二进制
└── dist_orin/               # Linux aarch64 (Jetson Orin NX) 预编译二进制
```

---

## 快速开始

### 方式一：直接用预编译二进制（推荐给最终用户）

| 目录 | 适用平台 | 说明 |
|---|---|---|
| `dist/` | Linux **x86_64** | 在普通 x86 PC / 服务器上运行 |
| `dist_orin/` | Linux **aarch64** | 面向 Jetson Orin NX（Ubuntu 20.04 / glibc 2.31 构建，兼容性更广） |

```bash
chmod +x dist/tcp_client_debug     # 从上位机拷贝后需补执行权限
./dist/tcp_client_debug
```

二进制是 PyInstaller 单文件打包，**目标机不需要安装 Python**；但它是 GUI 程序，需要图形环境（X11 相关系统库，如 `libx11-6`、`libxcb1`）以及可用的 `DISPLAY`。

### 方式二：从源码运行

```bash
# Linux 需要系统自带 tkinter
sudo apt install -y python3-tk

# 串口助手额外需要 pyserial（其余 4 个工具零第三方依赖）
pip install pyserial

python3 tcp_client.py
```

### Windows 上运行

本项目暂无 Windows 预编译产物，可直接拷源码运行：在 Windows 上安装官方 Python 3（python.org 安装包或 Microsoft Store 版，**默认自带 tkinter**），然后：

```
python tcp_client.py        # 会带一个控制台窗口
pythonw tcp_client.py       # 无控制台窗口（推荐给 GUI 程序）
```

除串口助手需 `pip install pyserial` 外，无需安装任何第三方库。

---

## 使用要点：TCP 客户端的“本地地址”

`tcp_client.py` 的「本地地址(0随机)」用于**绑定客户端本机网卡**，支持三种写法：

| 输入 | 含义 |
|---|---|
| `192.168.1.16:9000` | 绑定指定网卡 IP + 固定端口（多网卡环境下固定走哪张网卡） |
| `0.0.0.0:0`（默认值） | 不限制网卡、端口由系统随机分配 |
| `9000` 或 `0` | 只写端口，网卡 IP 取 `0.0.0.0` |

连接成功后状态栏会显示实际的本地绑定地址，例如 `状态: 已连接 192.168.1.100:5000 (本地:192.168.1.16:9000)`。

> 发送区勾选 `\r(0x0D)` / `\n(0x0A)` 后，**每次手动发送**都会在数据尾部追加对应字节：只勾 `\r` 追加 `0x0D`，只勾 `\n` 追加 `0x0A`，两者都勾则依次追加 `0x0D`、`0x0A`（等价 CRLF）。ASCII 与 HEX 发送模式都生效；自动回复不受此设置影响。

---

## 构建

### 1. 本机 x86_64 打包

```bash
chmod +x build_local.sh
./build_local.sh              # 打包全部 5 个工具
./build_local.sh tcp_client   # 只打包指定工具（不带 .py）
```

脚本会自检并自动补齐构建环境：Python3 → pip（缺失时用 get-pip.py 装到用户目录）→ tkinter（优先 `apt install python3-tk`，无免密 sudo 时退化为下载 deb 解包到临时目录）→ PyInstaller + pyserial。产物输出到 `dist/`，命名规则为 `xxx.py → xxx_debug`（`serial_assistant` 保持原名）。

### 2. 在 Orin NX 设备上打包（aarch64）

把 `build_orin.sh` 与 5 个 `.py` 拷到设备同一目录后执行：

```bash
chmod +x build_orin.sh && ./build_orin.sh
```

产物输出到 `dist_orin/`（需 sudo 安装 `python3-tk` 等系统依赖）。

### 3. 在 x86 主机上交叉编译 aarch64 版（需要 Docker）

```bash
./docker-build-arm64.sh export        # 导出 5 个 arm64 二进制到 dist_orin/
./docker-build-arm64.sh tcp_server    # 只构建 tcp_server_debug:arm64 镜像
./docker-build-arm64.sh all-images    # 构建 5 个镜像
```

基于 `ubuntu:20.04` 构建，产物 glibc 依赖 ≤ 2.31，能直接在 Orin NX 的 Ubuntu 20.04 上运行。

> PyInstaller **不支持交叉编译**：x86_64 版必须在 x86_64 机器上构建，arm64 版要么在 arm64 设备上构建，要么用 Docker + buildx（本仓库采用后者）。

---

## 依赖与平台支持

| 项目 | 说明 |
|---|---|
| Python | 3.8+（源码运行） |
| GUI | tkinter（Linux 由 `python3-tk` 提供；Windows 官方 Python 自带） |
| 第三方库 | 仅 `serial_assistant.py` 需要 `pyserial` |
| 运行预编译二进制 | 无需 Python；需要 X11 图形环境 |
| 已验证平台 | Linux x86_64、Linux aarch64（Jetson Orin NX，Ubuntu 20.04） |

---

## 常见问题

**运行二进制报缺少 `_tkinter` / tkinter**
用源码方式运行时报错，说明系统 Python 没装 tkinter，执行 `sudo apt install python3-tk`；使用 `dist/` 下的预编译二进制则不会遇到此问题（Tcl/Tk 已打包进去）。

**拷贝二进制后提示 Permission denied**
`chmod +x dist/*`。

**Windows 下载的 exe / py 被杀软拦截**
PyInstaller 单文件产物常被误报，可改用 `--onedir` 方式打包或加白名单；直接运行 `.py` 源码则无此问题。

**为什么 `dist/` 和 `dist_orin/` 里的文件名带 `_debug`**
与两个构建脚本的产物命名保持一致（`tcp_client.py → tcp_client_debug`），便于和源文件区分；`serial_assistant` 未加后缀。

**GitHub 上如何分发这些二进制**
`dist/` + `dist_orin/` 共约 90 MB，直接长期放在 Git 仓库里会让每次 clone 都变大。建议把源码作为仓库主体，把二进制放到 **Release 附件**中按版本发布。

---

## 许可证

本项目以 [MIT License](LICENSE) 开源，可自由使用、修改与再分发（需保留版权声明）。

第三方组件：`serial_assistant.py` 使用 [pyserial](https://github.com/pyserial/pyserial)（BSD-3-Clause）；打包工具 [PyInstaller](https://pyinstaller.org/)（GPL-2.0 及例外条款）仅参与构建，不影响本项目的授权。

## 免责声明

本项目仅用于自有设备的网络/串口协议调试与教学用途，请勿用于任何未授权的网络探测或攻击行为。
