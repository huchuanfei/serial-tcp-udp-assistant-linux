#!/usr/bin/env bash
# =============================================================
# 本机 x86_64 (Ubuntu) 一键构建脚本
# 在本机打包 5 个调试工具，产物是当前机器可直接运行的独立二进制:
#   udp_server_debug / udp_client_debug / tcp_server_debug /
#   tcp_client_debug / serial_assistant
#
# 用法（在本目录下执行）:
#   chmod +x build_local.sh
#   ./build_local.sh              # 打包全部 5 个工具
#   ./build_local.sh tcp_client   # 只打包指定的 1 个（源文件名，不带 .py）
# 产物输出到 dist/
#
# 说明: PyInstaller 不支持交叉编译，本脚本只能构建 x86_64 本机版本;
#       给 Orin NX (aarch64) 打包请用 build_orin.sh 或 docker-build-arm64.sh。
# =============================================================
set -e
cd "$(dirname "$0")"

TOOLS="udp_server udp_client tcp_server tcp_client serial_assistant"

# 源文件 -> 产物名（与 dist/ 现有命名保持一致）
out_name() {
    case "$1" in
        serial_assistant) echo "serial_assistant" ;;
        *)                echo "$1_debug" ;;
    esac
}

# ---- 0. 架构检查 ----
ARCH=$(uname -m)
if [ "$ARCH" != "x86_64" ]; then
    echo "[错误] 本脚本用于 x86_64 本机构建，当前架构: $ARCH"
    echo "       aarch64 (Orin NX) 请用 build_orin.sh"
    exit 1
fi

# ---- 1. 参数与源文件检查 ----
if [ -n "$1" ]; then
    case " $TOOLS " in
        *" $1 "*) TARGETS="$1" ;;
        *) echo "[错误] 未知工具: $1（可选: $TOOLS）"; exit 1 ;;
    esac
else
    TARGETS="$TOOLS"
fi

for f in $TARGETS; do
    if [ ! -f "$f.py" ]; then
        echo "[错误] 缺少源文件: $f.py"
        exit 1
    fi
done

WORK=$(mktemp -d /tmp/pyi_local.XXXXXX)
TMPSTACK=""
cleanup() { rm -rf "$WORK" ${TMPSTACK:+"$TMPSTACK"}; }
trap cleanup EXIT

# ---- 2. 准备 pip（系统若没有，用 get-pip 装到用户目录 ~/.local）----
PIP_FLAGS="--user"
python3 -m pip install --help 2>/dev/null | grep -q -- "--break-system-packages" \
    && PIP_FLAGS="$PIP_FLAGS --break-system-packages"

if ! python3 -m pip --version >/dev/null 2>&1; then
    echo "[1/4] 未检测到 pip，正在为用户安装 (get-pip.py)..."
    curl -fsSL -o "$WORK/get-pip.py" https://bootstrap.pypa.io/get-pip.py
    python3 "$WORK/get-pip.py" $PIP_FLAGS
else
    echo "[1/4] pip 已就绪: $(python3 -m pip --version)"
fi

# ---- 3. 准备 tkinter（5 个工具都依赖）----
# 优先用 apt 安装 python3-tk；若 sudo 需要密码，则退化为免 root 方式:
# 把对应的 deb 包下载后解包到临时目录，仅作为本次构建的 tkinter 来源。
TK_ENV_READY=0
if python3 -c "import tkinter" >/dev/null 2>&1; then
    echo "[2/4] tkinter 已就绪（系统自带）"
    TK_ENV_READY=1
else
    echo "[2/4] 未检测到 tkinter，尝试安装..."
    if sudo -n true 2>/dev/null; then
        sudo apt-get update -qq
        sudo apt-get install -y -qq python3-tk
        TK_ENV_READY=1
    else
        echo "      sudo 需要密码，改用免 root 方式（下载 deb 并解包到临时目录）..."
        PYVER=$(python3 -c 'import sys; print("%d.%d" % sys.version_info[:2])')
        TMPSTACK="$WORK/tkstack"
        mkdir -p "$TMPSTACK"
        (
            cd "$TMPSTACK"
            apt-get download "python${PYVER}-tk" libtk8.6 libtcl8.6
        )
        for deb in "$TMPSTACK"/*.deb; do
            dpkg-deb -x "$deb" "$TMPSTACK"
        done

        PYLIB_DIR="$TMPSTACK/usr/lib/python$PYVER"
        TCLTK_LIB_DIR=$(find "$TMPSTACK/usr/lib" -mindepth 1 -maxdepth 1 -type d ! -name python\* | head -n1)
        TK_DATA_DIR=$(find "$TMPSTACK" -maxdepth 5 -type d -name "tk[0-9]*" -path "*tcltk*" | head -n1)

        export PYTHONPATH="$PYLIB_DIR:$PYLIB_DIR/lib-dynload"
        export LD_LIBRARY_PATH="$TCLTK_LIB_DIR"
        export TK_LIBRARY="$TK_DATA_DIR"

        if python3 -c "import tkinter, _tkinter" >/dev/null 2>&1; then
            TK_ENV_READY=1
        fi
    fi
fi

if [ "$TK_ENV_READY" != "1" ]; then
    echo "[错误] 无法准备 tkinter，5 个工具都是 GUI 程序，缺少它无法打包。"
    echo "       请先手动执行:  sudo apt install -y python3-tk python3-pip"
    exit 1
fi

# ---- 4. 准备 PyInstaller / pyserial ----
if ! python3 -m PyInstaller --version >/dev/null 2>&1 || ! python3 -c "import serial" >/dev/null 2>&1; then
    echo "[3/4] 安装构建依赖 PyInstaller + pyserial ..."
    # 注意: 这里不锁定 pyinstaller==5.13.2（build_orin.sh 用的版本），
    #      老版本不支持新版 Python（如 3.12+），本机需用较新的 PyInstaller。
    python3 -m pip install -q $PIP_FLAGS pyinstaller pyserial
else
    echo "[3/4] PyInstaller 已就绪: $(python3 -m PyInstaller --version)"
fi

# ---- 5. 打包 ----
echo "[4/4] 开始打包 ($TARGETS)..."
mkdir -p dist
for src in $TARGETS; do
    out=$(out_name "$src")
    echo "  -> 打包 $src.py  ==>  dist/$out"
    rm -f "dist/$out"
    python3 -m PyInstaller --onefile --clean --noconfirm \
        --distpath dist --workpath "$WORK" --specpath "$WORK" \
        --name "$out" "$src.py" >/dev/null
done

echo ""
echo "构建完成，产物在 dist/ 目录:"
ls -lh dist/
echo ""
echo "提示: 拷贝到其他机器使用后，如遇权限问题可执行  chmod +x dist/*"
