#!/usr/bin/env bash
# 在 x86 主机上用 Docker 交叉编译 arm64 版调试工具
# 必须在含 .py 源文件和 Dockerfile 的目录运行（不是 dist/）
set -e

cd "$(dirname "$0")"

MODE="${1:-export}"   # export | tcp_server | all-images

case "$MODE" in
  export)
    echo ">>> 编译 5 个 arm64 二进制到 dist_orin/ (Ubuntu 20.04 / glibc 2.31，兼容 Orin) ..."
    docker buildx build --platform linux/arm64 \
      -f Dockerfile.export \
      -o type=local,dest=./dist_orin \
      .
    echo ""
    echo ">>> 产物:"
    ls -lh dist_orin/
    echo ""
    echo ">>> 架构验证:"
    file dist_orin/*
    echo ""
    echo ">>> glibc 依赖（应 <= GLIBC_2.31）:"
    if command -v aarch64-linux-gnu-objdump >/dev/null 2>&1; then
      for f in dist_orin/*; do
        echo "  $f:"
        aarch64-linux-gnu-objdump -T "$f" 2>/dev/null | grep GLIBC | sed 's/.*\t//' | sort -Vu | tail -3 | sed 's/^/    /'
      done
    else
      echo "  (可选) sudo apt install binutils-aarch64-linux-gnu 后查看 GLIBC 版本"
    fi
    ;;

  tcp_server)
    echo ">>> 构建镜像 tcp_server_debug:arm64 ..."
    docker buildx build --platform linux/arm64 \
      --build-arg APP=tcp_server \
      --build-arg OUT_NAME=tcp_server_debug \
      -t tcp_server_debug:arm64 --load \
      .
    ;;

  all-images)
    for spec in \
      "tcp_server:tcp_server_debug" \
      "tcp_client:tcp_client_debug" \
      "udp_server:udp_server_debug" \
      "udp_client:udp_client_debug" \
      "serial_assistant:serial_assistant"; do
      app="${spec%%:*}"; tag="${spec##*:}"
      echo ">>> 构建 ${tag}:arm64 ..."
      docker buildx build --platform linux/arm64 \
        --build-arg APP="$app" --build-arg OUT_NAME="$tag" \
        -t "${tag}:arm64" --load .
    done
    ;;

  *)
    echo "用法: $0 [export|tcp_server|all-images]"
    echo "  export      导出 5 个 arm64 二进制到 dist_orin/（推荐，给 Orin 直接用）"
    echo "  tcp_server  只打 tcp_server_debug:arm64 镜像"
    echo "  all-images  打 5 个镜像"
    exit 1
    ;;
esac
