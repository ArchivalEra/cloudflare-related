#!/usr/bin/env bash
# 一键跑 XIU2/CloudflareSpeedTest 完整测速（延迟 + 下载），生成 result.csv
# 用法: bash scripts/run-cfst.sh [结果文件路径]   (默认 data/result.csv)
# 依赖: curl, tar, 以及能访问 github.com（国内可给脚本前面加 ghfast.top 镜像前缀）
set -euo pipefail

cd "$(dirname "$0")/.."   # 回到 accelerate/

RESULT="${1:-data/result.csv}"
BIN=".cfst-bin/cfst"
VER="v2.3.5"
URL="https://github.com/XIU2/CloudflareSpeedTest/releases/download/${VER}/cfst_linux_amd64.tar.gz"

if [ ! -x "$BIN" ]; then
  echo ">> 下载 CloudflareSpeedTest ${VER} ..."
  mkdir -p .cfst-bin && cd .cfst-bin
  curl -fL --max-time 120 -o cfst.tar.gz "$URL"
  tar -zxf cfst.tar.gz --strip-components=1
  chmod +x cfst
  cd ..
fi

mkdir -p data
# 官方段（curl 拿不到就退回工具自带 ip.txt）
if [ ! -s data/ip.txt ]; then cp .cfst-bin/ip.txt data/ip.txt 2>/dev/null || true; fi

echo ">> 开始测速（官方段 ${PWD}/data/ip.txt）..."
"$BIN" -f data/ip.txt \
  -n 200 -t 4 \
  -tp 443 \
  -url https://cf.xiu2.xyz/url \
  -tl 300 -tlr 0.3 \
  -dn 20 -sl 2 \
  -o "$RESULT" -p 15

echo ">> 完成。Top 结果:"
head -n 12 "$RESULT"
echo ">> 最优 IP（result.csv 第一行）:"
sed -n '2p' "$RESULT"
