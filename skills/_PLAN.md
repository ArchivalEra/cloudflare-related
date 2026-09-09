# quiche + pingora 搜刮计划 spec（wayfinder map #1 的 destination）

> 状态：research 已闭环（T1–T4），T6 模板已定；T5 粒度决策挂起等 HITL。
> Tracker：[#1 map](https://github.com/ArchivalEra/cloudflare-related/issues/1)，
> [#2 T1](https://github.com/ArchivalEra/cloudflare-related/issues/2)
> [#3 T2](https://github.com/ArchivalEra/cloudflare-related/issues/3)
> [#4 T3](https://github.com/ArchivalEra/cloudflare-related/issues/4)
> [#5 T4](https://github.com/ArchivalEra/cloudflare-related/issues/5)
> [#6 T5](https://github.com/ArchivalEra/cloudflare-related/issues/6)
> [#7 T6](https://github.com/ArchivalEra/cloudflare-related/issues/7)。

## 1. 落点与格式（T6 已决）

- 落点：本仓库 `skills/` 扁平，一个功能一篇 `功能.md`（如 `skills/quiche-conn-lifecycle.md`）。
- `.gitignore` 白名单已补 `!skills/**`。
- 每篇模板（必须节）：

```markdown
# <功能名>（quiche/pingora）
> 一句话职责 / 前置篇 / 适用版本（锁版本，如 quiche 0.29.3 / pingora 0.8.1）

## 最小可用示例
## 关键 API/配置表
## 生产坑（附来源 issue/CVE）
## 可借鉴实现（官方 example + 社区项目链接）
## 版本与参考链接
```

- 口径：官方 + 高星第三方生产实现；二手博客只佐证；每篇锁版本；纯 md（暂不带 `scripts/` demo 包）。

## 2. 侦查结论摘要（T1–T4 closed，细节见各 ticket resolution comment）

### T1 quiche 官方全景（#2）
- 基线：`quiche 0.29.3`（2026-07-14），Rust ≥1.88，BoringSSL 经 `boring ^4.3` 自动编；master ~2506 commits。
- 模型：纯状态机无 I/O，App 负责 UDP+timer：`recv()/send()/timeout()/on_timeout()`。
- 旋钮全在 `Config::set_*`（流控初始全 0 必须显式设），CC 默认 CUBIC，可选 Reno/Bbr2Gcongestion。
- 0-RTT 默认关（`enable_early_data` + ticket-key 自轮换）；迁移经 `probe_path/migrate/path_event_next`。
- apps/h3i 声明 demo 非生产；`tokio-quiche 0.19.1`（`H3Driver` vs 自定义）是正道。
- 集成：curl quiche 后端仍 EXPERIMENTAL；nginx 旧补丁已死（停 1.16.x），走上游原生 QUIC。

### T2 quiche 社区实现（#3）
- 真成熟：Android DoH3（十亿级）、Cloudflare edge/Oxy/Private Relay/WARP 经 tokio-quiche、
  curl-quiche（客户端可用但后端标实验）、h3i（工具成熟）。
- 半成熟：pasque（MASQUE 最小形态，照抄对象）、monoio-quiche（io_uring 路线）、currentspace/http3。
- 顶层模式：别手搓事件循环，抄 `Router按DCID分发→IoWorker(recv→app→send)→timeout驱timer` + GSO/GRO + pacing。
- 大坑：零默认值陷阱、BoringSSL 构建税、降级（H2 fallback/eyeballing）+ qlog 可观测是生产门槛；
  `google/quiche` 与 `cloudflare/quiche` 同名不同物，搜索必须区分。

### T3 pingora 官方全景（#4）
- 基线：`pingora 0.8.1`（2026-08-17），MSRV 1.85；H1/H2 端到端，H3 仍 roadmap；rustls 仍 Highly Experimental；cache 标 experimental。
- 核心：`ProxyHttp`（2 必需 + ~31 可选 filter，全序见 phase.md）+ `Session` + CTX；
  `HttpPeer/PeerOptions`（四超时 + mTLS/UDS/CONNECT proxy）；`LoadBalancer + discovery + health_check`；
  `ConnectionPool`（同 Peer 才复用，idle=0 禁用）；`HttpCache`（`cache_key_callback` 默认 panic 必须覆写）。
- 运维：`ServerConf + -c/-d/-u/--upgrade` + SIGQUIT 交监听 fd + SIGTERM 优雅停；可观测 = logging/prom/sentry。
- 安全史：0.5.0/0.8.0 两次 smuggling 修补 + cache 默认 key 教训，写 skill 必须提。

### T4 pingora 社区实现·重点（#5）
- 首选引用三件套：`vicanso/pingap`（运营模板：插件契约+热加载+admin/etcd/ACME）、
  `zhu327/pingsix`（filter 链模板：`priority/phase/FilterVerdict` 最干净，APISIX 兼容）、
  `zentinelproxy/zentinel`（安全/隔离模板：外部 agent+WASM+熔断/配额）；备选 aralez（轻量 ingress）、pingclair（Caddy DX/ACME）。
- 趋同架构：五步插件链映射 ProxyHttp 相位；缓存命中短路放 `proxy_upstream` 前；统一 `Reject` 出口；
  LB 标配 `LoadBalancer + HttpPeer` + 后台健康刷；缓存全包官方 `pingora-cache`（自包 `CacheManager` 隔离 breaking）；
  热加载一律 `validate→原子swap→优雅排空`，listener/TLS 变更要求重启。
- 生产坑（有实证）：CVE-2025-4366 缓存命中走私（<0.5.0 必须升）、#946 WS 高负载偶发断（open）、
  `pingora-cache` API 高波动、rustls≠openssl 行为差、trailer/H3-CONNECT 短板、body 与重试互斥。

## 3. 建议篇目（候选，待 T5 grilling 锁定）

quiche（T1 原生候选 9 篇，T2 建议合并 FFI/移动端为附录 → 候选 7±2）：
- `quiche-conn-lifecycle.md`、`quiche-streams-zero-copy.md`、`quiche-http3-qpack.md`
- `quiche-cc-tuning.md`、`quiche-resumption-migration.md`
- `quiche-tokio-apps.md`（apps/h3i/tokio-quiche 合并）、`quiche-build-ship.md`、`quiche-integrations.md`
- FFI（`quiche-ffi-c.md`）待定单独成篇还是附录。

pingora（T3 原生候选 7 篇，T4 要求加运营/安全两篇 → 候选 8±1）：
- `pingora-proxy-lifecycle.md`（以 pingsix phase 表为骨）、`pingora-upstream-peer.md`
- `pingora-lb-failover.md`、`pingora-cache.md`、`pingora-pool-timeout.md`
- `pingora-tls-listeners.md`、`pingora-server-ops.md`
- `pingora-gateway-patterns.md`（pingap/pingsix/zentinel 蒸馏，T4 新增）、`pingora-waf-ratelimit.md`（备选）

总量预期：15±3 篇。T5 时按“全量不意味着一对一”合并/裁剪并排序（建议 proxy-lifecycle → upstream → lb → cache → ops 链式）。

## 4. 残雾（Not yet specified，T5 时毕业）
- `功能.md` 示例代码量上限；是否每篇附可跑最小 cargo demo。
- quiche FFI/移动端、pingora 运维观测深水区（graceful 细节/metrics 全表）单篇还是附录。
- 社区“成熟”阈值量化（star/维护时间/部署证据三选二即可引用）。
- `pingora-cache` breaking 隔离层写法；#946 复现矩阵是否进 skill。

## 5. 第二波 pingora 深挖（P1–P6 closed，细节见各 ticket resolution）

- [P1 filter 生命周期 #8](https://github.com/ArchivalEra/cloudflare-related/issues/8)：
  全序 0–18：`new_ctx → early_request → request(bool短路) → cache准入组 → proxy_upstream(bool短路) → upstream_peer(必需)`
  `→ connected/fail_to_connect(set_retry 回环) → upstream_request/body → upstream_response(缓存前)/response(缓存后)`
  `→ error_while_proxy(复用重试判定) → fail_to_proxy(兜底错误页) → logging(必跑)`；
  短路仅两处（request=true / proxy_upstream=false，均须手写响应）；`Err` 通走 `fail_to_proxy`。
- [P2 upstream/LB #9](https://github.com/ArchivalEra/cloudflare-related/issues/9)：
  `HttpPeer::new/new_uds/new_proxy/new_mtls`；`PeerOptions` 全字段表已定（含四超时/idle=0 禁用/ca 不进 hash 需自加 group_key）；
  selection 原名：`FNVHash/Random/RoundRobin/Consistent(Ketama)` + Weighted 容器；
  自带 discovery 仅 Static（TODO: DNS），`update()` 失败保旧池；健康检查 Tcp/Http（默认阈值 1/1，pingap 1/2）；
  核心无 slow-start（仅 pingclair 自实现）；failover 靠 select 迭代 + `set_retry` + CTX tries。
- [P3 缓存 #10](https://github.com/ArchivalEra/cloudflare-related/issues/10)：
  `CachePhase` 11 态；三必覆写（`request_cache_filter→enable`、`cache_key_callback` 默认 panic、`response_cache_filter` 默认不入库）；
  0.8.0 删默认 key（GHSA-f93w 投毒，key 必含 host+scheme+method）；stale 默认关（RFC 显式开）；
  `CacheLock` 防击穿（#392 范式）；`CacheManager` 隔离层草图已定；volatile 面：proxy-cache 集成 experimental + 疑似 main 分支 range 抽 crate 重构。
- [P4 server 运维 #11](https://github.com/ArchivalEra/cloudflare-related/issues/11)：
  `ServerConf` 全字段表（含 grace 300s/shutdown 5s/upgrade_sock 路径/`-t` 仅三项数值校验）；
  升级三步（同 upgrade_sock → 新进程 `-u` 收 fd → 老进程 `SIGQUIT` 交 fd + 排空）；
  可观测 = prom service + log 五级 + sentry（release 才生效）+ SSLKEYLOG；
  TLS 四后端互斥（openssl/boring 有 SNI callback，rustls/s2n 无）；cert 更新无热加载（#619 open）必走升级。
- [P5 网关 extraction #12](https://github.com/ArchivalEra/cloudflare-related/issues/12)：
  可抄契约骨架已定（Phases bitmask + `Verdict::Continue/Reject` + 单点 `send_rejection` + ArcSwap 快照重载）；
  热加载七实践（先验后换/单锁 publish/读侧 ArcSwap/增量调和/listener 分级/优雅排空/可观测回执）；
  文件指针行级（pingap `server.rs` / pingsix `pipeline.rs+runtime.rs` / zentinel `reload/` 全套）。
- [P6 坑与基准 #13](https://github.com/ArchivalEra/cloudflare-related/issues/13)：
  CVE-2025-4366 证实（<0.5.0，修 fda3317）；#946 WS 竞态证实 open（0.8.1，容器低 CPU 复现）；
  cache 易变证实（0.8.0 删默认 key）；rustls 差部分证实（#703/#716/#792/#627，`CA:TRUE leaf` 判未证实）；
  trailer/H3-CONNECT 部分证实（#514/CONNECT 默认关）；body-retry 互斥证实（#575，需 `enable_retry_buffering`）；
  基准只述方法不引数字（四家 HW/工具/维度均不同，不可比）。

## 6. 落库状态（goal 执行，2026-09-09）

pingora 8 篇正文已入库（共 734 行，均按 §1 五节模板）：

| 文件 | 行数 | 主来源 |
|---|---|---|
| `pingora-proxy-lifecycle.md` | 100 | P1 全序 0–18 + 短路/重试规则 |
| `pingora-upstream-peer.md` | 84 | P2 PeerOptions 全字段 + 复用 hash |
| `pingora-lb-failover.md` | 86 | P2 selection 原名 + 保旧池 + 阈值 |
| `pingora-cache.md` | 112 | P3 11 态 + 三必覆写 + CacheManager |
| `pingora-pool-timeout.md` | 72 | P2/P3 四超时 + 池三态 + fast_timeout |
| `pingora-tls-listeners.md` | 78 | P4 四后端对照 + P6 rustls 差 |
| `pingora-server-ops.md` | 114 | P4 ServerConf 全表 + 升级三步 |
| `pingora-gateway-patterns.md` | 88 | P5 契约骨架 + 热重载七实践 |

quiche 篇（§3 quiche 部分）尚未开工——wayfinder 上归 T5 后续 / 新 goal。
T5（#6）粒度决策：本 goal 执行即按 proposal 锁定的 15 篇（quiche 7 + pingora 8）落库，待人确认后转 done。

## 7. 下一步
1. T5 grilling（HITL）：锁定 3 节篇目 + 命名 + 排序 → 本 spec 修订为 v2。
2. 新 effort：按 v2 篇目写 skill 正文（每篇一 session 或一批次多 session，注意 100K 上限）。
