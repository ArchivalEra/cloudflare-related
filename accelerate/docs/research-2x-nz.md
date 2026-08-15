# 2x.nz《试试Cloudflare IP优选》研究笔记 + 子代理调研汇总

> 来源文章：https://2x.nz/posts/cf-fastip （AF Studio，2026-08-15 抓取全文）
> 本文档 = 文章要点 + 4 个并行调研代理的成果汇总（工具生态 / BYOIP 与优选域名 /
> Tunnel 接入 / IP 段与实测数据）。

## 1. 什么是优选（文章原意）

**优选 = 选择一个国内访问速度更快的 Cloudflare 节点**。CF 官方分配的 IP 在国内延迟高
甚至不可达；优选把域名手动解析到那些国内访问更快的 CF IP，显著提升可用性与加载速度。

实现优选需要同时做到两件事：

1. **自己控制路由规则**（规则层）
2. **自己控制 DNS 解析**（解析层）

普通"小黄云"解析时，CF 替你做了这两件事：写一条 DNS 指向 CF + 建一条路由规则。
关闭小黄云后路由规则会被删除，就没法用了。**SaaS 与 Worker 路由改变了这一点**——用它们
时这两件事都可以自己做：自己写 SaaS/Worker 规则（规则层），自己写 CNAME 指向优选节点
（解析层）。这就是为什么经由 SaaS 或 Worker 路由的流量可以做优选。

> [!tip] 文章作者提示：所有优选一个域名即可，无需两个域名（如 `s.2x.nz` 与 `s-s.2x.nz`
> 即可完成优选）。

## 2. 优选域名来源

### 传统社区优选域名

- 常用：`cf.090227.xyz`（CMLiussss 维护的优选域名汇总页）
- 原理：扫描 CF 官方 IP 段，整理出国内延迟最低的 IP
- 实测（2026-08-15）：`cf.090227.xyz` 解析到 172.64.152.241 等官方 anycast IP，是真实内容站
- 同类：`cloudflare.182682.xyz`（WeTest.Vip 维护，泛域名，约 15 分钟自动更新）、
  `cf-cname.xingpingcn.top`（约 40 分钟更新）、`bestcf.030101.xyz`（移动专线）等

### Cloudflare BYOIP 优选

- **BYOIP = Bring Your Own IP**：用户自带 IP 段托管给 Cloudflare，在 CF 全球节点宣告，
  受益于其加速与安全。来源：https://developers.cloudflare.com/byoip/
- 关键 ASN：**AS209242（Cloudflare London, LLC）** 用于 Spectrum/BYOIP，宣告的是客户
  自带的非 CF 段（ipregistry 列出 355 个 v4 范围，从 Hoster 到英国 NCSC 段都有）
- 找 BYOIP：查 https://ipregistry.co/AS209242#ranges ，用 ITDog 强制绑定 IP 访问你的
  CF 服务，**不返回 403 即可用**（返回 404 正常，如 R2 直连）
- 风险：
  - 有些 BYOIP 会强制跳转到它自己的网站——看测试日志是否有重定向，别让网站成为他人引流站
  - **不可长久依赖**：建议定时脚本筛选失效 IP，并混入官方段防止宕机

## 3. 各类优选方案（文章给的路线）

### 3.1 Worker 项目优选（最简单）

1. Page 项目先转成 Worker
2. 写 Worker 路由：`你的域名 + /*`
3. DNS 解析指向优选域名（不开 CF 代理）

### 3.2 Worker 反代全球并优选（进阶，脚本见 scripts/worker-proxy.js）

原理：Worker 反代你的源站，入口节点做优选；源站收到的 Host 仍是源站域名。
创建 Worker 粘贴代码 → 改 `domain_mappings` → 建路由 → 写一条 CNAME 到优选域名即可。
**适合 HTTP(S) 站点；代理/隧道流量不适合（Host 头是源站域名，不是隧道域名）。**

### 3.3 Cloudflare R2 优选

绑定自定义域 → 域名-规则-Cloud Connector → 写解析指向优选域名
（如 `fast-r2.2x.nz` CNAME `cf.090227.xyz`）。

### 3.4 传统 SaaS 优选（多域名路线）

CF for SaaS 让一个不改变 NS 的域名受益于 CF 网络。步骤（单域名可用子域名）：

