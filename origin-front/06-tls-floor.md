# 06-tls-floor — TLS 最低版本钉死（对应 front-requirements §2-5，优先级第 5）

> 状态：方案（对方实施；**结论可能是“维持现状”，结论即交付**）。前置：无，随时可做。
> 现状（对方回条 E）：pingora 0.8.1 + rustls 后端锁定（Cargo.lock 无 openssl-sys，有 rustls/aws-lc-rs），方案有效性确认。

## 1. 目标

rustls 默认（TLS 1.2+ intermediate 套件）今天够用；本篇只回答一个问题：0.8.1 的 rustls 后端有没有干净的 min-version knob？有则钉，无则维持现状等上游。

## 2. 改动点（先搜后定，已搜）

* **检索结论**：pingora 0.8.1 层面无干净的 rustls min-version knob 记录。
  openssl/boring 系经 `Deref → SslAcceptorBuilder::set_min_proto_version` 可调（见 tls-listeners 篇），但对方是 rustls 后端，此路不通；
  rustls 自身 `ServerConfig::builder_with_protocol_versions` 存在，但 pingora-rustls 0.8.x 是否透出该配置**未验证**（P6 遗留：rustls 动态 SNI 回调 #832 未合入是同类“透出不全”证据）；
  第三方 `synapse_pingora::TlsManager::with_tls12_minimum` 有 min_version 封装，可作参考实现，但引入第三方 TLS 封装要单独评估。
* **实施动作**（对方，二选一）：
  * A：查 `pingora-rustls 0.8.1` docs.rs 的 `ServerConfig`/builder 透出，有则包一层设 `TLSv1_2` floor，合入。
  * B：无透出 → 维持现状，关闭本项（rustls 默认 1.2+ 已够用，EdgeOne 回源只会更高不会更低）。

## 3. 取值决策表

| 项 | 取值 | 依据 |
|---|---|---|
| floor 目标 | TLS 1.2（如钉） | intermediate 兼容位，不动 1.3 |
| 默认结论 | B（维持现状）概率大 | 无干净 knob 不硬拗；硬拗（切 openssl/引第三方封装）成本远超收益 |

## 4. 回归风险

* 切 openssl 后端 = 引入 openssl-sys 构建依赖 + 行为差（P6 #703/#792/#627），为一个 floor 不值得，本篇明确不建议。
* 维持现状的风险≈0：回源对端是 EdgeOne，版本只高不低。

## 5. 对方验证命令

```bash
# 无论 A/B，先跑一遍现状基线：
openssl s_client -connect 127.0.0.1:7777 -tls1_1 </dev/null 2>&1 | grep -iE 'refused|alert|Cipher'  # 应拒绝
openssl s_client -connect 127.0.0.1:7777 -tls1_2 </dev/null 2>&1 | grep -i 'Cipher is'               # 应通过
# 若走 A：钉死后重跑 + cargo test 相关单测
```
