# 03-conn-limits — 连接限制（对应 front-requirements §2-2，优先级第 2）

> 状态：方案（对方实施）。前置：无，可与 00 并行。
> 现状（对方回条 D）：`Server::new(None)` 默认 conf，零 limit 配置；未出过事故；但 7777 公网可达 + 无限制是事实组合。

## 1. 目标

单 IP / 单连接吃光 front 之前先拦。分两层：**代码层只做 IP cap**，**直连先挡归运维层**（对方补充：Oracle 安全组/防火墙收 7777；代码里别把运维通路挡死）。

## 2. 改动点

* **先搜后定**：0.8.1 无 per-listener max-connections knob（已检索官方 user_guide/conf + ServerConf 全字段 + 社区，无此 knob；envoy 的 connection_limit_filter 是别家东西，pingora 没有对等物）。所以不造轮子：
* **IP cap**：`connection_filter`（需 `connection_filter` feature，关闭零开销）做 IP 允许/拒绝 + 运维通路放行；速率维度抄官方 `rate_limiter.md` + `examples/rate_limiter.rs` 范式（`request_filter` 内 `Rate::observe`，超限 429 + `X-Rate-Limit-*` 头 + `set_keepalive(None)` + 手写响应 + `Ok(true)`）。

```rust
// 伪形：allowlist（运维段）+ blocklist（腾讯回源段外全部？不——见下）
async fn should_accept(&self, addr: Option<&SocketAddr>) -> bool {
    match addr {
        Some(a) if OPS_NETS.contains(a.ip()) => true,  // 运维通路永不挡
        Some(a) if BLOCKED.contains(a.ip()) => false,  // 已确认的恶意段
        _ => true, // 其余放行，速率由 request_filter 的 Rate 兜底
    }
}
```

* **运维动作（非代码，checklist）**：Oracle 安全组收敛 7777 入站（回源段 + 运维段 + 基准测试源）；基准测试就是直连打的，收敛前先把测试源加白。

## 3. 取值决策表

| 项 | 取值 | 依据 |
|---|---|---|
| 代码层 | IP allow/block + Rate 兜底 | 0.8.1 无 max-conn knob，有现成的抄现成的 |
| 运维层 | 安全组收 7777 | 对方指定层，不进代码 |
| 运维通路 | 永不挡（allowlist 硬编码） | 对方原话 |
| 速率阈值 | 待 01 基线后定 | 先按回源峰值×2 起草，标待回填（现在无数，见 01） |

## 4. 回归风险

* `connection_filter` 在握手前执行，只看得到源 IP：SNI/Host 维度的拦不在此层（去 `early_request_filter`，本篇不做）。
* Rate 阈值拍脑袋会误杀回源：阈值必须等 01 的计数基线出来再定，本篇先给机制不给数。

## 5. 对方验证命令

```bash
# blocklist 加一个测试 IP，直连 curl 看连接被拒；运维段 IP 直连全程通过
# Rate 阈值设极低（如 1rps）压测，看 429 + X-Rate-Limit-* 头 + keepalive 已关（对官方 example 行为）
# 回归：回源流量 0 误杀（对 00 日志 status=429 计数）
```
