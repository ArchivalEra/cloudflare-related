# 02-timeouts — 上下游超时（对应 front-requirements §2-1，优先级第 1）

> 状态：方案（对方实施）。前置：无，可与 00 并行。
> 实测基线（对方回条 A，无 P99 仪表，tracing 无 span 统计，采样值 n=3）：
> loopback 磁盘命中 TTFB 15–16ms、100MB 全量 0.17s（~580MB/s）；
> multi-GB 冷拉 GDrive 16.6MB/s（并行分段）/ 7MB/s（单连）→ 3GB 典型 ~3 分钟；
> 管子本身可到 2Gbps，瓶颈 = 远端限速 × 本机其他业务争抢；
> EdgeOne 真实流量天然 Range 5MB 段（边缘单响应 ~70MB 上限），单请求秒级。

## 1. 目标

慢客户端 / 卡住的业务面不再无限占连接（slowloris 面）。硬约束：**只许 idle 语义（相邻读写间隔），禁总量计时**——3 分钟冷拉面前任何总量兜底都是杀良。

## 2. 改动点

* 上游（loopback 业务面 `HttpPeer.options`，见 pool-timeout 篇四超时表）：

```rust
fn main() {
    opts.connection_timeout = Some(Duration::from_secs(3)); // loopback 建连，3s 极松
    opts.read_timeout = Some(Duration::from_secs(10)); // idle 语义：相邻两次 read 间隔
    opts.write_timeout = Some(Duration::from_secs(10)); // idle 语义
    opts.idle_timeout = Some(Duration::from_secs(60)); // 池空闲保活
}
```

* 下游（client→front）：front 现状 `Server::new(None)` 默认 conf，idle 语义的下游读超时按 60s 配（回源段秒级请求 + 冷拉长尾，60s 是下限不是上限）。
* **明确不配**：`total_connection_timeout` 不设（总量计时禁区）；cold-pull 大流不加总量兜底。

## 3. 取值决策表

| 项 | 取值 | 依据（余量） |
|---|---|---|
| 上游 connect | 3s | loopback，TTFB 15ms → 200 倍余量 |
| 上游 read/write（idle） | 10s | Range 段秒级 → 3–10 倍；GDrive 抖动 + 本机争抢吃余量，不许收紧 |
| 上游 idle | 60s | 池保活常规值 |
| 下游 idle | 60s | 回源秒级 + 冷拉长尾下限 |
| 总量类 | 无 | 3GB/3min 禁区；“管子够粗”不是收紧理由（瓶颈不在管子） |

## 4. 回归风险

* 同机业务打满时 read 间隔抖动：10s 余量就是干这个的；若 00 日志显示 idle 超时误杀 committed Range 段，再放宽到 30s（调参，不改结构）。
* P99 缺失：tracing 无 span 统计是观测债，01 落地后如要补，先给业务面加 span（本篇不管）。

## 5. 对方验证命令

```bash
# 慢客户端模拟：连上不发完头，看 ~60s 被掐（下游 idle）
# 卡住上游模拟：业务面 sleep 15s，看上游 read 10s 先超时并走 fail_to_proxy（查 00 日志 err 字段）
# 回归：3GB 冷拉全程通过（禁总量，无误杀）；Range 5MB 段 P99 不劣化（对 01 基线）
```
