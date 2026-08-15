# accelerate — Cloudflare 域名优选研究与实践

> 目标：让国内客户端访问 Cloudflare 上的服务（本站点 / Worker / R2 / **Tunnel 内网穿透**
> / 隧道内代理）时，不再被 CF 默认调度到的高延迟节点拖累。
> 核心思路只有一句话：**把"接入 IP"换成离你更近的优选节点，SNI/域名保持不动。**

## 为什么需要优选

Cloudflare 是 **anycast**：同一 IP 段在全球各机房同时宣告，用户访问时由路由协议
就近分配。但官方分配给中国内地访客的节点往往延迟高、丢包多。**优选 = 从官方 IP 段
（或 BYOIP 段）里挑出从你所在网络 TCP 连接延迟最低的那批 IP，让客户端/解析直接打到
它们**，而 TLS 的 SNI、HTTP 的 Host 保持你原有的域名不变——CF 边缘照样按 SNI 把
流量路由到你的 Worker/站点/隧道。

## 目录结构

```
accelerate/
├── README.md                    ← 本文件
├── docs/
│   ├── research-2x-nz.md        ← 2x.nz《试试Cloudflare IP优选》文章要点 + 子代理调研汇总
│   └── tunnel-prefer.md         ← Tunnel 接入优选实战（对接 sing-box / cloudflared）
├── scripts/
│   ├── probe.py                 ← 纯 Python 轻量优选测速器（无依赖，任意机器可跑）
│   ├── run-cfst.sh              ← 一键跑 XIU2/CloudflareSpeedTest（延迟+下载测速）
│   ├── gen-pref-ips.sh          ← 从测速 CSV 提取 Top N 优选 IP
│   ├── apply-pref-ip.py         ← 把优选 IP 批量写入 sing-box 客户端配置
│   └── worker-proxy.js          ← Worker 反代全球并优选（改 DNS 即可，客户端零改动）
└── data/
    ├── cf-ips-v4.txt            ← CF 官方 IPv4 段（cloudflare.com/ips-v4，2026-08-15 抓取）
    ├── ip.txt                   ← 社区裁剪后的官方段（XIU2 推荐扫段，重点 104.16/13 与 172.64/13）
    ├── result-us-20260815.csv   ← 本机（美国）实测结果，2156 个可用 IP，含最优列表
    └── preferred-ips.txt        ← 从中提取的 Top 20 优选 IP
```

## 快速上手

```bash
# 1) 轻量测速（纯 python3，无依赖；-tls-sni 用你的隧道域名校验"真反代"）
cd accelerate
python3 scripts/probe.py -n 500 -tls-sni cf-azure.moons.de5.net -o data/result.csv

# 2) 或完整测速（XIU2/CloudflareSpeedTest，延迟+下载，结果更全）
bash scripts/run-cfst.sh data/result.csv

# 3) 提取 Top 20 优选 IP
bash scripts/gen-pref-ips.sh data/result.csv 20

# 4) 生成 5 份 sing-box 客户端配置（server=优选IP，SNI 保持域名）
python3 scripts/apply-pref-ip.py -c config-client.json -i data/preferred-ips.txt -n 5 -o out
```

完整流程与注意事项见 `docs/tunnel-prefer.md`。

## 三类优选方案（速查）

| 方案 | 原理 | 适合 | 难度 |
|---|---|---|---|
| 直连优选 IP | 客户端 `server` 填优选 IP，SNI/Host 保持域名 | 隧道代理（sing-box/v2rayN），本次主推 | ★★ |
| Worker 路由优选 | Worker 反代源站 + CNAME 到优选节点 | Page/Worker 站点、无需改客户端 | ★★ |
| SaaS/解析层优选 | CF for SaaS + 自定义主机名 + CNAME 优选 | 带域名的站点、多域名 | ★★★ |

> 详情、坑（404 识别真反代、BYOIP 风险、封号警告、优选失效周期）见 `docs/research-2x-nz.md`。

## 实操记录（2026-08-15，本仓库）

- 下载并运行 `XIU2/CloudflareSpeedTest v2.3.5`，对社区裁剪官方段（25 段）采样 **5955 个 IP**
- 结果：**2156 个可用**（TCP 443），最优 `104.26.8.123 @ 34.65 ms`（本机在美国）
- `scripts/probe.py` 带 `-tls-sni` 实测：可正确区分"真反代"（能完成任意域名 TLS 握手）与
  "假反代/受限节点"（握手失败）
- 注意：本机在美国，延迟数值不代表国内效果；**国内服务器上跑一遍才有意义**（结果因
  运营商/地区而异，移动偏 HKG、电信偏 SIN、联通较难优选）。

## 参考

- 文章：https://2x.nz/posts/cf-fastip 《试试Cloudflare IP优选！让Cloudflare在国内再也不是减速器！》
- 工具：https://github.com/XIU2/CloudflareSpeedTest （28.5k stars，v2.3.5）
- 优选库：ymyuuu/IPDB、yuanxiawan/cfipv4db（三网分线路）、svip-s/cloudflare_ip
- 关联文档：`vps-related/sing-box/docs/cf-prefer-guide.md`（隧道 ws/grpc/ss 优选实验指南）
