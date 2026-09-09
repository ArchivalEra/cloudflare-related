# pingora-lb-failover — 负载均衡、服务发现、健康检查与故障转移

> 一句话职责：流量“分给谁、谁死了怎么摘、失败怎么换”的全部机制。
> 前置：`pingora-upstream-peer.md`（peer 构造）。适用版本：`pingora-load-balancing 0.8.1`。
> 来源：[#4 T3](https://github.com/ArchivalEra/cloudflare-related/issues/4) / [#9 P2](https://github.com/ArchivalEra/cloudflare-related/issues/9)（算法名以 `selection/` 源码原名为准）。

## 最小可用示例

```rust
use pingora_load_balancing::health_check::TcpHealthCheck;
use pingora_load_balancing::selection::RoundRobin;
use pingora_load_balancing::{Backend, Backends, LoadBalancer};

fn main() -> pingora::Result<()> {
    // 静态后端 + TCP 健康检查 + 1s 后台任务
    let _upstreams = Backends::try_from_iter(["1.1.1.1:443", "1.0.0.1:443"])?;
    let mut lb = LoadBalancer::<RoundRobin>::try_from_iter(["1.1.1.1:443", "1.0.0.1:443"])?;
    lb.set_health_check(TcpHealthCheck::new());
    lb.update_frequency = Some(std::time::Duration::from_secs(1));
    let _bg = pingora_core::services::background::background_service("health check", lb);
    // server.add_service(bg); 选址：bg.task().select(b"", 256)
    Ok(())
}
```

`upstream_peer` 内选址 + 建 peer（`examples/load_balancer.rs` 范式）：

```rust
async fn upstream_peer(&self, _s: &mut Session, _c: &mut Ctx) -> pingora::Result<Box<HttpPeer>> {
    let upstream = self.lb.select(b"", 256).unwrap();
    Ok(Box::new(HttpPeer::new(
        upstream,
        true,
        "example.com".into(),
    )))
}
```

## 关键 API/配置表

**selection 变体**（`selection/mod.rs + weighted.rs + algorithms.rs + consistent.rs` 原名）：

| 源码名 | 实义 |
|---|---|
| `FNVHash = Weighted<FnvHasher>`（另有隐藏兼容别名 `FVNHash`） | 加权 hash；首选按权重展开表，后续 fallback 均匀 hash |
| `Random = Weighted<algorithms::Random>` | 首选加权随机，后续随机 |
| `RoundRobin = Weighted<algorithms::RoundRobin>` | key 无关递增；首选加权表，后续轮转 |
| `Consistent = consistent::KetamaHashing` | ketama 环；`KetamaConfig{point_multiple}`，`v2` 特性切 `Version::V2`；仅 `Inet`，UDS 被过滤 |
| `Weighted<H> / WeightedIterator / OwnedNodeIterator / UniqueIterator` | 通用加权容器/迭代器；`build` 断言后端 ≤ 65536，权重按复制索引展开 |

trait 链：`BackendSelection::build/iter` → `BackendIter::next` → `SelectionAlgorithm::next(key)`（所有 `Hasher + Default` 自动实现）。
`select(key, max_iter)` / `select_with(key, max_iter, accept)` 返回按优排序候选，调用方结合健康继续迭代。

**discovery**：`ServiceDiscovery::discover() -> (BTreeSet<Backend>, HashMap<u64, bool>)`，enablement key 为 `hash_key`，缺席 = 启用。
pingora 自带仅 `Static`（源码注 `TODO: DNS`）。`Backends::update` 失败直接返回、**不换 selector = 保留旧池**；
成员不变仅更新 enable flag，成员变则 generation+1 重建 selector。`HealthRegistry` 按等价键去重探针目标，多 view 共享。

**健康检查**：trait `HealthCheck{check, health_threshold, health_status_change, backend_summary}`；
`Health` 经 `ArcSwap` 原子翻转，连续达阈值才翻转，同态清零。

| 类型 | 关键参数/默认 |
|---|---|
| `TcpHealthCheck` | `consecutive_success/failure` 默认 1/1；模板 `BasicPeer("0.0.0.0:1" + conn 1s)`；`check` 仅建 TCP/TLS 流 |
| `HttpHealthCheck<C>` | 阈值默认 1/1；模板 `HttpPeer("0.0.0.0:1" + conn 1s + read 1s)`，`reuse_connection = false`；`req = GET / + Host`，无 validator 时仅 200 算过；支持 `port_override`、body 限流、总超时 |

后台模式：`LoadBalancer: BackgroundService`，`update_frequency/health_check_frequency = None` = 仅跑一次；
共享注册表由 `HealthCheckService` 统一探（未配 check 则 fail-closed）。

**failover**：pingora 不自动重试。靠 `select` 迭代 + `fail_to_connect` / `error_while_proxy` 置 `e.set_retry(true)` +
CTX `tries` 计数切 peer（`docs failover.md` 模板）；响应头已发出后只能记错。

## 生产坑

1. **核心无 slow-start**：源码无 `slow_start` 符号；刚恢复的后端会瞬间吃满，只有 pingclair 自实现恢复时间点。新后端上线先小权重（ketama 点数/权重表）再调大。
2. **DNS 失败保旧是特性不是 bug**：`update()` 错误早返保留旧 selector；pingclair 把 `Ok([])` 也视失败保旧。更新失败只打日志，别当场清空。
3. **默认阈值 1/1 太敏感**：pingora 默认一次失败即摘除，抖动网络下频繁摘挂；pingap 默认 1/2，生产建议失败阈值 ≥ 2。
4. **健康检查与业务池隔离**：探针 `HttpPeer` 必须 `idle_timeout = 0` + 关 verify（自签场景），否则探针连接污染业务复用池。
5. **`select` 的 `max_iter` 别太小**：后端多、坏的多时迭代次数不够会无可选 peer；示例用 256，大集群按规模调。
   深层原因（H2 R6，map #14）：`UniqueIterator` 去重但**重复也耗 `steps`**——Ketama 环连续命中同一后端多虚节点时步数照扣，
   `max_iter` 过小会把有健康节点的迭代提前截断成 `None`。H2 下此与流饱和无关，是 LB 层截断，别误调 `max_h2_streams`。
6. **UDS 进不了 Ketama**：`KetamaHashing::build_with_config` 只收 `SocketAddr::Inet`（0.8.1 `consistent.rs:45` 的 FIXME 原文：
   `ketama only supports Inet addr, UDS addrs are ignored here`），UDS 后端无声缺席；
   H2 over UDS 配 `Consistent` 等于没后端，切 `Weighted/FNV/RR`（H2 R7，map #14）。

## 可借鉴实现

- pingap：`SelectionLb::{RoundRobin / Consistent(ip/path/query/url/header/cookie) / Transparent}` + 健康 DSL
  （`tcp/http/https/grpc/ws/wss://…?conn/read/freq/success/failure/reuse`，默认 3s/3s/10s/1/2）+ 全坏通知。
  key 推导（S4 已验源码）：`algo` 格式 `"hash:<type>:<key>"`（如 `hash:cookie:session_id`），
  `HashStrategy::get_value` 按类型取 `Url（完整 uri）/Ip（含 XFF 回写复用）/Header/Cookie/Query/Path（默认，仅 path）`；
  调站点 `new_http_peer` 对 Consistent 传 `value.as_bytes()` 进 `select_with(…, 4, accept)`，RR 传 `b""`，Transparent 直接 Host 直连。
- aralez：**实测完全不用 pingora `LoadBalancer::select`**（全仓 35 个 rs grep 零命中，`lb` feature 仅开未用）——
  自研 `DashMap<host<path<(Vec<InnerMap>, AtomicUsize)>>>` + `fetch_add % len` 轮转 + `backend_id` cookie 粘滞
  （`response_filter` 写 `Set-Cookie: backend_id=<sha256[..50]>`）+ path 最长前缀回退。
  极简运维：`hc_method: HEAD` + `hc_interval: 2`，`GET /status?live` 直接看各后端 alive（抄状态端点形状）。
- pingclair：自研 LB + `DynamicDialPlan`（60s/512 条拨号缓存，支持 `unix://, h2c://`）+ 逐后端修正 SNI/Host（修官方模板只换地址的坑）+ 慢启动。
  三件套：`retry{max_attempts 含初次，仅幂等+无 body 才重试}` + `overload{max_in_flight/pending}` + `circuit_breaker{per-backend}`。
- pingsix 主被动检查（`USER_GUIDE.md`）：active（http/https/tcp）+ passive（429/500/503 计数），共享执行器 register/unregister/abort。
- zentinel：`P2C（默认）/ least-tokens / consistent / adaptive` 健康感知 + 熔断 FSM `closed → open → half-open`。
- 官方 `failover.md` 标准件：`CTX{tries}` + `fail_to_connect` 内 `e.set_retry(true)` + “响应头已发不可救 / GET 可重试 POST 慎重”检查表。

## 现成单测清单（H2，map #14：改分片逻辑先跑这些）

- `selection::consistent::test::test_ketama`：固定映射 + 删节点最小扰动（删 b3 仅两 key 漂移）。
- `selection::weighted::test::{test_fnv, test_round_robin, test_random}`：权重首选 + fallback 序列 + 10k 权重比。
- `selection::tests::{unique_iter_max_iterations_is_correct, unique_iter_duplicate_items_are_filtered}`：截断与去重语义。
- `test::{test_static_backends, test_backends, test_discovery_readiness, test_parallel_health_check}`：装载/ready/enablement/并行一致性。
- 提案（未进官方）：`test_ketama_evenness`（千 key 均衡 ±85%、删节点 85% 不动）、`test_ketama_failover_removes_unhealthy`（摘除后永不返回坏点）、
  `test_unique_iter_repeats_consume_steps`（固化 R6）、`reuse_hash` 分池断言（R1）、探针隔离断言（R10）。

## 相关篇

- `pingora-upstream-peer.md`（peer 构造）· `pingora-pool-timeout.md`（超时配多大）· `pingora-server-ops.md`（后台任务挂载与发布）

## 版本与参考链接

- https://docs.rs/pingora-load-balancing/latest/pingora_load_balancing/
- examples：`load_balancer.rs` / `multi_lb.rs`
- 未验证：`selection/discovery` 部分变体参数细节（需读源码锁定）；aralez 轮转权重精确语义。
