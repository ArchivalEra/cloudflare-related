# 04-prewarm-body-bound — prewarm 包体上限（对应 front-requirements §2-3，优先级第 3）

> 状态：方案（对方实施）。前置：无。
> 现状：`POST /_internal/prewarm/*key` 到 front 无界；token 鉴权在业务面；对象路由 GET/HEAD only，请求面唯一 body 就是这个 tiny prewarm POST。

## 1. 目标

front 层先卡体大小：超大 body 到不了业务面（鉴权之前省资源）。鉴权仍归业务面，front 只管大小。

## 2. 改动点

* `request_body_filter`（P1 全序 #10：逐片到达，双工中）：累计字节数放 CTX，超 cap 直接 `Err` 进 `fail_to_proxy`（→ 413 由 `fail_to_proxy` 错误页统一出，或手写 413 + `Ok(true)` 短路，二选一，推荐后者省一次错误映射）。
* header 数上限：0.8.1 无现成 per-request header-count knob（已检索，无对等物；如 pingora-http 有头大小限制 API，实施时以 0.8.1 docs.rs 为准，**本方案不编造 API 名**，标实施时核查）。

```rust
const PREWARM_MAX_BODY: usize = 64 * 1024; // 64KB：tiny POST 的百倍余量

async fn request_body_filter(
    &self, session: &mut Session, body: &mut Option<Bytes>,
    end_of_stream: bool, ctx: &mut Ctx,
) -> pingora::Result<()> {
    if !session.req_header().uri.path().starts_with("/_internal/prewarm/") {
        return Ok(()); // 非 prewarm 路由不管（对象路由本无 body）
    }
    if let Some(b) = body { ctx.body_bytes += b.len(); }
    if ctx.body_bytes > PREWARM_MAX_BODY || !end_of_stream && ctx.body_bytes > PREWARM_MAX_BODY {
        let h = ResponseHeader::build(413, None)?;
        session.write_response_header(Box::new(h), true).await?;
        session.set_keepalive(None);
        // 调用方约定：写完 413 后返回 Err 或由 request_filter 前置短路（见下）
    }
    Ok(())
}
```

* 更干净的替代位（推荐实施时二选一）：`request_filter` 里先看 `content-length`，超 cap 直接 413 短路（连 body 都不读）；`request_body_filter` 只防 chunked 无长度谎报。两者都写进方案，实施时按代码现状挑。

## 3. 取值决策表

| 项 | 取值 | 依据 |
|---|---|---|
| cap | 64KB | tiny POST 百倍余量；误杀概率≈0 |
| 生效路由 | 仅 `/_internal/prewarm/*` | 对象路由无 body，不扩大打击面 |
| header 数 | 实施时核查 0.8.1 API，不编造 | 已检索，无现成 knob 记录 |

## 4. 回归风险

* chunked 无长度 body：靠 `request_body_filter` 累计兜底，不依赖 content-length。
* 413 后 keepalive 必须关（参官方 rate_limiter 429 范式），否则污染复用连接。

## 5. 对方验证命令

```bash
# 正常 prewarm POST 通过；64KB+1 字节 POST 得 413；chunked 分片超限同样 413
# 回归：GET/HEAD 对象路由全程无影响（对 00 日志 status 分布）
```
