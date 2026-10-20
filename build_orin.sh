#!/usr/bin/env bash
# =============================================================
# Orin NX (aarch64 Ubuntu) 一键构建脚本
# 在 Orin NX 设备上运行，把 5 个调试工具打包成独立可执行文件:
#   udp_server / udp_client / tcp_server / tcp_client / serial_assistant
#
# 用法:
#   1. 把本脚本和 5 个 .py 源文件拷到 Orin NX 同一目录
#   2. chmod +x build_orin.sh && ./build_orin.sh
# 产物输出到 dist_orin/ 目录
# =============================================================
set -e

# ---- 环境检查 ----
ARCH=$(uname -m)
if [ "$ARCH" != "aarch64" ]; then
    echo "[错误] 本脚本必须在 aarch64 (Orin NX) 上运行，当前架构: $ARCH"
    echo "       PyInstaller 不支持交叉编译，x86 版请用本机打包。"
    exit 1
fi

for f in udp_server.py udp_client.py tcp_server.py tcp_client.py serial_assistant.py; do
    if [ ! -f "$f" ]; then
        echo "[错误] 缺少源文件: $f"
        exit 1
    fi
done

echo "[1/4] 安装系统依赖 (需要 sudo)..."
sudo apt-get update -qq
sudo apt-get install -y -qq python3 python3-venv python3-tk python3-dev >/dev/null

echo "[2/4] 创建独立构建环境 venv..."
rm -rf .build_venv
python3 -m venv .build_venv
. .build_venv/bin/activate
pip install --upgrade pip -q
pip install 'pyinstaller==5.13.2' 'pyinstaller-hooks-contrib==2023.10' pyserial -q

echo "[3/4] 开始打包 (5 个工具)..."
rm -rf build dist_orin
mkdir -p dist_orin

for name in udp_server udp_client tcp_server tcp_client serial_assistant; do
    echo "  -> 打包 ${name}..."
    pyinstaller --onefile --windowed --clean --noconfirm \
                --distpath dist_orin --name "${name}" "${name}.py" >/dev/null
done

deactivate
rm -rf .build_venv build *.spec

echo "[4/4] 验证产物..."
ls -lh dist_orin/
echo ""
echo "全部完成！可执行文件在 dist_orin/ 目录。"
echo "首次拷贝到其他位置后记得加执行权限:  chmod +x dist_orin/*"
