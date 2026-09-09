# quiche-streams-zero-copy — 流收发、背压与零拷贝

> 一句话职责：QUIC stream 的读写、优先级、关闭与零拷贝收发。
> 前置：`quiche-conn-lifecycle.md`（连接已建立）。适用版本：`quiche 0.29.3`。

## 最小可用示例

```rust
fn echo_stream(conn: &mut quiche::Connection) -> quiche::Result<()> {
    // 可读流迭代
    for stream_id in conn.readable() {
        let mut buf = [0u8; 65535];
        while let Ok((n, fin)) = conn.stream_recv(stream_id, &mut buf) {
            // 背压：可写才发
            if conn.stream_writable(stream_id, 0)? {
                conn.stream_send(stream_id, &buf[..n], fin)?;
            }
        }
    }
    // 优先级（urgency 0 最急） + 半关闭写端
    conn.stream_priority(4, 0, false)?;
    conn.stream_shutdown(4, quiche::Shutdown::Write, 0)?;
    Ok(())
}
```

零拷贝收发（`BufFactory/BufSplit`）：

```rust
// 发送侧零拷贝：stream_send_zc 避免一次内存复制，大包/高频场景用
// 接收侧：stream_recv_buf 配合自定义 BufFactory 做 buffer 池
```

## 关键 API 表

| API | 语义 |
|---|---|
| `stream_send/recv/_buf/_zc/_discard` | 发送/接收/buffer 版/零拷贝版/丢弃版 |
| `readable()` / `writable()` | 可读/可写流迭代器（writable 需传待发长度做背压判断） |
| `stream_priority(id, urgency, incremental)` | 紧急度 + 增量 flag（H3 优先级映射基础） |
| `stream_shutdown(id, Shutdown::Read/Write, err)` | 半关闭；双向流可只关一端 |
| `stream_capacity(id)` / `stream_finished(id)` / `stream_closed(id, app)` | 可发窗口/对端 fin/关闭状态查询 |
| `BufFactory` / `BufSplit` | 自定义 buffer 工厂 + 零拷贝拆分 |

## 生产坑

1. **writable 传 0 等于没问**：`stream_writable(id, 0)` 只问“可写否”；要发 N 字节就传 N，让流控窗口做真正的背压判断，否则大包半截卡住。
2. **fin 只发一次**：`stream_send(..., fin=true)` 后再发即错；半关闭用 `stream_shutdown(Write)`，别用 fin 重复关。
3. **零拷贝的池子归你管**：`BufFactory` 只是接口，buffer 池的分配/回收/上限全是 App 的活；池漏了就是内存漏，先给池加 metrics。
4. **接收不读=背压上游**：`readable` 出来了不 `stream_recv`，流控窗口不释放，对端越发越慢——症状像“带宽不足”，实为应用没读。

## 可借鉴实现

* `tokio-quiche` 的 `H3Driver` body 流转接（见 tokio-apps 篇）。
* monoio-quiche 的 `AsyncReadRent/WriteRent` 所有权式零拷贝（非 Tokio 路线对照，见 T2）。

## 相关篇

* `quiche-conn-lifecycle.md` · `quiche-http3-qpack.md`（流上跑 H3）· `quiche-cc-tuning.md`（流控窗口调优）

## 版本与参考链接

* https://docs.rs/quiche/latest/quiche/struct.Connection.html（`stream_*` 方法组）
* https://github.com/cloudflare/quiche/blob/master/quiche/src/stream.rs
