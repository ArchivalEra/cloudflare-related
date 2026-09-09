# pingora-cache — HTTP 缓存状态机与隔离层

> 一句话职责：缓存“何时查、何时写、key 怎么定才不投毒”的全部规则。
> 前置：`pingora-proxy-lifecycle.md`（缓存准入组位）。适用版本：`pingora-cache 0.8.1`。
> 警告：官方定性 proxy–cache 集成 **experimental，API 高度易变**；本篇含 `CacheManager` 隔离层写法。
> 来源：[#10 P3](https://github.com/ArchivalEra/cloudflare-related/issues/10)。

## 最小可用示例（社区 issue #392 范式）

```rust
use pingora_cache::{CacheKey, HttpCache, MemCache};

// 后端预建 'static（Lazy<CacheBucket> 式），业务只配 key 规则与 TTL
static STORAGE: std::sync::LazyLock<MemCache> = std::sync::LazyLock::new(MemCache::new);

async fn request_cache_filter(&self, session: &mut Session, _c: &mut Ctx) -> pingora::Result<()> {
    session.cache.enable(
        &*STORAGE,         // storage
        None,              // eviction（默认）
        None,              // predictor
        Some(&CACHE_LOCK), // 防击穿锁（CacheLock::new(1s)）
        None,              // overrides
    );
    Ok(())
}

fn cache_key_callback(&self, session: &Session, _c: &mut Ctx) -> pingora::Result<CacheKey> {
    // 必含 host + scheme + method；永远不要回退到“仅 path”
    let mut k = CacheKey::new(host_authority(session).into());
    k.user_tag.push(scheme_method_tag(session).into());
    Ok(k)
}

fn response_cache_filter(
    &self,
    _s: &Session,
    resp: &pingora_http::ResponseHeader,
    _c: &mut Ctx,
) -> pingora::Result<RespCacheable> {
    if resp.status.as_u16() == 200 {
        Ok(RespCacheable::Cacheable(meta_60s()))
    } else {
        Ok(RespCacheable::Uncacheable("non-200".into()))
    }
}
```

## 状态机（`CachePhase` 11 态）与代理调用序

`Disabled(NoCacheReason)` / `Uninit` / `Bypass` / `CacheKey` / `Hit` / `Miss` /
`Stale` / `StaleUpdating` / `Expired` / `Revalidated` / `RevalidatedNoCache`。

1. `request_cache_filter`：唯一能 `enable(storage, eviction?, predictor?, lock?, overrides?)` 的地方；默认空实现 = 全不缓存。
2. `cache_key_callback` → `set_cache_key` → `cache_lookup()`（仅 CacheKey 期可 `bypass()`，错期 panic）。
3. 命中 → `cache_found(meta, hit_handler, hit_status)` → `cache_hit_filter`（可返 `ForcedFreshness` 强制失效，也跑在 stale 体上）→ `Hit/Stale`；
   未命中 → `cache_miss()` → `Miss`。
4. 回源后 `response_cache_filter` → `RespCacheable` → `set_cache_meta` → `set_miss_handler` → 写 `miss_handler()` →
   `finish_miss_handler()`（触发 eviction；drop 不调视为 admission 未完成需清理）。
5. 头过滤双层（为缓存集成而分，仍 WIP）：`upstream_response_filter`（入库前，改动被缓存；命中不触发）vs
   `response_filter`（出库后，命中也触发）。另 `range_header_filter`（默认单 range）、`cache_vary_filter`、
   `cache_not_modified_filter`（ETag/304）、`should_serve_stale`、`is_purge` + `purge()`。

## 三必覆写清单（默认 = 不缓存 / 直接 panic）

| 方法 | 默认行为 | 不重写的后果 |
|---|---|---|
| `request_cache_filter` | 空实现 | 全程 `Disabled`，缓存静默不工作 |
| `cache_key_callback` | `unimplemented!` | 启用缓存即 **panic**（0.8.0 故意为之，见投毒案） |
| `response_cache_filter` | `Uncacheable(Custom("default"))` | 永远不入库 |

另：`cache_vary_filter` 默认 `None`（vary 关）；`is_purge` 默认 `false`。
防御性检查可用 `phase()` / `maybe_cache_key()` / `enabled()` / `bypassing()`；`disable(reason)` 附 `NoCacheReason` 可观测。

## 生产坑

1. **默认 key 投毒案（GHSA-f93w / CVE-2026-2836）**：`≤ 0.7.0` 默认 key 只含 URI path、不含 host，跨租户投毒；
   `0.8.0` 删除默认实现改为 panic。key 至少含 `host/:authority + 上游 TLS scheme + method`，
   再按需叠 Vary、重写后 origin host、用户 tag。官方原话：“无放之四海的默认 key，错了就是 poisoning”。
2. **错期调用即 panic**：`bypass()` 仅 CacheKey 期；`set_cache_key / cache_found / purge` 等均有期检。`set_cache_lock` 须在 key/hit 期前调完。
3. **stale 默认关**：`should_serve_stale` 默认仅上游错误才可 stale；开 stale 须按 RFC9111§4.2.4 / RFC5861§4 显式声明，
   MUST NOT 无依据造 stale。
4. **无锁则惊群**：不配 `CacheLock` 时同 asset 并发全部打向上游；`MemoryCache`/`RTCache`（tinyufo TinyLFU + S3-FIFO）自带防护，
   但 `enable` 时仍建议显式配锁（`CacheLock::new(1s)`）。
5. **pingsix breaking 教训**：`cache` 插件重命名为 `proxy-cache` 无别名，静态配置每个 `cache:` 键必须改写，
   否则单个 stale key 导致整个 config snapshot 构建失败——网关层必须自包 `CacheManager` 隔离官方 breaking。
6. **volatile 面**：main 分支出现 `proxy_cache::range_filter` 引用但 workspace 无该成员，疑为 0.8 后 range 抽 crate 重构；
   版本钉死前勿直接依赖该路径。

## CacheManager 隔离层（最小骨架）

```rust
pub struct CacheManager {
    storage: &'static dyn Storage,
    eviction: Option<&'static dyn EvictionManager>,
    lock: Option<&'static CacheKeyLockImpl>,
}
// ProxyHttp 侧：request_cache_filter → mgr.enable；cache_key_callback → mgr.key；
// response_cache_filter 返回 Cacheable{meta鲜期} / Uncacheable(reason)；
// purge 路由先 is_purge 鉴权再 HttpCache::purge。
```

要点：后端全部预建 `&'static`；`finish_miss / finish_hit` 配对调；`max_file_size` 三件套截大体。

## tinyufo 机制（U1，map #14：pinned 0.8.1）

* **TinyLFU 准入**：Count-Min Sketch（`u8` 饱和计数，满窗 `window_limit = size×8` 后全表 `>>1` 衰减），无 doorkeeper；
  **只在 `put` 驱逐时用，`get` 只读不加频**（省去 window-cache 抗突发）。
  准入流程：新项频率 vs 被驱逐项频率，低则拒绝（被驱逐者塞回）；`force_put` 跳过比较（命中率代价 zipf 下 ≤0.5pp）。
* **S3-FIFO 驱逐**：`small`（上限 total×10%）+ `main` 双 `SegQueue`，全 lock-free（flurry/skiplist + 原子量）；
  元数据 `uses` 上限 3，small 扫到热项晋升 main。
* **双后端**：`Fast`（flurry HashMap，快但占内存）/ `Compact`（分片 skiplist，省内存，`get` 慢常数因子）；
  key 只存 `ahash` 后的 `u64`，不存原 key。
* **MemoryCache 接线**：外再包一次 `ahash` 做 `K→u64` 二次哈希，`weight` 恒 1；
  零 TTL 拒绝插入；`RTCache` 读穿透用 `Semaphore(0)` 合并同 key 并发（只一回调源，余者等 `add_permits` 后 `LockHit`），写回统一 `force_put`。
* **pingap 对照**：未用 `MemoryCache/RTCache`，自家 `TinyUfoCache + FileCache`（内存命中 → 文件 → 回填；`put` 双写，大对象截断不进内存）。

## redb 互补位（单立小节）

redb 当 `Storage` 持久后端 + tinyufo 当准入热前端，二者正交：
请求 → 查内存 tinyufo → 命中返回；未命中 → 查 redb → 命中视热度回填；未命中 → 回源 →
经 TinyLFU 判决：高频同时 `put`（内存按页计 weight + redb 全量），低频只放 redb 或丢弃。
`FileCache` 即此模式的文件版先例（内存 `TinyUFO` + 磁盘文件 + `cache_file_max_weight` 截大对象），
把文件读写换成 redb 事务读写即可，配额回收（`max_size/atime/clear`）另行做——驱逐回传的 `Vec<KV>` 只管内存层。

## 可借鉴实现

- pingsix `proxy-cache`：TTL + opt-in PURGE + conditions（29 插件之一，APISIX 风格）。
- aralez：自研无依赖内存缓存（`cache_size_mb` + `cache_ttl`，未用 `pingora-cache`）。
  两行缓存语义：“honors Cache-Control，no-store/private 不缓存；仅 GET 200”（抄默认策略）。
- zentinel：memory/disk/hybrid 三后端宣称——但 issue #90 实锤 disk 未实现（解析接受 `backend=disk`，永远初始化内存 store），引用时只认 memory。
  缓存 KDL 可抄：`default-ttl 300 + stale-while-revalidate 60 + stale-if-error 300 + vary[Accept, Encoding]`。
- pingap 双后端对照（`pingap-cache/README.md`）：内存 TinyUFO vs 文件（`inactive/reading_max/cache_max/levels/max_size`），
  `PURGE /*` 仅文件后端支持——需要 purge 端点的场景直接选文件后端。
- 社区唯一端到端实现：`Object905` gist（`CacheBucket + 四钩子` + 落盘/压缩/LRU）+ issue #392 讨论串；API 易变，抄时锁版本。
- sbproxy 自研缓存（S3 深挖，与 `pingora-cache` 无关）：key `v2:<workspace>:<tenant>:<host>:<method>:<path>:<identity>:<query>:<vary>:<config_fp>`，
  三招直接抄——`%/:` 转义防 `/victim:foo` 污染、vary 只能收窄（放宽即投毒面）、credential 分区；
  JCS canonical 后再哈希（消客户端键序抖动）、域分隔 `FieldDigest`（防拼接别名）、配置指纹进 key 尾（滚动不串味）；
  语义缓存侧：LSH 只召回 + 精确余弦重排 + `WriteToken` 绑定本次 lookup，坏记录永不误命中。

## 相关篇

- `pingora-proxy-lifecycle.md`（缓存钩子在全序中的位置）· `pingora-gateway-patterns.md`（网关缓存配置形状）

## 版本与参考链接

- https://docs.rs/pingora-cache/0.8.1/pingora_cache/ · https://docs.rs/pingora-memory-cache/0.8.1/pingora_memory_cache/
- GHSA-f93w-pcj3-rggc（默认 key 投毒）：https://github.com/advisories/GHSA-f93w-pcj3-rggc
- 最小接线示例：https://github.com/cloudflare/pingora/issues/392