```
CF SaaS DNS:
  origin.yourdomain.com -> 源站（开小黄云）
  cdn.yourdomain.com    -> CF 优选域名（不开代理）
  pro.yourdomain.com    -> cdn.yourdomain.com（最终访问域名）

CF SaaS:
  添加自定义主机名 pro.yourdomain.com，源站为 origin.yourdomain.com
```

流程：用户访问 pro.yourdomain.com → CNAME 到 cdn 优选域名，携带源主机名 →
优选节点识别源主机名 → 查回退源 → 回退到源站内容。

> [!WARNING] 新接入域名的 SSL 默认设为"完全"，记得改为"灵活"。

### 3.5 Tunnel（ZeroTrust）优选

先按 SaaS 优选做好接入（源站即 Tunnel）→ 再创建一条 **Tunnel 规则**：域名 = 最终访问域名，
源站与 SaaS 回退源一致（否则命中 catch-all，无法访问）→ 最后写一条 CNAME 到优选域名。

> 这是文章给"域名接入优选"的路线；若你的场景是"客户端直连优选 IP 访问隧道内代理"
> （sing-box 隧道），见 docs/tunnel-prefer.md —— 那条路线不需要 SaaS。

## 4. 子代理调研要点

### 4.1 工具生态

- **XIU2/CloudflareSpeedTest**（28.5k stars，事实标准，Go 单二进制，v2.3.5）
  - 原理：遍历官方段 → TCPing（可换 HTTPing）测延迟排序过滤 → 对低延迟一批做真实下载测速
  - 关键参数：`-n 200`（线程）、`-t 4`（每 IP 次数）、`-tp 443`（端口）、
    `-url`（>200MB 的 CF 下载测速地址）、`-tl 200 -tlr 0.2`（延迟/丢包上限）、
    `-dn 20 -sl 5`（下载测 20 个且 >5MB/s）、`-f ip.txt`（IP 段文件）、`-o result.csv`、
    `-dd`（只测延迟）、`-allip`（全量扫）
  - result.csv 第一行即"下载最快且延迟最低"的 IP；格式：
    `IP,已发送,已接收,丢包率,平均延迟,下载速度(MB/s),地区码`
  - 注意：HTTPing 在服务器上跑属扫描行为，可能被商家暂停；TCPing 时 `-n` 别太高
- 衍生/替代：cf-speed-dns（GitHub Actions 每 6h 自动更新 DNS）、gslege/CloudflareIP
  （纯 Python 自扫 + 生成 VLESS 订阅，`vless://...@优选IP:443?...&sni=真实域名`）、
  RinTosaka/cfst4v2rayN（读 result.csv 前 10 写入 v2rayN guiNDB.db）、eooce/Merge-sub
  （订阅链接后加 `?CFIP=优选IP&CFPORT=端口` 动态替换节点地址）

### 4.2 客户端接入优选 IP 的标准做法

**server 填优选 IP，SNI/server_name/Host 保持原域名**（TLS 握手用 SNI 携带域名，
CDN 据此路由并校验证书；TCP 连接落在优选 IP 上）。sing-box 的 `tls.server_name` 文档
明确：填域名时放入握手作 SNI，填 IP 时不放入——所以 server=IP + server_name=域名是合法组合。
Clash/v2rayN 同理：Address=优选 IP，TLS SNI/伪装域名=原域名。

### 4.3 Tunnel 直连优选（对接 sing-box / v2rayN）

- 原理：Tunnel 公开主机名本质是 CNAME 到 `<隧道UUID>.cfargotunnel.com`；客户端无论从哪个
  CF 边缘 IP 进来，CF 按 **SNI/Host 匹配隧道规则**，经隧道送回 VPS。**接入 IP 与隧道域名解耦**
  ——换任何优选 IP 都能路由到同一隧道，这就是优选能成立的根本原因。
- 配置：Address=优选IP、Port=443、Network=ws、Host/SNI=真实隧道域名、Path=隧道 ws 路径
- 坑：
  - Host/SNI 必须匹配隧道公开主机名，否则命中 catch-all 404
  - CF 官方 HTTPS 端口只有 **443, 2053, 2083, 2087, 2096, 8443**（HTTP: 80, 8080, 8880,
    2052, 2082, 2086, 2095）；443 被封时可换备用端口，客户端与隧道端同步
  - 优选 IP 无永久性：CF anycast 路由动态变化 + 热门 IP 被复用/封锁，
    社区反馈"几小时到几天失效/变慢"；业界重测周期 15 分钟~12 小时
  - 免费隧道 UDP 打包成 TCP，hy2/UDP 协议不适用；vless-ws/grpc 无此问题
