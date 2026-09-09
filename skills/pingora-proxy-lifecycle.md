# pingora-proxy-lifecycle — ProxyHttp 过滤器全生命周期

> 一句话职责：`ProxyHttp` 全部回调的触发顺序、短路/重试语义，一篇查全。
> 前置：无。适用版本：`pingora-proxy 0.8.1`（2026-08-17）+ main HEAD（2026-08）。
> 来源：wayfinder [#4 T3](https://github.com/ArchivalEra/cloudflare-related/issues/4) / [#8 P1](https://github.com/ArchivalEra/cloudflare-related/issues/8)。

## 最小可用示例

```rust
use async_trait::async_trait;
use pingora_core::upstreams::peer::HttpPeer;
use pingora_proxy::{ProxyHttp, Session};
use std::sync::Arc;

pub struct Ctx { pub retries: usize }
pub struct LB(Arc<str>); // 上游地址，示例 1.1.1.1:443

#[async_trait]
impl ProxyHttp for LB {
    type CTX = Ctx;
    fn new_ctx(&self) -> Ctx { Ctx { retries: 0 } }

    async fn upstream_peer(&self, _s: &mut Session, _c: &mut Ctx)
        -> pingora::Result<Box<HttpPeer>>
    {
        Ok(Box::new(HttpPeer::new("1.1.1.1:443", true, "example.com".into())))
    }

    async fn upstream_request_filter(
        &self, _s: &mut Session, upstream_request: &mut pingora_http::RequestHeader,
        _c: &mut Ctx,
    ) -> pingora::Result<()> {
        upstream_request.insert_header("Host", "example.com")?;
        Ok(())
    }
}
// 挂载：let mut svc = pingora_proxy::http_proxy_service(&conf, LB(addr));
// svc.add_tcp("0.0.0.0:8080"); server.add_service(svc);
```

限流短路范式（`examples/rate_limiter.rs`）：在 `request_filter` 里判超限 →
`session.write_response_header(Box::new(resp), true)` 手写 429 → 返回 `Ok(true)` 直接跳 `logging`。

重试退避范式（`examples/backoff_retry.rs`）：`fail_to_connect` 里 `ctx.retries += 1` +
`e.set_retry(true)` + 指数 sleep → 回到 `upstream_peer` 换 peer。

## 全序表（phase # | 方法 | 时机 | 返回/短路）

| # | 方法 | 时机 | 返回/短路 |
|---|---|---|---|
| 0 | `init_downstream_modules` | 服务启动前，非每请求 | 无返回；默认加 disabled 压缩 builder |
| 0 | `init_upstream_modules`（需 feature） | 启动前注册上游 module，`order()` 大者先跑 | 无返回 |
| 1 | `new_ctx` **必需** | 每请求最先，建 per-request 状态 | 返回 `CTX`，供 `upstream_peer` 等选址用 |
| 1b | `on_connection_reuse` / `persist_connection_context` | 仅 H1 keepalive 复用：前者在 `early_*` 前注入上次状态，后者在 `logging` 后保存 | 默认空 |
| 2 | `early_request_filter` | 首 filter，下游 module 之前 | 只能 `Err` 终止（无 bool 短路）；要控 module 行为才放此 |
| 3 | `allow_spawning_subrequest` | `early_*` 后检查 | `false` 禁子请求（默认）；后台缓存再验证不受限 |
| 4 | `request_filter` | 校验/限流/鉴权主点 | `Ok(true)`=已手写响应→跳 `logging`；`Ok(false)`=继续；`Err`→`fail_to_proxy` |
| 5 | 缓存准入组（详见 `pingora-cache.md`） | `request_filter` 后、`proxy_upstream` 前 | `cache_key_callback` 默认 panic，启用缓存必须重写 |
| 6 | `proxy_upstream_filter` | 缓存 miss 后、选 peer 前；把限流/鉴权延迟到“真需回源” | `Ok(false)`=已手写响应即结束（须自管 keepalive/body 排空，否则默认 502）；`Err`→`fail_to_proxy` |
| 7 | `upstream_peer` **必需** | 选址+连接方式；重试循环每次都重进 | `Ok(peer)` 拨号；`Err`→`fail_to_proxy` |
| 8a | `connected_to_upstream` | TCP/TLS/复用连接成功后 | 仅日志/指标；`Err`→`fail_to_proxy` |
| 8b | `fail_to_connect` | 建连失败 counterpart | 默认原样返不重试；`e.set_retry(true)` 则回 #7 换 peer |
| 9 | `upstream_request_filter` | 发上游前最后改上游头（Host 重写典型） | peer 自动头策略先应用；H1 无 CL/TE 且有 body 时补 `TE: chunked` |
| 10 | `request_body_filter`（async，每片一次） | 发上游双工中，body 非全量 | 改 `Option<Bytes>` 即改转发（`clear()` 丢弃）；可节流/WAF offload |
| 12 | `adjust_upstream_modules`（feature 门控） | 上游头到达、上游 module 之前；1xx 可调多次 | 只读头，改头用 `upstream_response_filter` |
| 13 | `upstream_response_filter` | 收上游头后，**缓存前** | 改动会被缓存；缓存命中不触发（仅 304 重验触发并 merge） |
| 14 | `upstream_response_body_filter`（N×）/ `upstream_response_trailer_filter`（1×） | 上游 body/trailer，缓存前 | body 同 #10；节流返 `Some(duration)` |
| 15 | `response_filter` / `response_body_filter`（N×）/ `response_trailer_filter`（1×） | 发下游前，**缓存后**，命中也触发 | `modify_response.rs` 范式：缓冲转格式、去 CL 加 Chunked |
| 16 | `error_while_proxy` | 建连后代理中错误；`Send/Recv` 失败与 response 系 `Err` 都经此 | 默认 `more_context(peer)` + 按 `client_reused && !truncated` 自动判复用重试；可改写判幂等 |
| 17 | `fail_to_proxy`（async） | 任意 fatal 终点；默认按类型映射（Upstream 502 / Internal 500 / Downstream 400 / `HTTPStatus(c)` 透传） | 自写错误页后返码 + `can_reuse_downstream`（今日仅 peer 选择前错误有效，默认 false 最安全） |
| 18 | `logging`（async） | 必经终点，资源释放前 | 只做指标/access log，已有 error log 不在此打错误 |
| ⊥ | `suppress_error_log` / `suppress_proxy_warn_log`（实验）/ `request_summary` | 正交：压 `fail_to_proxy` 终错误 log / 压可重试上游失败 warn / 返回请求摘要串 | 实现须廉价；压掉 retry warn 会丢逐跳审计，需自补 metrics |

## 关键 API/配置表

- `Session`：下游/上游连接与头读写（`req_header` / `set_keepalive` / `write_response_header` / `respond_error`）。
- `CTX`：`new_ctx` 的 per-request 用户状态，几乎全 filter `&mut`，async 系要求 `Send + Sync`。
- `FailToProxy { error_code: u16, can_reuse_downstream: bool }`：`fail_to_proxy` 返回。
- WS 升级边：隧道/upgrade 后 body/trailer filter 行为不同（详见 P6 坑 #946）；另有 `#[doc(hidden)]` 自定义转发通道疑似未验证，不展开。

## 生产坑

1. **短路必须先手写响应**：两处 bool 短路都要先 `write_response_header`，否则下游挂起或 502。
2. **`proxy_upstream_filter(false)` 后自清现场**：keepalive 置空 + 排空 body，否则连接污染。
3. **重试只在两个环**：`fail_to_connect --retry--> upstream_peer`（须 `set_retry(true)`）；
   `error_while_proxy --retry--> upstream_peer`（默认自动判，幂等+缓冲完整才重用）。响应头已发出后只能记错不能重试。
4. **`upstream_response` vs `response` 改错层**：想进缓存的改动放前者，想每次（含命中）生效的放后者；放反会导致缓存投毒或改写丢失。

## 可借鉴实现

- 官方 examples：`load_balancer.rs`（最小模板）、`ctx.rs`（CTX→选址）、`rate_limiter.rs`（429 短路）、
  `modify_response.rs`（响应改写）、`backoff_retry.rs`（重试退避）、`gateway.rs`（path 路由 + `respond_error_with_body(403)` 鉴权 + prometheus 边车）。
- 官方限流范式（`user_guide/rate_limiter.md`）：`request_filter` 内判超限 → 429 + `X-Rate-Limit-*` 头 + 手写响应 → `Ok(true)`。
- 社区契约：`zhu327/pingsix` 的 `priority/phase/FilterVerdict` + 单点 `send_rejection`（详见 `pingora-gateway-patterns.md`）。

## 社区对照（S1 搜刮合入）

| 来源 | 相位划分 | 可抄点 |
|---|---|---|
| pingap 五步（`pingap-plugin/README.md`） | `early_request / request / proxy_upstream / upstream_response / response`，“runs at exactly one request step” | 五步表可直接当插件文档目录 |
| pingap 回调映射（`pingap-proxy/README.md`） | `early_request_filter → request_filter → proxy_upstream_filter → upstream_peer → response_filter → logging` | 回调→步骤映射表，与本篇全序表互校 |
| zentinel 16 步（`architecture.md`） | `TLS → Trace → Route → RateLimit → GeoIP → Agent(headers/body) → LB → Breaker → Shadow → Cache → Log` | 生产网关完整流水线，写网关 skill 时照此查漏 |
| pingsix 两层（`USER_GUIDE.md`） | 全局按 priority 降序先行，“global short-circuit prevents every route plugin incl. auth” | 全局短路语义：全局层 Reject 跳过路由层全部插件 |
| `InfiniteConsult/pingora-guide`（40+ 课 + docker lab） | 转发/TLS/路由/健康检查/限流（36–38）/鉴权（41–42）/缓存（43–47）/可观测（48） | 课程序列可当学习路径；单课代码偏教学简化，别当生产实现 |
| `vicanso/pingora-demo`（pingap 作者实战笔记） | 自定义错误保持 keepalive / `session.digest()` 计时 / H2C 开关 / TLS1.1 降级 | 6 个官方文档没有的坑，排障时先查此仓 |

## 相关篇

- `pingora-upstream-peer.md`（`upstream_peer` 回调的 peer 构造）· `pingora-cache.md`（#5 缓存准入组详述）· `pingora-gateway-patterns.md`（插件契约怎么写）

## 版本与参考链接

- trait 签名：https://docs.rs/pingora-proxy/latest/pingora_proxy/trait.ProxyHttp.html
- 阶段图：https://github.com/cloudflare/pingora/blob/main/docs/user_guide/phase.md
- examples：https://github.com/cloudflare/pingora/tree/main/pingora-proxy/examples
- 未完全验证：`upstream_response_body_filter` 节流调度的具体语义、`#[doc(hidden)]` 转发通道。
