# quiche-conn-lifecycle — 连接建立与事件循环最小闭环

> 一句话职责：`Config → connect/accept → recv/send/timeout` 事件循环跑起来的最小闭环。
> 前置：无。适用版本：`quiche 0.29.3`（Rust ≥1.88）。
> 来源：T1 quiche 官方全景（map #1）+ S1 复核（0.29.3 现行，无漂移）。

## 最小可用示例

```rust
use std::net::UdpSocket;

fn main() -> Result<(), Box<dyn std::error::Error>> {
    let mut config = quiche::Config::new(quiche::PROTOCOL_VERSION)?;
    // 流控初始全 0，必须显式设（零默认值陷阱，见下）
    config.set_initial_max_data(10_000_000);
    config.set_initial_max_stream_data_bidi_local(1_000_000);
    config.set_initial_max_stream_data_bidi_remote(1_000_000);
    config.set_initial_max_streams_bidi(100);
    config.set_initial_max_streams_uni(100);
    config.set_application_protos(quiche::h3::APPLICATION_PROTOCOL)?;
    config.verify_peer(false); // 客户端自测用；生产必须验

    let socket = UdpSocket::bind("127.0.0.1:0")?;
    socket.connect("127.0.0.1:4433")?;
    let mut conn = quiche::connect(None, &mut quiche::tests::Pipe::new(), &mut config)?;
    let mut buf = [0u8; 65535];
    // 事件循环：recv → 处理 → send → timeout（quiche 是纯状态机，socket+timer 归 App）
    loop {
        let n = socket.recv(&mut buf)?;
        let recv_info = quiche::RecvInfo { from: socket.peer_addr()?, to: socket.local_addr()? };
        conn.recv(&mut buf[..n], recv_info)?;
        let mut out = [0u8; 65535];
        while let Ok((n, send_info)) = conn.send(&mut out) {
            socket.send_to(&out[..n], send_info.to)?;
        }
        if conn.is_closed() { break; }
        if let Some(t) = conn.timeout() {
            std::thread::sleep(t.saturating_sub(std::time::Instant::now()));
            conn.on_timeout();
        }
    }
    Ok(())
}
```

服务端对侧：`quiche::accept(scid, odcid, local, &mut config)` + `quiche::retry(...)`（Retry 防放大）+
`negotiate_version()`（版本协商）；SCID 由 App 生成、可跨连接共享 `Config`。

## 关键 API 表

| API | 语义 |
|---|---|
| `Config::new(PROTOCOL_VERSION)` | 建配置；`PROTOCOL_VERSION = 0x1`（QUIC v1 定版） |
| `connect(server_name, scid, config)` / `accept(...)` / `accept_with_retry(...)` | 客户端建连 / 服务端建连（含 Retry 版） |
| `retry(scid, dcid, ...)` / `negotiate_version(...)` | Retry 包 / 版本协商包 |
| `recv(&mut buf, RecvInfo{from, to})` / `send(&mut out) -> (n, SendInfo{to, at})` | 收发；`send` 返回空即无包可发 |
| `timeout() -> Option<Instant>` / `on_timeout()` | 定时器：App 负责 sleep 到点调回 |
| `is_closed()` / `is_established()` / `is_in_early_data()` / `is_resumed()` | 连接状态机位 |
| `stats()` / `path_stats()` / `peer_transport_params()` / `trace_id` | 可观测：rtt/cwnd/对端参数 |

## 生产坑

1. **零默认值陷阱**：`initial_max_data/streams/stream_data` 全 0，不设即死连接（能建连但任何流都打不开）。`ConnectionParams → Config → h3::Config` 级联一次配齐。
2. **`Config` 可共享、`Connection` 不可**：配置对象多连接复用；每个连接独立状态机 + 独立 UDP 四元组。
3. **App 欠 timer 的债**：`timeout()` 不调 `on_timeout()` 会导致丢包检测/拥塞窗口冻结，症状是“建连后吞吐掉零”——先查 timer 循环。
4. **`send` 要抽干**：一次 `recv` 后循环 `send` 到 `Done` 为止，否则 GSO/pacing 批量包发不全（见 cc-tuning 篇 `send_quantum`）。

## 可借鉴实现

* `quiche/examples/{client,server,http3-client,http3-server}.rs`（C 版需 libev+uthash，仅作对照）。
* `tokio-quiche` 的 `IoWorker(recv→app→send)` 即本循环的异步版（见 tokio-apps 篇，别手搓）。
* Android DoH3：最大存量部署，DoT/DoH/DoH3 回退链可抄（见 T2）。

## 相关篇

* `quiche-streams-zero-copy.md`（流收发）· `quiche-http3-qpack.md`（h3 接入）· `quiche-tokio-apps.md`（别手搓事件循环）

## 版本与参考链接

* https://docs.rs/quiche/latest/quiche/struct.Config.html
* https://docs.rs/quiche/latest/quiche/struct.Connection.html
* https://github.com/cloudflare/quiche/tree/master/quiche/examples
