# quiche-resumption-migration — 0-RTT 会话恢复与连接迁移

> 一句话职责：建连加速（0-RTT）与网络切换不断连（migration）的全部规则。
> 前置：`quiche-conn-lifecycle.md`（连接位）。适用版本：`quiche 0.29.3`。

## 最小可用示例

```rust
fn resumption_config(ticket_key: &[u8]) -> quiche::Result<quiche::Config> {
    let mut config = quiche::Config::new(quiche::PROTOCOL_VERSION)?;
    // 0-RTT：开早数据 + 票据密钥（多机部署必须共享密钥，见坑 2）
    config.enable_early_data();
    config.set_ticket_key(ticket_key)?;
    Ok(config)
}

fn migrate(conn: &mut quiche::Connection, new_local: std::net::SocketAddr) -> quiche::Result<()> {
    // 主动迁移：先探路，validated 后再切（别裸 migrate）
    conn.probe_path(new_local, None)?;
    // 事件循环里排空 path 事件
    while let Some(evt) = conn.path_event_next() {
        match evt {
            (n, quiche::PathEvent::Validated) => {
                conn.migrate(new_local, None)?;
                let _ = n;
            }
            (_, quiche::PathEvent::Failed) => break, // 老路继续用
            _ => {}
        }
    }
    Ok(())
}
```

会话恢复对侧：`conn.set_session(ticket_bytes)`（client 存上次 `session()`）→
`is_resumed()` / `is_in_early_data()` 确认；`early_data_reason()` 查拒绝原因。

## 关键 API 表

| API | 语义 |
|---|---|
| `enable_early_data()` | 开 0-RTT；默认关（产品侧 Cloudflare dashboard 同样默认关） |
| `set_ticket_key(key)` / `set_session(bytes)` / `session()` | 票据密钥 / 存取会话票据；多机共享密钥需自轮换保前向安全 |
| `is_in_early_data()` / `is_resumed()` / `early_data_reason()` | 早数据中 / 已恢复 / 早数据拒绝原因 |
| `migrate(addr)` / `migrate_source(addr)` | 主动迁移 / 被动迁移（对端先动，本端跟） |
| `probe_path(addr)` / `path_event_next()` | 探路 / 排空路径事件（`New/Validated/Failed/Closed/ReusedSCID/PeerMigrated`） |
| `send_on_path/send_ack_eliciting_on_path/paths_iter/is_path_validated/path_stats` | 多 path 收发与状态 |
| `set_disable_active_migration` / `set_disable_dcid_reuse` | 关主动迁移 / 关 DCID 复用（安全收紧位） |

## 生产坑

1. **0-RTT 默认关是有原因的**：早数据可重放——只许幂等 GET，把 `Early-Data:1` 请求的写操作全拒了；
   产品侧默认关，客户端别自作主张开。
2. **ticket-key 轮换是你的活**：多机共享密钥不轮换 = 丢前向安全；轮换窗口内新旧双 key 并存，老票据宽限 epoches 后作废。
3. **迁移先探路**：裸 `migrate` 到黑洞地址等于自杀；`probe_path → Validated → migrate` 三步缺一不可；
   移动端 WiFi/蜂窝切换是迁移的主战场，桌面端基本用不上。
4. **0-RTT 与 CC 的交互**：早数据阶段拥塞窗口按新连接算，别指望复用上次的 cwnd（见 cc-tuning 篇 init cwnd）。
5. **0.29.3 安全项**：`ReusedSourceConnectionId` 限流（CVE-2026-12707 修）、FFI 连接 ID 迭代器 UAF（0.29.2 CVE-2026-11941）——
   用 FFI 的先升 0.29.3（见 build-ship 篇）。

## 可借鉴实现

* `quiche/examples` 的 session 复用示例；curl 的 0-RTT 开关形态对照。
* easyquic 默认开 0-RTT/会话恢复的参数包（抄默认，再按坑 1 收紧）。

## 相关篇

* `quiche-conn-lifecycle.md` · `quiche-cc-tuning.md`（cwnd）· `quiche-tokio-apps.md`（apps 实操）

## 版本与参考链接

* https://blog.cloudflare.com/even-faster-connection-establishment-with-quic-0-rtt-resumption
* https://github.com/cloudflare/quiche/issues/1648（migration 用法问答）