- 自动化：客户端 url-test/fallback 组（60s 健康检查自动切换）或 cron 定时重测
  （参考 lee1080/CloudflareSpeedTestDDNS、uxiaohan/CloudflareIP-dnspod-ddns 15 分钟切换）

### 4.4 优选 IP 段与实测数据

- **官方 IPv4 段（2026-08-15）**，共 15 段 ~152 万地址：

  ```
  173.245.48.0/20  103.21.244.0/22  103.22.200.0/22  103.31.4.0/22
  141.101.64.0/18  108.162.192.0/18 190.93.240.0/20  188.114.96.0/20
  197.234.240.0/22 198.41.128.0/17  162.158.0.0/15   104.16.0.0/13
  104.24.0.0/14    172.64.0.0/13    131.0.72.0/22
  ```

  IPv6 7 段：2400:cb00::/32、2606:4700::/32、2803:f800::/32、2405:b500::/32、
  2405:8100::/32、2a06:98c0::/29、2c0f:f248::/32（IPv4 优选优先）
- **社区扫段重点**（XIU2 ip.txt 已裁剪，官方段自 2022 年未变）：
  - `104.16.0.0/13`（按 /12 扫，覆盖 104.16–104.31）
  - `172.64.0.0/13` 细分多段（172.64 部分 /24 是回源 IP 不可用，需细分跳过）
  - `141.101/173.245/190.93` 等段偏回源用途，几乎扫不出低延迟
- **真实国内优选结果**（2026-08-15 抓取，按运营商分线路）：
  - **移动 → 香港 HKG**（85–102ms，如 104.17.146.22、104.25.242.240）
  - **电信 → 新加坡 SIN**（67–97ms，如 162.159.1.56、172.64.42.48）
  - **联通 → 洛杉矶 LAX**（186ms 左右，较难优选）
  - 95% 的优选结果落在 `104.16.0.0/13` 内，其次 `172.64.0.0/13`、`162.158.0.0/15`
  - 线上优选库（当日更新）：ymyuuu/IPDB（BestCF/ipv4.csv）、yuanxiawan/cfipv4db、
    svip-s/cloudflare_ip（含第三方反代段，注意区分）
- 2025 动态：CF×京东云合作（中国区流量路由到京东云数据中心，AI 推理延迟最高降 80%），
  面向企业合规接入，个人自建不适用

## 5. 风险与坑（务必看）

| 风险 | 说明 |
|---|---|
| **封号** | CF 明文禁止"优选IP 和 CF 代理节点"（ddgth/cf2dns 警告，链接到 landiannews 报道）。个人自用风险自负，避免大规模/商用 |
| **第三方优选 IP"不干净"** | V2EX 案例：CNAME 到三方优选 IP 后，一个已备案域名第二天根域名 connection reset。建议只用不重要的域名（eu.org 之类）测试 |
| **假反代/伪优选** | 判别标准：用**真实目标站 SNI** 能否完成 TLS 握手并回源。仅能响应白名单 SNI、或 `/cdn-cgi/trace` 可用但任意 SNI 握手失败 = 受限/假反代，代理场景不可用（参考 ToiCF/CF-Workers-CheckProxyIP） |
| **404 测试法** | 对真 CF 站点访问随机路径 → 返回真实 404；返回 301 自跳/超时 = 纯反代端点或引流设计 |
| **优选失效** | 无永久性，需定时重测 + 多 IP 兜底；热门前缀被 GFW/CF 封锁属常态 |
| **CNAME 403** | 若接入域名也托管在 Cloudflare，CNAME 指向第三方优选域名会被 CF 拦截（403）；把 DNS 托管到他处或用 SaaS |

## 6. 结论与建议

1. **首选官方段自测**：XIU2/CloudflareSpeedTest（服务器）或本仓库 scripts/probe.py（轻量）
2. **客户端接法**：server=优选IP + SNI/Host=原域名 + url-test 多节点自动切换
3. **按运营商维护**：移动偏 HKG、电信偏 SIN、联通难优选；各自测各自的
4. **BYOIP/三方优选谨慎**：可用于低价值域名，配失效预案
5. **完整流程**见 `docs/tunnel-prefer.md`，工具链见 `README.md`
