# quiche-tokio-apps — apps/h3i/tokio-quiche 实操地图与 H3 定位

> 一句话职责：别手搓事件循环——tokio 侧的标准写法 + 工具链地图。
> 前置：`quiche-conn-lifecycle.md`（裸循环长什么样，对照用）。适用版本：`quiche 0.29.3` + `tokio-quiche 0.19.1`。
> **定位声明（2026-09，国内语境）**：H3 在国内 UDP 让车道现实下是**储备粮不是主食**——
> 运营商 QoS 保游戏/语音，QUIC 包批量丢弃是常态；官方页面停在 H1/H2，pingora 开源版 H3 仍 roadmap。
> 本篇是“万一要 H3 / 出海 / UDP 归零兜底”时的地图，不是主攻方向。主攻见 8 篇 pingora。

## 最小可用示例（tokio-quiche H3 server，README/AGENTS 官方形状）

```rust
use futures::StreamExt;
use tokio_quiche::{listen, ConnectionParams};
use tokio_quiche::metrics::DefaultMetrics;

async fn serve() -> anyhow::Result<()> {
    // ConnectionParams → quiche::Config → h3::Config 级联（零陷阱在此配）
    let params = ConnectionParams::new();
    // 每连接一个 tokio task（IoWorker 在内部：recv→app→send）
    let mut listener = listen("0.0.0.0:4433", params, DefaultMetrics).await?;
    while let Some(controller) = listener.next().await {
        tokio::spawn(async move {
            // H3Events 经 controller.event_receiver_mut() 收（见 H3Driver 文档）
            let _ = controller;
        });
    }
    Ok(())
}
```

两条路线：**自定义 `ApplicationOverQuic`**（`on_conn_established/process_reads/process_writes/wait_for_data`，
worker 循环喂包）vs **现成 `H3Driver`**（client/server 两套 controller/event，`DriverHooks` sealed 不可自扩展）。

## quiche 与 pingora 的关系（定稿）

* **quiche（经 tokio-quiche）正是 pingora 缺的 H3 那块蛋糕**：pingora 开源 0.8.1 仅 H1/H2（#95/#905 无 ETA）；
  Cloudflare 自家 Oxy/WARP/Private Relay 全走 tokio-quiche；社区 pingclair 已是 `pingora-proxy(H1/H2) + quiche(H3)` 双栈量产形态。
* **但不是即插即用**：filter 链 vs 状态机+H3Driver，中间缺转接层（连接表/DCID 分片/命令通道）；
  Oxy 上层 HTTP 抽象未开源，别等。
* **对 S3 全链路无意义**：EdgeOne 回源 H2 + loopback + 磁盘 + 云盘，每跳 TCP 友好，H3 换不到东西。
  H3 只在两种情况下出场：出海（UDP 正常）/ UDP 归零兜底（tunnel，见下）。

## 附录 A：UDP-over-TCP 插座（MASQUE fallback 候选，非定论）

定位：**兜底连通性，不是加速**——TCP-over-TCP 熔断（内外双重传 + HoL 回归）在降速不断流的网络里大概率不如原生 H2；
只在 UDP 归零环境有意义，且验收标准是“可用”不是“更快”。

* 现成参照：kiche `H3Adaptive`（TCP↔QUIC 自适应路由，同构度最高，先读它）；
  pasque（RFC9298 CONNECT-UDP / RFC9484 CONNECT-IP 最小端点，接线抄它）；
  WARP MASQUE 经 tokio-quiche（生产级 fallback 形态实证）。
* 原型位（如果做）：client 侧封装器 + pingora 侧 `ProxyHttp` decaps + 上游 UDP 重放；
  触发条件 = 探测 UDP 不通才切 tunnel（fallback 档，常开即负优化）。
* 决策门：01-metrics 的 H2 engaged 率先行——H2 跑满则本附录永久归档不做。

## 附录 B：curl/nginx 集成边界

* **curl**：quiche 后端 EXPERIMENTAL（ngtcp2 才是默认）；`--http3/--http3-only/--alt-svc` + Happy Eyeballs（~100ms 软/~200ms 硬）降级可抄。
* **nginx**：Cloudflare 旧补丁停在 1.16.x 历史线（`e434e42` 已删，issue #554 关）——**此路已死**；
  现状走上游 nginx 原生 QUIC（≥1.25，`quic_retry/ssl_early_data/quic_gso`），用的不是 quiche。
* **h3i**：低层 H3 debugger（可弯曲 RFC、录制/回放 qlog/netlog）+ 故障注入用例，一致性测试进 CI 用它。

## 生产坑

1. **apps 声明非生产**：`quiche-client/quiche-server` + examples 明确无性能/安全保证，GSO/`SO_TXTIME`/`send_quantum`/`max_pacing_rate` 自测。
2. **`H3Driver` 够用就别自定义**：自定义 `ApplicationOverQuic` 要自己管 `Router(DCID)→IoWorker→timeout` 全套（AGENTS.md 代码地图），抄错 timer 即吞吐掉零。
3. **`google/quiche` 同名不同物**：Chromium/Envoy 那套叫 QUICHE，选型/搜索必须区分（T2 血泪）。

## 相关篇

* 前 6 篇 quiche（本篇是地图，其他是手册）· `pingora-*` 8 篇（主攻）

## 版本与参考链接

* https://github.com/cloudflare/quiche/tree/master/tokio-quiche（README + AGENTS.md 代码地图）
* https://docs.rs/tokio-quiche/latest/tokio_quiche/trait.ApplicationOverQuic.html
* https://blog.cloudflare.com/async-quic-and-http-3-made-easy-tokio-quiche-is-now-open-source
