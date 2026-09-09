# pingora-upstream-peer — HttpPeer 与 PeerOptions 全字段

> 一句话职责：上游“拨谁、怎么拨、连接能否复用”的全部定义，一篇查全。
> 前置：`pingora-proxy-lifecycle.md`（`upstream_peer` 回调位）。适用版本：`pingora-core 0.8.1`。
> 来源：[#4 T3](https://github.com/ArchivalEra/cloudflare-related/issues/4) / [#9 P2](https://github.com/ArchivalEra/cloudflare-related/issues/9)（以 `peer.rs` 源码为准，`pooling.md` 只列子集）。

## 最小可用示例

```rust
use pingora_core::upstreams::peer::{HttpPeer, PeerOptions};

fn main() {
    // 基础：IP + 是否 TLS + SNI
    let _peer = HttpPeer::new("1.1.1.1:443", true, "example.com".into());

    // 调优：连接超时 3s、空闲复用 60s
    let mut opts = PeerOptions::new();
    opts.connection_timeout = Some(std::time::Duration::from_secs(3));
    opts.idle_timeout = Some(std::time::Duration::from_secs(60));
    let mut peer = HttpPeer::new("1.1.1.1:443", true, "example.com".into());
    peer.options = opts;

    // mTLS / UDS / CONNECT 代理（cert_key 与 headers 按需传入）
    let _mtls = HttpPeer::new_mtls("10.0.0.1:443", "internal".into(), cert_key);
    let _uds = HttpPeer::new_uds("/run/app.sock", false, "".into());
}
```

## HttpPeer 构造子

| 构造 | 语义 |
|---|---|
| `new(addr, tls, sni)` | 取解析首个 IP；`scheme` 由 tls 定；`group_key = 0` |
| `new_uds(path, tls, sni)` | Unix 路径 → `SocketAddr::Unix`，仅 unix |
| `new_proxy(next_hop, ip, port, tls, sni, headers)` | CONNECT 代理：`_address` 为最终 IP 而非 next_hop，`next_hop` 现仅 UDS |
| `new_mtls(addr, sni, Arc<CertKey>)` | `new(tls=true)` + 挂客户端证书 |
| `is_tls()` | `scheme == HTTPS` |

## PeerOptions 全字段（`new()` 默认：`verify_cert/hostname = true`，`alpn = H1`，`max_h2_streams = 1`，`second_keyshare = true`，余 None/false/空）

| 字段 | 语义 |
|---|---|
| `bind_to` | 本地源地址绑定 |
| `connection_timeout` | TCP `connect()` 单次超时；健康检查模板常用 1s |
| `total_connection_timeout` | 建连总体超时（含 TLS 握手） |
| `read_timeout` / `write_timeout` | 每次 read/write 超时，读到即重置；HTTP 探针读头前设置 |
| `idle_timeout` | 池中空闲保活；`= 0` 即禁用复用（pingap 探针强制 0 防污染） |
| `verify_cert` / `verify_hostname` | 验链 / 验 CN==SNI；探针常关；aralez 对自签上游直接关 |
| `use_system_certs`（s2n） | 加载系统信任库，影响性能 |
| `alternative_cn` | CN 与 SNI 不符时的第二可接受名 |
| `alpn` | `set_http_version(max, min)` 换算；H1 / H2 / H2H1 |
| `ca` | 私有根覆盖系统库；**不进复用 hash**，多 CA 必须自加 `group_key` 隔离 |
| `tcp_keepalive` / `tcp_recv_buf` / `dscp` / `tcp_fast_open` | L4 调优 |
| `h2_ping_interval` | H2 保活 ping 间隔 |
| `max_h2_streams` | 单连接并发流，默认 1（pingap 可配 100 做多路复用）；**进 hash**（改值即分池，见 map #14 H2 规则 R1） |
| `h2_stream/connection_window_size` | H2 流/连接级窗口；**main-only，0.8.1 无此二字段**（0.8.1 握手窗口硬编码 `H2_WINDOW_SIZE=1<<23`，见 map #14 H2 规则 R2） |
| `allow_h1_response_invalid_content_length` | 单个非法 CL 按 close-delimited 容错；重复/冲突 CL 仍硬错；unstable |
| `http_upstream_request_policy` | 默认剥 hop-by-hop + `Connection` 提名字段 + 仅转发 WS 升级；`preserve()` / `deny_upgrades()` 为兼容预设 |
| `curves` / `second_keyshare` | TLS 曲线/二轮 keyshare；**main 进 hash，0.8.1 不在 hash 内**（切曲线 0.8.1 不分池，见 map #14 H2 规则 R3） |
| `upstream_tcp_sock_tweak_hook` | 建连前调 `TcpSocket` 的钩子 |
| `tracer` / `custom_l4` | 连接追踪 / 自定义 L4 拨号器 |
| `psk` / `s2n_security_policy` / `max_blinding_delay` / `upstream_tls_handshake_complete_hook` | 特性门控 TLS 扩展 |

## 复用判等（`Hash for HttpPeer` + `pooling.md`，以源码为准）

复用键含（0.8.1）：`_address, scheme, proxy, sni, client_cert, verify_cert/hostname, alternative_cn, psk, group_key, max_h2_streams`（`curves / second_keyshare` 仅 main 进 hash，见上表）。
`BasicPeer` 仅 hash 地址。请求中出错的连接标不可复用。代理场景比 `next_hop` 否则比 `address`。

## 生产坑

1. **多 CA 不隔离必串池**：`ca` 不进 hash，不同租户不同根必须配不同 `group_key`（pingap 即此做法），否则 A 租户复用 B 租户 CA 建的连接。
2. **`idle_timeout = 0` 才是禁用**：`None` 是用默认而非禁用；探针 peer 必须显式 0，否则探针连接污染业务池。
3. **流控窗口改了不分池**：`h2_stream/connection_window_size` 排除出 hash，不同窗口需求混用同一池会互相钳制，必要时用 `group_key` 分开。
4. **探针关 verify 别抄到业务**：`verify_cert/hostname = false` 只属于健康检查模板和自签调试，业务 peer 保持默认 true。

## 可借鉴实现

- pingap `pingap-upstream`：`UpstreamConf → PeerOptions` 全量映射 + `ca_key = hash(ca)` 隔离 + `max_h2_streams` 可配。
  发现三源 `Static/DNS/Docker`；私有 ca 替换系统 trust 时“pooled connections are keyed by bundle”（直抄 group_key 隔离）。
- aralez：`is_http2 → ALPN::H2`，ssl 上游双 verify 关（曾测 keepalive/recv_buf，高负载降级弃用）。
  上游 TLS 自动探测、自签静默接受 + `DEFAULT` 兜底（零配置开箱，照抄需评估安全口径）。
- pingclair：按 `Scheme` 设 TLS/ALPN/H2c/Unix peer，SNI 取 `HostName` 或显式值。
- pingsix 节点双形（`config.yaml`）：map（weight）/ list（host/port/weight/priority i8），`pass_host: pass/rewrite/node` 控制上游 Host 策略。
- zentinel：target 权重 + `connection-pool{max/idle/timeout}` + `discovery kubernetes{namespace/service/port}`。
- `0xRichardH/pingora-gateway`：SNI callback 预载多证书 + Host 路由 + filter 链 + `proxy_tls` 开关，最像生产网关的最小结构。

## Host/SNI 双设陷阱（S1 搜刮合入）

HTTPS 上游必须同时设对两处：`HttpPeer::new(addr, tls=true, sni)` 的 SNI（TLS 握手用）+
`upstream_request_filter` 里重写 `Host`（HTTP 层用）。只设其一的典型症状：SNI 对但上游报 404/421（Host 还是 LB 地址），
或 Host 对但握手报证书错（SNI 透的是 IP）。`0xRichardH` 与官方 LB 示例均显式做双设，照抄。

## 相关篇

- `pingora-lb-failover.md`（选址与健康检查）· `pingora-pool-timeout.md`（复用判等续篇）· `pingora-tls-listeners.md`（上游 TLS 对应下游侧）

## 版本与参考链接

- https://docs.rs/pingora-core/latest/pingora_core/upstreams/peer/struct.HttpPeer.html
- https://github.com/cloudflare/pingora/blob/main/docs/user_guide/peer.md
- https://github.com/cloudflare/pingora/blob/main/docs/user_guide/pooling.md
