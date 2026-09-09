# 01-conn-metrics — 前置任务：先接 prom 栈（对应 front-requirements §3-2）

> 状态：方案（对方实施）。前置：无——本篇是其他所有篇的前置。
> 现状（对方回条 C）：tracing 已接好；**prometheus 未接**（无 pingora-prometheus、无 metrics 端口、无 fd 交接；systemd restart 模式；healthz 是业务面自造 JSON，保持不动）。

## 1. 目标

给 front 加上“ H2 优化是否真的 engaged”的证据链：h2/h1 请求计数、活跃连接数、上游（loopback）复用率。无此基线，后续每个加固项的效果都不可对照。

## 2. 改动点（抄现成的，不自己搓）

* **服务挂载**：抄官方 `pingora::apps` 的 `prometheus_` HTTP app 模式（docs.rs `pingora::apps` 0.8.1 有 `prometheus_http`/`http_` 应用；P4 已验证 `prometheus_http_service()` + `add_tcp("127.0.0.1:PORT")` 作为 service 挂入 `server.add_service()`）。
  端口只绑 loopback（metrics 不对外），端口号对方定（建议 9090，避开 7777/业务口）。
* **业务指标**（全部用 `prometheus` crate static 宏注册，自动暴露）：
  * `front_requests_total{proto="h2"|"h1",method,status}` Counter — 在 `logging()` 里 +1（必经终点，成功失败全覆盖）；proto 取自 session 的 ALPN/版本。
  * `front_connections_active` Gauge — `new_ctx` +1、`logging()` -1（CTX 跨 phase 计数模式，官方 `ctx.rs` example 有活跃连接计数范式）。
  * `front_upstream_reused_total{reused="true"|"false"}` Counter — 在 `connected_to_upstream(reused, …)` 里按 `reused` 打点（复用率 = reused / total）。
* **不动**：healthz（业务面 JSON）保持现状；metrics 端口走 systemd restart（现状无 fd 交接机制，重启断采如实接受，见回归风险）。

```rust
use prometheus::{register_int_counter_vec, register_int_gauge, IntCounterVec, IntGauge};
use std::sync::LazyLock;

static REQ_TOTAL: LazyLock<IntCounterVec> = LazyLock::new(|| {
    register_int_counter_vec!("front_requests_total", "front requests", &["proto", "method", "status"]).unwrap()
});
static CONN_ACTIVE: LazyLock<IntGauge> = LazyLock::new(|| {
    register_int_gauge!("front_connections_active", "active downstream conns").unwrap()
});
static UPSTREAM_REUSED: LazyLock<IntCounterVec> = LazyLock::new(|| {
    register_int_counter_vec!("front_upstream_reused_total", "upstream reuse", &["reused"]).unwrap()
});
```

## 3. 取值决策表

| 项 | 取值 | 依据 |
|---|---|---|
| metrics 端口 | 127.0.0.1:9090（对方可改） | 不对外，只给本机 Prometheus 抓 |
| proto 标签 | `"h2"` / `"h1"` | 验证 H2 engaged 的核心维度 |
| counter 位置 | `logging()` | 必经终点（P1 全序 #18），不断流 |

## 4. 回归风险

* metrics 端口无 fd 交接：systemd restart 瞬间断采，告警规则要容忍抓取空窗（对方无零停机升级机制，见 front-requirements §5 上下文）。
* Gauge 增减必须配对（`new_ctx` +1 / `logging` -1）：`logging` 必经所以安全，但 panic 路径要确认不跳过（pingora 保证 `logging` 在资源释放前跑）。

## 5. 对方验证命令

```bash
# 跑起来后：
curl -s 127.0.0.1:9090/metrics | grep -E 'front_requests_total|front_connections_active|front_upstream_reused_total'
# 打几发 EdgeOne 回源流量（或直连 7777），确认 proto="h2" 有计数、reused="true" 占比>0
```
