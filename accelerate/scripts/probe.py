#!/usr/bin/env python3
"""
CF 优选 IP 轻量测速器（纯标准库，无第三方依赖）

原理：Cloudflare 是任播（anycast），同一官方 IP 段在不同地区/运营商路由到不同
机房。所谓"优选"= 从官方段里采样 IP，测出从你当前位置 TCP 连接延迟最低的那批，
让客户端直连这些 IP（SNI 保持你的隧道/站点域名），从而绕开 CF 默认调度到的不佳节点。

与 XIU2/CloudflareSpeedTest 的区别：
- 本脚本无二进制、无依赖，任意机器（含国内服务器/软路由）可直接 python3 运行
- 可选 -tls-sni 校验：验证该 IP 能否为你的域名完成 TLS 握手（区分"真反代/假反代"）
- 只做延迟排序，不做下载测速（下载测速请用 CFST，见 run-cfst.sh）

用法：
  # 默认：采样 200 个 IP，测 443 端口 TCP RTT，输出 result.csv + 屏幕 Top 10
  python3 probe.py -n 200

  # 自定义端口（CF 支持的 HTTPS 端口：443, 2053, 2083, 2087, 2096, 8443）
  python3 probe.py -n 300 -p 443,2053,8443

  # 指定 IP 段文件（默认 data/cf-ips-v4.txt 官方段）
  python3 probe.py -f data/ip.txt -n 400

  # 校验 TLS SNI：只保留能为你域名完成 TLS 握手的 IP（推荐！）
  python3 probe.py -n 500 -tls-sni cf-azure.moons.de5.net -o result.csv

输出：
  - 屏幕打印按 RTT 排序的 Top N
  - CSV（UTF-8 带 BOM，Excel 可直接打开）：
    IP,端口,连接数,成功数,RTT最小值(ms),RTT平均(ms),TLS_SNI校验
"""

import argparse
import csv
import ipaddress
import random
import socket
import ssl
import sys
import threading
import time
from concurrent.futures import ThreadPoolExecutor

DEFAULT_PORTS = (443,)
DEFAULT_RANGES = "data/cf-ips-v4.txt"  # 相对 accelerate/ 运行；也可用绝对路径
RANGES_FALLBACK = [
    "104.16.0.0/13", "172.64.0.0/13", "162.158.0.0/15",
    "141.101.64.0/18", "108.162.192.0/18",
]


def load_ranges(path):
    """读取 IP 段文件：每行一个 IP 或 CIDR，支持 # 注释与空行"""
    nets = []
    try:
        with open(path, "r", encoding="utf-8") as f:
            lines = [ln.strip() for ln in f if ln.strip() and not ln.strip().startswith("#")]
        for ln in lines:
            try:
                nets.append(ipaddress.ip_network(ln, strict=False))
            except ValueError as e:
                print(f"[warn] 跳过无效段 {ln}: {e}", file=sys.stderr)
    except FileNotFoundError:
        print(f"[warn] {path} 不存在，使用内置兜底段", file=sys.stderr)
        nets = [ipaddress.ip_network(x) for x in RANGES_FALLBACK]
    return nets


def sample_ips(nets, n):
    """
    按 /24 粒度采样：把大段切分成 /24，每个 /24 内随机挑 1 个 host。
    这与 CloudflareSpeedTest 默认行为一致（每 /24 随机测 1 个）。
    返回去重后的候选 IP 列表，最多 n 个。
    """
    pools = []
    for net in nets:
        if net.version != 4:
            continue
        if net.prefixlen <= 24:
            # 切成 /24，每个里随机取 1 个非网络/广播地址
            for sub in net.subnets(new_prefix=24):
                hosts = list(sub.hosts())
                if hosts:
                    pools.append(random.choice(hosts))
        else:
            hosts = list(net.hosts())
            if hosts:
                pools.append(random.choice(hosts))
    random.shuffle(pools)
    seen, picked = set(), []
    for ip in pools:
        if len(picked) >= n:
            break
        if ip not in seen:
            seen.add(ip)
            picked.append(ip)
    return picked


