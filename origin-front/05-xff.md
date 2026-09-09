# 05-xff — 客户端 IP 传递（对应 front-requirements §2-4，优先级第 4）

> 状态：方案（对方实施）。前置：**00-access-log 落地**（XFF 现状不可测是无日志所致，先有日志再终判）。
> 现状（对方回条 B）：未实测，拿不到；回源段位无现成数据（腾讯控制台拿，标 TODO）；默认方案已同意：覆盖式 + 仅信任回源段。

## 1. 目标

业务面（及日志）能看到真实客户端 IP，为未来 per-IP 限流与日志取证铺路。现在业务面只看到 front/loopback 对端。

## 2. 改动点

* `upstream_request_filter`（P1 全序 #9：发上游前最后改头位）覆盖式写入：

```rust
async fn upstream_request_filter(
    &self, session: &mut Session, upstream_request: &mut RequestHeader,
    _ctx: &mut Ctx,
) -> pingora::Result<()> {
    if TRUSTED_ORIGIN_NETS.iter().any(|n| n.contains(&downstream_ip(session))) {
        let client_ip = downstream_ip(session).to_string();
        upstream_request.insert_header("X-Forwarded-For", client_ip)?;
    }
    // 非信任段直连：不写（03 已在安全组/connection_filter 层处理），不伪造
    Ok(())
}
```

* `TRUSTED_ORIGIN_NETS`：TODO，管理员从腾讯控制台拿段位后填；填之前代码合入但 nets 为空 = 不写头（安全默认：不信任任何人，而不是信任所有人）。
* 与 03 联动：直连流量由安全组 + `connection_filter` 先挡，本篇只处理“已放行流量怎么写头”。

## 3. 取值决策表

| 项 | 取值 | 依据 |
|---|---|---|
| 写入方式 | 覆盖 | EdgeOne 是否自带 XFF 未知，覆盖最安全；等 00 数据再终判（若 EdgeOne 带且可信，改追加） |
| 信任模型 | 仅回源段 | 非信任段不写头、不伪造 |
| 段位空窗期 | 默认不写 | 安全默认 |

## 4. 回归风险

* 覆盖式会丢 EdgeOne 自带的 XFF（如果它带的话）：00 日志先看一周，确认行为后再终判覆盖/追加，本篇合入时保持覆盖。
* 业务面日志字段要同步加 XFF 列（业务面改动，不在本篇代码范围内，列为联动项）。

## 5. 对方验证命令

```bash
# 回源段请求：查业务面日志拿到 XFF == 真实客户端 IP
# 直连请求：无 XFF 头（不伪造）；查 00 日志 xff 字段验证两种流量行为
# 段位填入后：回测覆盖/追加终判，更新本篇
```
