# quiche-cc-tuning — 拥塞控制、pacing 与流控调优表

> 一句话职责：CC 选型 + pacing/HyStart/PMTUD/流控窗口，一张调优表。
> 前置：`quiche-conn-lifecycle.md`（Config 在哪配）。适用版本：`quiche 0.29.3`。

## 最小可用示例

```rust
fn tuned_config() -> quiche::Result<quiche::Config> {
    let mut config = quiche::Config::new(quiche::PROTOCOL_VERSION)?;
    // CC 选型（连接建立前定，握手内可回调版另调）
    config.set_cc_algorithm(quiche::CongestionControlAlgorithm::CUBIC);
    // pacing + HyStart++ 默认开，按需显式
    config.enable_pacing(true);
    config.enable_hystart(true);
    // 初始窗口：10 包 + 初始 RTT 333ms（默认值，RTT 已知可调）
    config.set_initial_congestion_window_packets(10);
    // 流控（与 lifecycle 零陷阱呼应）
    config.set_initial_max_data(10_000_000);
    config.set_initial_max_stream_data_bidi_local(1_000_000);
    config.set_initial_max_stream_data_bidi_remote(1_000_000);
    // PMTUD
    config.discover_pmtu(true);
    // DATAGRAM（MASQUE/DoH3 按需）
    config.enable_dgram(true, 100, 100);
    Ok(config)
}
```

## 调优表

| 旋钮 | 默认 | 什么时候动 |
|---|---|---|
| `set_cc_algorithm(Reno/CUBIC/Bbr2Gcongestion)` | CUBIC | 高丢包长肥管试 BBR2 系（`Bbr2Gcongestion`，gcongestion）；短平快内网 Reno 也行；动之前先有 01-metrics 式基线 |
| `enable_pacing` | 开 | 关了突发重传，基本别关；GSO 聚合（`send_quantum`）与 pacing 配合看 |
| `enable_hystart`（HyStart++） | 开 | 慢启动出口灵敏度，默认即可 |
| `set_initial_congestion_window_packets` | 10 包 | RTT 已知且稳定可加大（如 loopback/同机房）；公网别碰 |
| `set_initial_rtt` | 333ms | 同上，RTT 先验明确才调 |
| `set_max_pacing_rate` | 不限 | 下行封顶场景（如 egress 限速） |
| `discover_pmtu` / `set_pmtud_max_probes` | 关/默认 | 大包多（视频/冷拉）开 PMTUD；多 path 继承有坑（#2572/#2565 未决）先记 |
| 流控四件套 | **全 0** | 必显式设（lifecycle 零陷阱），按带宽时延积 × 并发流数算 |
| `send_quantum` / `get_next_release_time` | — | GSO 批量 + pacing 发包时刻，调优压测时看，不用日常配 |

## 生产坑

1. **先有基线再调 CC**：换 CC 是拿丢包率/重传率换吞吐的买卖，没基线就是玄学（对照 origin-front 01 篇方法论）。
2. **初始窗口是放大器**：loopback 上调大 init cwnd 美滋滋，公网同款配置就是放大攻击帮凶 + 慢启动爆冲，保持两套配置。
3. **`Bbr2Gcongestion` 语义 decoration**：tokio-quiche 下 `gcongestion/zero-copy` 特性语义官方只有一行说明，生产用之前先读 feature 文档 + 小流量灰度。
4. **DATAGRAM 队列是新的背压点**：`enable_dgram(recv, send)` 队列长不设，MASQUE 隧道突发会把内存打爆（对照 zentinel 100x/10MB 有界设计）。

## 可借鉴实现

* easyquic 的默认参数包（流/窗口/CC/0-RTT 一次配齐，新手抄它再调）。
* `qlog` + `set_keylog` + `QlogLevel`：调参必备观测（wireshark 解密对照）。

## 相关篇

* `quiche-conn-lifecycle.md`（Config 位）· `quiche-resumption-migration.md`（0-RTT 与 CC 交互）· `quiche-streams-zero-copy.md`（窗口）

## 版本与参考链接

* https://docs.rs/quiche/latest/quiche/enum.CongestionControlAlgorithm.html
* https://docs.rs/quiche/latest/quiche/struct.Config.html（`set_*` 全表）
