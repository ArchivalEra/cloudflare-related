# 00-access-log — one-line 访问日志（对应 front-requirements §3-1）

> 状态：方案（对方实施）。前置：tracing 已接好（对方回条 C），本篇纯加法。

## 1. 目标

front 的请求从“隐形”变“可见”：每请求一行（method、path、status、bytes、duration、h1/h2），走现有 `tracing` 栈。顺带解决 05-xff 的“XFF 现状不可测”问题——日志落地后即可实测回填。

## 2. 改动点（抄现成的）

* **`logging()` 必经终点写一行**（P1 全序 #18：成功失败都到，无遗漏）+ **`request_summary()` 定制错误摘要**（phase.md：`fail_to_proxy` 自动打 error log 时调它 dump 上下文）。
* 字段从 `Session` 取：`req_header().method/uri`、响应 status、上下游字节数、CTX 里记 `Instant::now()`（`new_ctx` 打点，`logging` 相减得 duration）、proto（h1/h2）。
* 预留 `xff` 字段位（05 落地后填，不提前编造）：

```rust
async fn logging(&self, session: &mut Session, e: Option<&pingora::Error>, ctx: &mut Ctx) {
    tracing::info!(
        method = %session.req_header().method,
        path = %session.req_header().uri.path(),
        status = status_of(session),
        bytes = bytes_of(session),
        duration_ms = ctx.start.elapsed().as_millis(),
        proto = proto_of(session), // "h2" / "h1"
        xff = %session.req_header().headers.get("x-forwarded-for")
            .map(|v| v.to_str().unwrap_or("-")).unwrap_or("-"),
        err = ?e.map(|e| e.to_string()),
        "front access",
    );
}
```

* 日志量：EdgeOne 回源量有界，直连 7777 的基准流量另算；如需采样/分级，后续在 03 限流落地后再加（本篇不做）。

## 3. 取值决策表

| 项 | 取值 | 依据 |
|---|---|---|
| 打点位 | `logging()` | 必经终点；错误摘要走 `request_summary()` |
| duration 起点 | `new_ctx` 的 `Instant` | per-request CTX，P1 范式 |
| xff 字段 | 先占位，05 回填 | 现状不可测，不编数据 |

## 4. 回归风险

* 高频小请求下 info 级日志放量：回源流量有界可接受；若直连压测打满，临时调 `RUST_LOG`（`EnvFilter` 现成）。
* `to_str().unwrap_or("-")`：非 ASCII 头不炸，只记 `-`。

## 5. 对方验证命令

```bash
RUST_LOG=info ./origin-front 2>&1 | grep 'front access' | head -5
# 打一发回源请求，确认一行含 method/path/status/duration_ms/proto；再 grep xff 字段看 EdgeOne 到底带不带 XFF（回填 05）
```
