#!/usr/bin/env python3
"""
apply-pref-ip.py — 把优选 IP 应用到 sing-box 客户端配置

背景：CF 隧道优选的做法是「server 换优选 IP，SNI/server_name/Host 保持隧道域名不动」。
本脚本只改 outbound 里带 TLS 的行的 server 字段（及其成对 transport.ws/grpc 的 host 若等于原域名时不动），
server_name / reality / shadowtls 等字段一律不改。

用法：
  # 生成 5 份配置，分别用 preferred-ips.txt 里的前 5 个 IP，输出到 ./out/
  python3 scripts/apply-pref-ip.py -c config-client.json -i data/preferred-ips.txt -n 5 -o out

  # 不生成文件，只打印"如果换成第一个 IP，哪些行会变"的预览
  python3 scripts/apply-pref-ip.py -c config-client.json -i data/preferred-ips.txt --dry-run

注意事项：
- 对 vless/vmess/trojan/ss 等带 "tls": {"enabled": true} 的行生效（即 CF 直连优选行）。
- 对 reality / shadowtls 行自动跳过（那些行不能换 IP，换了会断）。
- 建议先 --dry-run 确认改动范围，再正式生成。
"""

import argparse
import copy
import json
import os
import sys

CF_PORTS = (443, 2053, 2083, 2087, 2096, 8443)  # CF 官方 HTTPS 端口


def read_ips(path, n):
    ips = []
    with open(path, "r", encoding="utf-8") as f:
        for ln in f:
            ln = ln.strip()
            if ln and not ln.startswith("#") and ":" not in ln:  # 只要 IPv4
                ips.append(ln)
            if len(ips) >= n:
                break
    return ips


def is_cf_line(out):
    """判断该 outbound 是否为「CF 直连优选」候选行：TLS 已启用且非 reality/shadowtls"""
    if out.get("type") not in ("vless", "vmess", "trojan", "ss"):
        return False
    tls = out.get("tls") or {}
    if not tls.get("enabled"):
        return False
    if tls.get("reality", {}).get("enabled"):
        return False
    return True


def patched_lines(cfg, ip, port=443):
    """返回 (新配置, 变更行 tag 列表)。不改 sni/server_name/host/path。"""
    new = copy.deepcopy(cfg)
    changed = []
    for out in new.get("outbounds", []):
        if not is_cf_line(out):
            continue
        if out.get("server_port", 0) not in CF_PORTS and out.get("server_port", 0) != port:
            continue
        old = out.get("server")
        out["server"] = ip
        out["server_port"] = port
        changed.append((out.get("tag") or out.get("type"), old, ip))
    return new, changed


def main():
    ap = argparse.ArgumentParser(description="把优选 IP 应用到 sing-box 客户端配置")
    ap.add_argument("-c", "--config", required=True, help="sing-box 客户端 config.json")
    ap.add_argument("-i", "--ips", required=True, help="优选 IP 列表文件（每行一个）")
    ap.add_argument("-n", "--count", type=int, default=5, help="生成几份配置（默认 5）")
    ap.add_argument("-p", "--port", type=int, default=443, help="目标端口（默认 443，CF 官方还支持 2053/2083/2087/2096/8443）")
    ap.add_argument("-o", "--outdir", default="out", help="输出目录（默认 ./out）")
    ap.add_argument("--dry-run", action="store_true", help="只预览不写文件")
    args = ap.parse_args()

    with open(args.config, "r", encoding="utf-8") as f:
        cfg = json.load(f)
    ips = read_ips(args.ips, args.count)
    if not ips:
        print("[error] 优选 IP 列表为空", file=sys.stderr)
        sys.exit(1)

    if args.dry_run:
        new, changed = patched_lines(cfg, ips[0], args.port)
        print(f"dry-run：用 {ips[0]}:{args.port} 会改动 {len(changed)} 个 outbound:")
        for tag, old, new_ip in changed:
            print(f"  - {tag}: server {old} -> {new_ip}")
        return

    os.makedirs(args.outdir, exist_ok=True)
    for idx, ip in enumerate(ips, 1):
        new, changed = patched_lines(cfg, ip, args.port)
        if not changed:
            print(f"[warn] {ip}: 没有可改写的 CF TLS outbound，跳过", file=sys.stderr)
            continue
        base = os.path.splitext(os.path.basename(args.config))[0]
        path = os.path.join(args.outdir, f"{base}-pref{idx:02d}-{ip}.json")
        with open(path, "w", encoding="utf-8") as f:
            json.dump(new, f, ensure_ascii=False, indent=2)
        print(f"[ok] {path}  ({len(changed)} 行: " + ", ".join(t for t, _, _ in changed) + ")")

    print(f"\n完成。把选中的配置交给客户端前先跑: sing-box check -c <文件>")


if __name__ == "__main__":
    main()
