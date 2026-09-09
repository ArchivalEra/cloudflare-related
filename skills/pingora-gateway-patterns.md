# pingora-gateway-patterns — 生产网关模式蒸馏与插件契约

> 一句话职责：三家生产网关（pingap / pingsix / zentinel）蒸馏出的“网关该怎么写”。
> 前置：`pingora-proxy-lifecycle.md`（相位语义）。适用版本：pingora 0.8.x + 三家 2026-08 主干。
> 来源：[#5 T4](https://github.com/ArchivalEra/cloudflare-related/issues/5) / [#12 P5](https://github.com/ArchivalEra/cloudflare-related/issues/12)（行级文件指针已验证）。

## 三向对照

| 维度 | pingap `vicanso/pingap`（~1.4k★，首选运营模板） | pingsix `zhu327/pingsix`（69★，首选 filter 链模板） | zentinel（~105★，安全/隔离模板） |
|---|---|---|---|
| 插件模型 | `Plugin` trait + `PluginFactory` 按 category 创建；`PluginStep` 显式分步 | `ProxyPlugin` trait + `PluginEntry{plugin, phases}` + `ProxyPluginExecutor` 分区；`PLUGIN_META` 声明相位 | 无进程内 Plugin trait；`Filter + FilterPhase{Request,Both,Response}` + 外部 agent（UDS/gRPC） |
| ProxyHttp 映射 | `Server: ProxyHttp`（`pingap-proxy/src/server.rs`）：early→找 location+EarlyRequest 插件；request→admin/acme/metrics+标准插件；余类推 | `HttpService: ProxyHttp`（`src/service/http.rs`）：early→凭证+matcher+pipeline.early；request→404 无路由 else pipeline；upstream_peer→override>route>timeout>WS 宽松 | `ZentinelProxy: ProxyHttp`：early→计数+ACME 短路+listener/global 匹配；request→限流/MCP 体解析/agent；余类推 |
| 统一出口 | `RequestPluginResult::Skipped/Continue/Respond`，插件自造响应经 `send` 写回 | `FilterVerdict::Continue / Reject(Rejection)`，插件禁自写，`CompiledPipeline` 唯一经 `send_rejection` 写一次 + `exit-transformer` 统一生效 | `apply_request_filters → bool` 短路 + 各处直写；MCP/A2A 体解析优先 |
| 热重载 | `ConfigManager{ArcSwap<PingapConfig>}` + `diff_and_update_config`；热子集（upstream/location/plugin/cert）直换，残差（addr/TLS listener）转 `-a/--autorestart` 优雅重启 | `RuntimeSnapshot{revision,…}` + `RuntimeStore{ArcSwap}`：`validate→compile→publish`，增量调和探针（fingerprint + `Arc::ptr_eq` 复用），etcd revision 防回退 | `ConfigManager::reload`：`from_file→validate→store→post_hook→cert_reloader`；`GracefulReloadCoordinator` 排空；`SIGHUP` 信号 |
| 对照档 | aralez（轻量：`DashMap + ArcSwap` 双表 + filewatch 2s 防抖 + cert 自加载；**实测完全不用 `LoadBalancer::select`**，纯自研轮转，见 lb 篇） | — | pingclair（Caddyfile DX：`publish_config → 409 restart_required` 保留 last-good；LE/internal CA） |

## sbproxy（AI 网关深度定制，S3 深挖）

40 crates workspace（v1.14.0），唯一自维护 pingora fork（`sbproxy-0.8.0` 分支，`[patch.crates-io]` 覆盖 11 个 `pingora-*`，相对上游 merge-base + 自有 7 提交，`scripts/divergence.sh` 可刷新）。
fork 改动（已确认）：`TlsSettings::with_cert_resolver`（ACME 动态选 cert 免重启）、`upstream_response_decision` 状态码重试钩、
响应字节到达客户端后拒绝重试、listeners 预准备、runtime 线程栈 8MiB 可配。

* **五引擎接 ProxyHttp**：`request_filter` 内 15 步（身份 hook → 认证 → CEL/Rego/bundle 策略链 → 响应缓存查 → 17 种动作分发）；
  `response_body_filter` 是唯一 transform 附着点；Cedar 不进 ProxyHttp，走 `McpPolicyHook` 与 RBAC 并行。
  沙箱配额可抄：rquickjs（100ms 看门狗/16MB 堆/1MB 栈）、wasmtime（无 FS/网络、epoch 中断、16MiB）；
  **WASM ABI 版本放导出名**（wasmtime 读不到 global 初值，别学 OPA 式 global）。
* **语义缓存**（自研 `sbproxy-cache`，与 `pingora-cache` 无关）：LSH 只召回 + 精确余弦重排 + `WriteToken` 绑定本次 lookup；
  key `v2:<workspace>:<tenant>:<host>:<method>:<path>:<identity>:<query>:<vary>:<config_fp>`，
  `%/:` 转义防污染、vary 只能收窄、credential 分区——三招直接抄进 cache 篇键规范。
* **预算 fail-closed**：七域 + 独立时间窗桶 + `Block/Log/Downgrade` + `soft_landing`；
  未验证身份归 sentinel 共享桶（防刷额度，耗尽即拒，计费事件留审计）；keystore 错误永不缓存。
* **MCP digest 锁**：`tool-versions.lock.yaml`（Cargo.lock 式基线）+ `contract_digest`（RFC8785 canonical，v1/v2 永不碰撞）+
  三维兼容预言机（structural/behavioral/description）+ `contract_of` 单 owner（grep 测试防第二投影）。
* **审计 hash 链**：四通道四文件，`digest=SHA256(prev||seq||time||event)` + Ed25519 签名，seq 自 0 连续可重放验证；
  key 链不存值只存 HMAC 指纹。

## 可复制契约骨架（最小 Rust，抄 pingsix）
```rust
// 1. 相位 bitmask：hook 仅声明相位才执行
#[derive(Clone, Copy, PartialEq, Eq, Default)]
pub struct Phases(u8);
impl Phases {
    pub const EARLY: Self = Self(1 << 0);
    pub const REQUEST: Self = Self(1 << 1);
    pub const UP_REQ: Self = Self(1 << 2);
    pub const RESP: Self = Self(1 << 4);
    pub const LOG: Self = Self(1 << 6);
    pub const PEER: Self = Self(1 << 7);
}

// 2. 统一 Reject：插件禁自写响应，全部经单点写回
pub struct Rejection {
    pub status: http::StatusCode,
    pub body: Option<String>,
    pub headers: Vec<(String, String)>,
    pub close: bool,
}
pub enum Verdict {
    Continue,
    Reject(Rejection),
}

// 3. 插件 trait + 执行器：按 priority desc + name 排序分区；global 先，global Reject 跳过 route 层
#[async_trait::async_trait]
pub trait Plugin: Send + Sync {
    fn name(&self) -> &str;
    fn priority(&self) -> i32;
    async fn request(&self, s: &mut Session, c: &mut Ctx) -> anyhow::Result<Verdict> {
        Ok(Verdict::Continue)
    }
    // + early / up_req / resp / resp_body / log（默认空实现）
}

// 4. 单点写回：去 1xx/204/304 体、丢 framing 头、归一 Content-Type、套 exit-transformer、close 则 set_keepalive(None)
async fn send_rejection(s: &mut Session, r: &Rejection, c: &mut Ctx) -> anyhow::Result<()> {
    /* … */
}

// 5. 重载路径：validate → compile → ArcSwap publish → drain
async fn reload(store: &arc_swap::ArcSwap<Snapshot>, path: &str) -> anyhow::Result<()> {
    let next = Snapshot::load_validate(path)?; // 失败直接返回，旧快照继续服务
    if next.listeners_changed(&store.load()) {
        tracing::warn!("listener changed: restart required"); // zentinel/pingclair 模式
    }
    store.store(std::sync::Arc::new(next)); // 原子 swap，在途请求持旧 Arc
    Ok(())
}
```

## 热重载七实践（源码提炼）

1. **先验后换、失败保留**：坏配置永不 publish，在途请求持旧 `Arc`。
2. **单锁串行 publish**：`publish_lock` / `reload_mutex` 防并发半覆盖；写盘加锁防 admin/ACME lost-write。
3. **读侧全走 `ArcSwap`**：数据面禁读写锁临界代理（pingap/pingsix/zentinel/aralez 一致）。
4. **增量调和优于全重建**：探针按指纹复用，先起新后弃旧；留 `previous_config` 可回滚。
5. **listener/TLS 分级**：路由/upstream/插件/cert 内容可热换；`addr/端口/TLS 绑定/worker/mTLS 新开/transport 策略` 一律 `restart_required`（zentinel warn、pingclair 409、pingap 转 autorestart）。
6. **优雅排空**：计数器 inc/dec + 轮询 `wait_for_drain`；readiness 回告 + 超时放弃；发布窗拒新请求防半策略。
7. **可观测回执**：`ReloadEvent{Started/Validated/Applied/Failed}` + diff 通知/webhook + revision 状态；增删 API 空删/重复加必须报错非静默成功。

## 生产坑

1. **插件自写响应是万恶之源**：pingap 允插件自写，zentinel 各处直写——只有 pingsix 的单点 `send_rejection` 经住了推敲；
   新网关直接抄 pingsix，禁插件自写。
2. **缓存短路放错相位污染 LB 指标**：命中短路放 `proxy_upstream` 前（pingsix/zentinel 一致），放错会污染 LB 健康统计。
3. **body 插件忘判 `was_upgraded()`**：WS 升级后 body 语义变化，pingap 有 #114 保护，抄时别丢。
4. **MCP/A2A 按 body 鉴权**：zentinel 教训——头只能校验一致性，策略放 body 解析后；但 body 解析须配上限防 zip bomb（100x/10MB）。

## 行级文件指针（raw 已验证）

- pingap：`pingap-core/src/plugin.rs`（PluginStep）· `pingap-proxy/src/server.rs`（ProxyHttp 映射）·
  `pingap-config/src/manager.rs`（ArcSwap）· `src/process/auto_restart.rs`（diff_and_update）·
  `pingap-plugin/README.md`（五步生命周期 + 插件索引表）· `docs/modules.md`（模块依赖 mermaid 图）。
- pingsix：`src/core/plugin/mod.rs`（Verdict/Rejection）· `src/core/plugin/pipeline.rs`（Executor）·
  `src/service/http.rs`（HttpService）· `src/proxy/runtime.rs`（Snapshot publish）·
  `USER_GUIDE.md`（17 章全指南）· `config.yaml`（全注释范例：listeners/etcd/admin/routes/upstreams/services/global_rules）。
- zentinel：`crates/proxy/src/reload/mod.rs`（reload 管线）· `reload/coordinator.rs`（排空）·
  `crates/proxy/src/proxy/http_trait.rs`（ProxyHttp）· `crates/proxy/src/tls.rs`（cert 热换）·
  `crates/proxy/docs/{architecture,deployment,rate-limiting,examples}.md`。
- aralez：`aralez.rs/docs/{config,api}`（provider 拆分 + 远程推送 JSON 形）。
- pingclair：`docs/GUARDRAILS.md`（四分册护栏索引）。

## 文档级可抄（S1 搜刮合入）

- pingap 插件矩阵（`pingap-plugin/README.md`）：鉴权/访问/流量/内容/运维五类 20+，`plugins=[...]` 按列序执行——抄插件分类法。
- pingsix APISIX 兼容（`config.yaml`）：`PUT /apisix/admin/routes/1 + X-API-KEY` + `data_encryption{keyring 轮换}`——抄 Admin API 形状。
- zhu327《从 Pingora 到 API 网关》：APISIX 式 Router/Upstream YAML + discovery/lb 拆分 + matchit 路由思路。
- 基准方法（只述方法不引数字）：aralez `oha + 3 echo 后端 + 300 并发 10 分钟 + glibc/musl 对照`；
  pingclair `c7i-flex + wrk/h2load + H1/H2/H1S 六格表`；跨仓数字不可比（见 P6 #13）。

## 相关篇

- `pingora-proxy-lifecycle.md`（相位语义）· `pingora-cache.md`（网关缓存）· `pingora-server-ops.md`（热重载与发布）

## 版本与参考链接

- https://github.com/vicanso/pingap · https://github.com/zhu327/pingsix · https://github.com/zentinelproxy/zentinel
- https://github.com/sadoyan/aralez · https://github.com/dorianverlaine/pingclair
- raw 直读例：`https://raw.githubusercontent.com/vicanso/pingap/main/pingap-core/src/plugin.rs`
