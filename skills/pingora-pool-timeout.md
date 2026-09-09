# pingora-pool-timeout — 连接池复用与超时调优

> 一句话职责：上游连接“何时复用、何时掐断、超时怎么配”的全部规则。
> 前置：`pingora-upstream-peer.md`（复用键定义）。适用版本：`pingora-pool / pingora-timeout 0.8.1`。
> 来源：[#4 T3](https://github.com/ArchivalEra/cloudflare-related/issues/4) / [#9 P2](https://github.com/ArchivalEra/cloudflare-related/issues/9)。

## 最小可用示例

```rust
use pingora_core::upstreams::peer::{HttpPeer, PeerOptions};

let mut opts = PeerOptions::new();
// 建连 3s / 读 10s / 写 10s / 空闲池 60s
opts.connection_timeout = Some(std::time::Duration::from_secs(3));
opts.total_connection_timeout = Some(std::time::Duration::from_secs(10));
opts.read_timeout = Some(std::time::Duration::from_secs(10));
opts.write_timeout = Some(std::time::Duration::from_secs(10));
opts.idle_timeout = Some(std::time::Duration::from_secs(60));
// H2 多路复用：单连接 100 流
opts.max_h2_streams = 100;

let mut peer = HttpPeer::new("1.1.1.1:443", true, "example.com".into());
peer.options = opts;
```

`ServerConf` 全局池水位（`conf.yaml`）：`upstream_keepalive_pool_size: 128`（每 Tokio worker 空闲连接数，
总上限 = 128 × threads，全局一致驱逐）。

## 关键 API/配置表

**连接池**（`pingora-pool`：`ConnectionPool / PoolNode / ConnectionMeta`）：每 group lock-free 热池，
高 RPS 下无全局锁。复用判等 = peer 的 `reuse_hash`（见 upstream 篇）；
同 Peer 才复用，不同 `group_key`/verify/CA/`max_h2_streams` 永不混池。
请求中出错的连接标不可复用。`examples/pipelining.rs` 演示上游复用/pipelining 行为。

**四超时**（`PeerOptions`，各管一段）：

| 字段 | 管辖 | 建议起点 |
|---|---|---|
| `connection_timeout` | TCP `connect()` 单次 | 1–3s；健康检查模板 1s |
| `total_connection_timeout` | 建连总体（含 TLS 握手） | connect 的 2–3 倍 |
| `read_timeout` | 每次 `read()`，读到即重置 | 按 P99 上游延迟 × 2 |
| `write_timeout` | 每次 `write()` | 同 read |
| `idle_timeout` | 池中空闲保活；`= 0` 禁用 | 业务 60s；探针必须 0 |

**fast_timeout**（`pingora-timeout`）：高性能 async 超时，懒初始化、无全局锁、10ms 对齐共享 timer，
bench 自报 4ns vs tokio 107ns。`ServerConf.fast_timeout_to_tokio_threshold_seconds`（默认 900s，
`null` 禁用）：超阈值改走 Tokio 原生 timeout，避免 fast-timeout map 滞留。

## 生产坑

1. **`idle_timeout` 语义三态**：`None` = 用默认（照样复用），`Some(0)` = 禁用复用，其他值 = 空闲上限。
   想关复用必须显式 0，写 `None` 关不掉。
2. **探针 peer 必须 `idle_timeout = 0`**：否则健康检查连接进业务池，TLS/verify 配置不同还会污染复用判断。
3. **H2 `max_h2_streams` 默认 1**：不改就是单连接单流并发，H2 多路复用名存实亡；pingap 生产配到 100。
   但该值进复用 hash，改前后连接不混池，滚动发布期池子会短暂分裂，属正常。
4. **读超时按次重置**：`read_timeout` 是每次 read 不是整响应；大 body 慢流场景下按次重置可能永远不触发，
   需另配总量/业务超时兜底。
5. **重试与池的配合**：`error_while_proxy` 默认按 `client_reused && !truncated` 自动判复用重试；
   大 body 未全量缓冲时不会重试（见 P6 #575），别指望池自动兜底大上传。

## 可借鉴实现

- pingap：探针 `idle_timeout = 0` + 关 verify；业务 `max_h2_streams` 可配；`upstream_keepalive_pool_size` 按 worker 数核算。
  监听调优（`pingap-proxy/README.md`）：`reuse_port / tcp_fastopen / tcp_idle / interval / probe_count` +
  `downstream_read/write_timeout` + H2 窗口/流控。
- aralez：曾测 keepalive/idle/recv_buf 调优，高负载下反而降级而弃用——池参数必须压测后定，不抄默认值。
- pingclair：拨号缓存 60s/512 条（`DynamicDialPlan`），DNS 变化与池复用的折中样本。
  分级超时：`limits{header/body/idle/request}` + `transport{connect/first_byte/between_reads}`（“H1/H2 只暴露一个读 timer 取严者”）。
- pingsix 池化（`USER_GUIDE.md`）：`keepalive_pool.idle_timeout → per-peer idle` + `timeout{connect/send/read}` + `retries/retry_timeout`。
- zentinel 长连接：WS 例 `timeouts{read/write 3600} + websocket #true`（WS 隧道超时配到小时级）。

## 版本与参考链接

- https://github.com/cloudflare/pingora/blob/main/docs/user_guide/pooling.md
- https://docs.rs/pingora-timeout/latest/pingora_timeout/
- https://github.com/cloudflare/pingora/blob/main/pingora-proxy/examples/pipelining.rs
