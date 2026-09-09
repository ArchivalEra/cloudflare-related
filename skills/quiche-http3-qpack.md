# quiche-http3-qpack — HTTP/3 接入、QPACK 与 GOAWAY

> 一句话职责：QUIC 建连之后，把 H3 跑起来的全部步骤。
> 前置：`quiche-conn-lifecycle.md`（握手完成）。适用版本：`quiche 0.29.3`。

## 最小可用示例

```rust
fn http3_client(conn: &mut quiche::Connection) -> quiche::Result<()> {
    // 1. ALPN 必须在 Config 期配好：set_application_protos(h3::APPLICATION_PROTOCOL)
    let mut h3_config = quiche::h3::Config::new()?;
    let mut h3_conn = quiche::h3::Connection::with_transport(conn, &h3_config)?;

    // 2. 发请求
    let req = vec![
        quiche::h3::Header::new(b":method", b"GET"),
        quiche::h3::Header::new(b":scheme", b"https"),
        quiche::h3::Header::new(b":authority", b"quic.tech"),
        quiche::h3::Header::new(b":path", b"/"),
        quiche::h3::Header::new(b"user-agent", b"quiche"),
    ];
    let stream_id = h3_conn.send_request(conn, &req, true)?;

    // 3. 事件循环收响应
    loop {
        match h3_conn.poll(conn) {
            Ok((id, quiche::h3::Event::Headers { list, .. })) => println!("{id} headers: {list:?}"),
            Ok((id, quiche::h3::Event::Data)) => {
                let mut buf = [0u8; 65535];
                let n = h3_conn.recv_body(conn, id, &mut buf)?;
                println!("{id} data: {}", n);
            }
            Ok((_, quiche::h3::Event::Finished)) => break,
            Ok((_, quiche::h3::Event::GoAway)) => break, // 对端要走，戒掉重试执念
            Err(quiche::h3::Error::Done) => break,       // 读完，非错
            Err(e) => return Err(e.into()),
        }
    }
    let _ = stream_id;
    Ok(())
}
```

服务端回包：`send_response(conn, stream_id, &resp_headers, false)` + `send_body(conn, stream_id, bytes, fin)`。

## 关键 API 表

| API | 语义 |
|---|---|
| `h3::APPLICATION_PROTOCOL` | ALPN ID（`h3`），Config 期 `set_application_protos` 必配，否则 H3 建不起来 |
| `h3::Config::new()` + QPACK 参数 | QPACK 动态表/字段段/扩展 CONNECT/settings（见下） |
| `h3::Connection::with_transport(conn, cfg)` | 角色自推断（client/server 按连接类型） |
| `send_request / send_response / send_body / recv_body` | 请求/响应/体收发（`fin` 语义同 stream） |
| `poll(conn) -> Event` | `Headers / Data / Finished / Reset / GoAway / PriorityUpdate` 六事件 |
| `send_goaway(conn, id)` | 优雅告别：告诉对端别再开新流（滚动发布/缩容前必调） |

## 生产坑

1. **ALPN 忘配**：握手成功但 `with_transport` 起不来，第一检查项永远是 `set_application_protos`。
2. **`Error::Done` 不是错**：`poll` 返回 Done = 本轮读完，`break` 继续事件循环；当错处理会把正常连接掐了。
3. **GOAWAY 后别开新流**：收到 `GoAway` 还 `send_request` 会直接失败；正确姿势是换连接（LB failover 语义，见 pingora-lb 篇对照）。
4. **QPACK 队头阻塞是 H3 独有**：动态表不同步会卡住整连接解码，QPACK 调参（表大小/阻塞流上限）与带宽时延积一起算，别只调 QUIC 流控。
5. **优先级只是建议**：`PriorityUpdate` 发了对端也不保证执行；关键资源别赌优先级，用多连接或预加载。

## 可借鉴实现

* `quiche/examples/http3-{client,server}.rs`（最小闭环）。
* `h3i` 低层 H3 debugger：可弯曲 RFC、录制/回放 qlog，一致性测试与故障注入用它（见 tokio-apps 篇）。
* curl quiche 后端（EXPERIMENTAL）：C 侧调用范式对照。

## 相关篇

* `quiche-conn-lifecycle.md` · `quiche-streams-zero-copy.md`（H3 跑在流上）· `quiche-tokio-apps.md`（H3Driver 版）

## 版本与参考链接

* https://docs.rs/quiche/latest/quiche/h3/index.html
* https://github.com/cloudflare/quiche/blob/master/quiche/src/h3/mod.rs