def tcp_rtt(ip, port, timeout, attempts):
    """TCP 连接 RTT（min of attempts），失败返回 None"""
    rtts = []
    for _ in range(attempts):
        t0 = time.perf_counter()
        try:
            with socket.create_connection((str(ip), port), timeout=timeout):
                rtts.append((time.perf_counter() - t0) * 1000)
        except OSError:
            return None
    return min(rtts)


def tls_sni_ok(ip, port, hostname, timeout):
    """用 server_hostname 做 TLS 握手；握手成功说明该 IP 能为你的域名完成终结（真反代）"""
    try:
        ctx = ssl.create_default_context()
        with socket.create_connection((str(ip), port), timeout=timeout) as sock:
            with ctx.wrap_socket(sock, server_hostname=hostname) as tls:
                tls.getpeercert()
        return True
    except (OSError, ssl.SSLError, socket.timeout):
        return False


def main():
    ap = argparse.ArgumentParser(description="CF 优选 IP 轻量测速器")
    ap.add_argument("-f", "--file", default=DEFAULT_RANGES, help="IP 段文件（默认 data/cf-ips-v4.txt）")
    ap.add_argument("-n", "--count", type=int, default=200, help="采样 IP 数量（默认 200）")
    ap.add_argument("-p", "--ports", default="443", help="逗号分隔端口（默认 443）")
    ap.add_argument("-t", "--timeout", type=float, default=2.0, help="单次连接超时秒（默认 2）")
    ap.add_argument("-a", "--attempts", type=int, default=2, help="每 IP 测速次数取 min（默认 2）")
    ap.add_argument("-c", "--concurrency", type=int, default=64, help="并发线程数（默认 64）")
    ap.add_argument("-tls-sni", dest="tls_sni", default=None,
                    help="校验该 SNI 的 TLS 握手（填你的隧道域名/站点域名）")
    ap.add_argument("-o", "--output", default="result.csv", help="输出 CSV 路径（默认 result.csv）")
    ap.add_argument("--seed", type=int, default=None, help="随机种子（便于复现）")
    args = ap.parse_args()

    if args.seed is not None:
        random.seed(args.seed)

    ports = [int(p) for p in args.ports.split(",") if p.strip()]
    nets = load_ranges(args.file)
    ips = sample_ips(nets, args.count)
    print(f"共 {len(ips)} 个候选 IP，测端口 {ports}，并发 {args.concurrency}")

    rows = []
    lock = threading.Lock()
    done = 0

    def work(ip):
        nonlocal done
        for port in ports:
            rtt = tcp_rtt(ip, port, args.timeout, args.attempts)
            tls = ""
            if rtt is not None and args.tls_sni:
                tls = "OK" if tls_sni_ok(ip, port, args.tls_sni, args.timeout) else "FAIL"
            with lock:
                if rtt is not None:
                    rows.append({"ip": str(ip), "port": port, "rtt": round(rtt, 2),
                                 "tls": tls or "N/A"})
                done += 1
                if done % 50 == 0:
                    print(f"  进度 {done}/{len(ips)}", file=sys.stderr)

    with ThreadPoolExecutor(max_workers=args.concurrency) as ex:
        for ip in ips:
            ex.submit(work, ip)

    rows.sort(key=lambda r: r["rtt"])
    if not rows:
        print("无可用 IP", file=sys.stderr)
        sys.exit(1)

    # 屏幕 Top 10
    print("\n===== Top 10 =====")
    for r in rows[:10]:
        print(f"  {r['ip']:>16}:{r['port']:<5} {r['rtt']:>8.2f} ms  TLS={r['tls']}")

    # 写 CSV（带 BOM，Excel 友好）
    with open(args.output, "w", encoding="utf-8-sig", newline="") as f:
        w = csv.writer(f)
        w.writerow(["IP", "端口", "RTT(ms)", "TLS_SNI"])
        for r in rows:
            w.writerow([r["ip"], r["port"], r["rtt"], r["tls"]])
    print(f"\n结果已写入 {args.output}（共 {len(rows)} 个可用 IP）")


if __name__ == "__main__":
    main()
