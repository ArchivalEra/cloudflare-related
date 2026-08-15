# Tunnel 接入优选实战（对接 sing-box / cloudflared）

> 关联文档：`vps-related/sing-box/docs/cf-prefer-guide.md`（隧道 ws/grpc/ss 优选实验指南）。
> 本文档补充该指南的「优选 IP 从哪来、怎么自动更新、具体命令」。

## 0. 拓扑回顾（隧道优选为什么能成立）

```
client ──TLS──> CF 优选节点(IP):443 ──SNI/Host 路由──> cloudflared 隧道 ──> VPS 内 sing-box (ws/grpc)
```

- 隧道公开主机名的 DNS 本质是 `CNAME -> <隧道UUID>.cfargotunnel.com`
- 客户端无论从哪个 CF 边缘 IP 进来，CF 都**按 SNI/Host 匹配隧道规则**，经隧道送回 VPS
- **接入 IP 与隧道域名解耦** → 把 server 换成优选 IP，SNI/Host 保持隧道域名，路由不受影响

## 1. 获取优选 IP（三条路，按推荐排序）

### 路线 A：本机自测（推荐，最贴合你的网络）

```bash
cd accelerate

# 轻量：纯 python3 无依赖。用你的隧道域名做 TLS-SNI 校验（区分真/假反代）
python3 scripts/probe.py -n 500 -tls-sni cf-azure.moons.de5.net -o data/result.csv

# 完整：XIU2/CloudflareSpeedTest（延迟+下载，服务器上跑）
bash scripts/run-cfst.sh data/result.csv

# 提取 Top 20
bash scripts/gen-pref-ips.sh data/result.csv 20
```

> 在**客户端所在网络**（如家里/公司）或**出口服务器**上跑才有意义。
> 结果按运营商/地区差异巨大：移动→HKG、电信→SIN、联通→LAX。

### 路线 B：拉线上优选库（零本地测速，当日更新）

```bash
curl -fsSL https://raw.githubusercontent.com/ymyuuu/IPDB/main/BestCF/ipv4.csv -o data/online-ipv4.csv
# 或三网分线路：
curl -fsSL https://raw.githubusercontent.com/yuanxiawan/cfipv4db/main/cfip.csv -o data/cfip.csv
```

### 路线 C：社区优选域名（DNS 层，客户端零改动但依赖第三方）

接入域名 CNAME 到 `cf.090227.xyz` / `*.cloudflare.182682.xyz` / `cf-cname.xingpingcn.top`。
注意：**接入域名若也托管在 CF，CNAME 会被 CF 拦截(403)**，需把 DNS 托管他处或用 SaaS。

## 2. 应用到 sing-box 客户端（对应指南第 4 节）

```bash
cd accelerate

# 用 gen-client.sh 生成基础客户端配置（详见 sing-box 仓库 README）
bash /home/.../sing-box/scripts/gen-client.sh --from-server /etc/sing-box/config.json \
  --addr cf-azure.moons.de5.net --outputname config-client.json

# 删除 reality/hy2/tuic/shadowtls/ss-over-st 行后，把优选 IP 写进去：
python3 scripts/apply-pref-ip.py -c config-client.json -i data/preferred-ips.txt -n 5 -o out

# 校验
sing-box check -c out/config-client-pref01-<IP>.json
```

`apply-pref-ip.py` 只改带 TLS 的 vless/vmess/trojan/ss 行的 `server` 与 `server_port`，
**不动** `server_name`/`transport.host`/`path`；reality/shadowtls 行自动跳过。
生成 5 份（对应前 5 个 IP），客户端逐个试延迟，或做成 url-test 组自动切换。

### 多节点自动切换（应对优选失效）

在客户端配置里把 5 个优选 IP 行放进一个 urltest 组：

```jsonc
{
  "type": "urltest", "tag": "auto",
  "outbounds": ["vless-ws-pref1", "vless-ws-pref2", "vless-ws-pref3", ...],
  "url": "https://www.gstatic.com/generate_204",
  "interval": "1m"      // 60s 健康检查，坏了自动换
}
```

## 3. 端口兜底

CF 官方 HTTPS 端口：**443, 2053, 2083, 2087, 2096, 8443**（HTTP: 80, 8080, 8880, 2052,
2082, 2086, 2095）。443 被封锁/特殊对待时换用备用端口，客户端与隧道端需同步。

## 4. 自动更新（cron 定时重测）

```cron
# 每 6 小时重测一次并刷新 preferred-ips.txt（供手动/脚本取用）
0 */6 * * *  cd /path/to/accelerate && bash scripts/run-cfst.sh data/result.csv \
             && bash scripts/gen-pref-ips.sh data/result.csv 20
```

> 不推荐自动改 DNS（CF 明文禁止优选/代理节点，封号风险自担，见 research-2x-nz.md 第 5 节）。
> 若坚持走 DNS 自动更新，参考 ZhiXuanWang/cf-speed-dns（GitHub Actions 每 6h）或
> uxiaohan/CloudflareIP-dnspod-ddns（15 分钟切换）。

## 5. 验收清单

| 项 | 命令 | 期望 |
|---|---|---|
| 配置语法 | `sing-box check -c out/xxx.json` | 无错误 |
| 隧道连通 | 客户端经 socks 代理 `curl -k -x socks5://127.0.0.1:10808 https://cf-azure.moons.de5.net/ws` | 404/400（说明走到隧道且 WS 未握手，正常） |
| SNI 正确 | 错误 SNI 会命中 catch-all | 404 页与 204 测试区分 |
| 优选生效 | 对比默认域名解析 IP 与优选 IP 的 RTT | 优选明显更低 |

## 6. 已知边界（如实记录）

- **ss/tcp 隧道（cf-ss.moons.de5.net:8389）**：按 CF 文档，"Non-HTTP services require
  installing cloudflared on the client"。cloudflared 无强制指定优选 IP 的官方参数，
  ss 路线暂无法优选（保持默认边缘路径），详见 sing-box 指南第 5 节。
- **grpc**：dashboard 源站默认可能走 HTTP/1.1，grpc 需 HTTP/2 源站；若 ws 通 grpc 不通，
  属已知 gap，按指南记录处理，不要静默丢弃。
- **本仓库 result-us-20260815.csv 是美国节点实测**，数值不代表国内；仅作工具链演示。
