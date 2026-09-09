# pingora-tls-listeners — TLS 后端、监听器与连接过滤

> 一句话职责：下游“用什么 TLS 接、SNI 怎么选 cert、握手前怎么拦”的全部规则。
> 前置：`pingora-server-ops.md`（监听 fd 交接位）。适用版本：`pingora-core 0.8.1`。
> 来源：[#11 P4](https://github.com/ArchivalEra/cloudflare-related/issues/11) / [#13 P6](https://github.com/ArchivalEra/cloudflare-related/issues/13)。

## 最小可用示例

```rust
use pingora_core::listeners::Listeners;
use pingora_core::protocols::tls::TlsSettings;

fn main() -> pingora::Result<()> {
    // 中间兼容配置（mozilla intermediate v5）
    let tls = TlsSettings::intermediate("server.crt", "server.key")?;
    tls.enable_h2(); // ALPN = H2H1；或 set_alpn(H1/H2/Custom)
    let mut listeners = Listeners::new();
    listeners.add_tls("0.0.0.0:443", tls)?;

    // 握手前 IP 黑白名单（需 features = ["connection_filter"]，关闭零开销）
    listeners.set_connection_filter(std::sync::Arc::new(Allowlist));
    Ok(())
}

use pingora_core::listeners::connection_filter::ConnectionFilter;
#[derive(Debug)]
struct Allowlist;
#[async_trait::async_trait]
impl ConnectionFilter for Allowlist {
    async fn should_accept(&self, addr: Option<&std::net::SocketAddr>) -> bool {
        is_private_or_allowed(addr) // false 直接丢连接
    }
}
```

下游 mTLS（openssl 系）：经 `Deref → SslAcceptorBuilder` 调 `set_verify` + verify 回调；
rustls：`set_client_cert_verifier(Arc<dyn ClientCertVerifier>)`。

## 关键 API/配置表

**TLS 后端互斥**（`pingora-core` feature 二选一，默认 openssl）：

| 后端 | 握手构造 | SNI 动态选 cert | ALPN | 备注 |
|---|---|---|---|---|
| `openssl` | `TlsSettings::intermediate(crt, key)`；`Deref` 出 `SslAcceptorBuilder` 可调 `set_ca_file/set_verify` | `TlsAccept::certificate_callback` + `handshake_complete_callback` | `enable_h2()` = H2H1 / `set_alpn(...)` | 功能最全；支持 FIPS 走 boringssl |
| `boringssl` | 同 openssl | 同上 | 同上 | 同上 |
| `rustls` | `intermediate(crt, key)` → `with_single_cert` 静态 | **无** `certificate_callback`（issue #832 未合入）；动态 SNI 需自注 `ServerConfig` | `set_alpn` → wire protocols | 无回调，多域名场景吃力 |
| `s2n` | 同 intermediate；`s2n_config_cache_size`（默认 10，0 禁用，建 config 贵勿关） | 同 rustls | 同 rustls | 后量子/FIPS 场景 |

通用：`Listeners::tls / add_tls / add_tls_with_settings` 建端点；
`set_offload_threadpool_from_server_conf` 开下游握手 offload（须 ServerConf 双 `*_offload_threadpools` 同时 > 0）；
`PreTlsProcess` 可在握手前吃 PROXY protocol。
上游侧对等项在 `PeerOptions`（`verify_hostname / use_system_certs / alternative_cn / ca / psk / s2n_security_policy`，见 upstream 篇）。

**connection_filter**：accept 后、TLS 握手前执行，`false` 直接丢连接；适合 IP 黑白名单、私网段过滤；
实现必须高效（每次连接调用一次）。官方 `connection_filter.rs` example（`BlockAllFilter` + 挂载演示）可直接跑。

## 生产坑

1. **rustls ≠ openssl（P6 部分证实）**：`verify_cert/verify_hostname = false` 在 rustls 曾被忽略（#703→#716 已修）；
   per-peer CA（`peer.get_ca()`）rustls 忽略仅 openssl 支持（#792 open）；自签/V1 链 rustls 拒而 openssl 宽（#627）。
   多后端/自签场景必须双栈实测；`tls_min/max/cipher` 按 backend 分分支配，不跨 backend 复用代码。
   （`CA:TRUE leaf` 一条无 pingora 一级来源，判未证实，仅作 webpki 严格性推论。）
2. **证书更新无热加载**：证书文件无 inotify（#619 open），改 cert/key 必须走优雅升级；
   pingap 的 `--autoreload` 只热加载 upstream/location/plugin，改 listen 端口须全量优雅重启。
3. **rustls 多域名是坑**：无 SNI callback，多 cert 场景要么切 openssl/boring，要么自注 `ServerConfig`；
   选型时先数域名数量。
4. **`connection_filter` 放错位置**：它是 TCP 层、握手前，只能看到源 IP；要做 SNI/Host 维度的拦，去 `early_request_filter`。

## 可借鉴实现

- pingap：`validate_servers_tls`（配置校验期先验 TLS）+ `--autoreload` 热子集 + `-a/--autorestart` 残差重启。
  选证链：`exact → wildcard → default + ArcSwap 原子替换`；rustls 构建 `tls_*` 参数直接 rejected、恒为 1.2+1.3。
- pingclair：`tls auto`（LE HTTP-01 + 80 跳转）/ `tls internal`（10 年 CA + 90 天 leaf）/ DNS-01 仅 cloudflare；
  transport 策略变更拒 `409 restart_required` 保留 last-good。
  `auto_https on/disable_redirects/off` 对照表抄跳转策略。
- zentinel：`CertificateReloader::reload_all()` 热换 cert 内容；listener 集合变化 warn + 继续旧 listener（需 restart）。
  终止端 KDL 可抄：`listener https{tls{cert/key/min-version}} + sni{hostnames + cert/key}` + 上游 `tls{sni}`。
- aralez 分级（`README`）：`proxy_tls_grade high/medium/unsafe`（high ≈ SSL Labs A+）+ dummy 自签 bootstrap 首对——
  等级制 cipher 配置，新手抄 high 起步。
- `0xRichardH/pingora-gateway`：SNI callback 预载多证书 + `proxy_tls` 开关，抄多证书挂载形状。

## 相关篇

- `pingora-server-ops.md`（监听 fd 交接与 cert 更新走升级）· `pingora-upstream-peer.md`（上游 TLS 字段）

## 版本与参考链接

- https://github.com/cloudflare/pingora/blob/main/pingora-core/src/listeners/mod.rs
- https://github.com/cloudflare/pingora/blob/main/pingora-core/src/listeners/connection_filter.rs
- https://github.com/cloudflare/pingora/issues/619（cert 热加载） · https://github.com/cloudflare/pingora/issues/832（rustls SNI 回调）
- https://github.com/cloudflare/pingora/pull/716 · https://github.com/cloudflare/pingora/issues/792
