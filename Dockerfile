# 目标: Orin NX Ubuntu 20.04 (glibc 2.31) — 必须用 ubuntu:20.04 构建，不可用 22.04
# 用法见 docker-build-arm64.sh 或下方命令
#
# 构建单个工具镜像（含二进制）:
#   docker buildx build --platform linux/arm64 \
#     --build-arg APP=tcp_server --build-arg OUT_NAME=tcp_server_debug \
#     -t tcp_server_debug:arm64 --load .

# syntax=docker/dockerfile:1
FROM --platform=linux/arm64 ubuntu:20.04 AS builder
ARG APP=tcp_server
ARG OUT_NAME=

ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3 python3-pip python3-venv python3-tk python3-dev binutils \
    && rm -rf /var/lib/apt/lists/*

WORKDIR /src
COPY udp_server.py udp_client.py tcp_server.py tcp_client.py serial_assistant.py ./

RUN OUT="${OUT_NAME:-$APP}" \
    && python3 -m venv /venv \
    && /venv/bin/pip install --no-cache-dir -q \
         'pyinstaller==5.13.2' 'pyinstaller-hooks-contrib==2023.10' pyserial \
    && /venv/bin/pyinstaller --onefile --windowed --clean --noconfirm \
         --distpath /out --name "$OUT" "${APP}.py"

# 运行时镜像（GUI 程序需在 Orin 上配 DISPLAY，一般更推荐直接拷 /out 里的二进制运行）
FROM --platform=linux/arm64 ubuntu:20.04
ARG APP=tcp_server
ARG OUT_NAME=
ENV DEBIAN_FRONTEND=noninteractive

RUN apt-get update && apt-get install -y --no-install-recommends \
    python3-tk libx11-6 libxcb1 \
    && rm -rf /var/lib/apt/lists/*

COPY --from=builder /out/${OUT_NAME:-$APP} /usr/local/bin/app
ENTRYPOINT ["/usr/local/bin/app"]
