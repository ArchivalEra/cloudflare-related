#!/usr/bin/env bash
# 从测速 CSV 提取优选 IP，生成:
#   - data/preferred-ips.txt   (每行一个 IP，按 RTT 排序，可喂给客户端/脚本)
#   - 屏幕输出逗号分隔 Top N（可直接用于订阅 ?CFIP= 参数）
# 用法: bash scripts/gen-pref-ips.sh [result.csv] [TopN]
set -euo pipefail

cd "$(dirname "$0")/.."

CSV="${1:-data/result-us-20260815.csv}"
TOP="${2:-20}"
OUT="data/preferred-ips.txt"

if [ ! -s "$CSV" ]; then
  echo "[error] 找不到 $CSV，先跑 scripts/probe.py 或 scripts/run-cfst.sh" >&2
  exit 1
fi

# 兼容 probe.py 输出(IP,端口,RTT) 与 CFST 输出(IP,...,平均延迟,...)
# 取第 1 列 IP，按第 3 列(RTT)/第 5 列(平均延迟)数值升序
TMP="$(mktemp)"
if head -1 "$CSV" | grep -q "平均延迟"; then
  tail -n +2 "$CSV" | sort -t, -k5 -n -k2 -n > "$TMP"
else
  tail -n +2 "$CSV" | sort -t, -k3 -n > "$TMP"
fi
cut -d, -f1 "$TMP" | head -n "$TOP" > "$OUT"
rm -f "$TMP"

echo ">> 已写入 $OUT（Top $TOP）:"
cat -n "$OUT"
echo
echo ">> 逗号分隔版本（可作订阅 CFIP 参数）:"
paste -sd, "$OUT"
