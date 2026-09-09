# pingora-server-ops — 服务配置、优雅升级与可观测

> 一句话职责：进程“怎么起、怎么热更、挂了怎么看”的运维全链。
> 前置：无（建议先读 `pingora-proxy-lifecycle.md` 理解 service 挂载）。适用版本：`pingora-core 0.8.1`。
> 来源：[#11 P4](https://github.com/ArchivalEra/cloudflare-related/issues/11)。

## 最小可用示例

```rust
use pingora_core::server::Server;
use pingora_core::server::configuration::Opt;
use clap::Parser;

fn main() {
    let opt = Opt::parse();
    let mut server = Server::new(Some(opt)).unwrap();
    server.bootstrap();
    // service 挂载
    let mut svc = pingora_proxy::http_proxy_service(&server.configuration, lb);
    svc.add_tcp("0.0.0.0:8080");
    server.add_service(svc);
    // 健康检查后台任务
    server.add_service(pingora_core::services::background::background_service("hc", lb));
    server.run_forever();
}
```

`conf.yaml` 最小运维模板：

```yaml
version: 1
threads: 4
pid_file: /run/pingora.pid
error_log: /var/log/pingora.log
upgrade_sock: /tmp/pingora_upgrade.sock
daemon: true
```

systemd（`Type=forking + PIDFile`）：

```ini
[Service]
Type=forking
PIDFile=/run/pingora.pid
ExecStart=/bin/pingora -d -c /etc/pingora.conf
ExecReload=kill -QUIT $MAINPID
ExecReload=/bin/pingora -u -d -c /etc/pingora.conf
```

注意官方示例写两行 `ExecReload`，systemd 以最后一行为准；建议配套 `daemon_wait_for_ready`
或拆成单行 `sh -c 'kill -QUIT $MAINPID; exec /bin/pingora -u -d -c …'`（未验证官方本意，生产先测）。

## 关键配置表（`ServerConf`，YAML，`#[serde(default)]`，未知键忽略）

| 键 | 默认 | 说明 |
|---|---|---|
| `threads` | `1` | **每个 service 独享**，非共享 |
| `listener_tasks_per_fd` | `1` | 每 fd 并行 accept 任务数 |
| `work_stealing` | `true` | 同 service 线程间偷取；`false` 时 alt timer 被忽略 + warn |
| `upgrade_sock` | `/tmp/pingora_upgrade.sock` | 新老进程必须一致的 fd 交接路径 |
| `grace_period_seconds` | `300` | SIGTERM/升级后最终关闭前的优雅期 |
| `graceful_shutdown_timeout_seconds` | `5` | runtime `shutdown_timeout` 上限 |
| `upstream_keepalive_pool_size` | `128` | 每 worker 空闲上游连接数，总上限 × threads |
| `max_retries` | `16` | `e.retry() == true` 时代理重试 fail-safe 上限 |
| `ca_file` | 各 TLS 库默认 trust store | 上游校验用根 CA |
| `user` / `group` | 无 | daemon 化后降权再接流量；可先加载 secret 再降权 |
| `upstream_debug_ssl_keylog` | `false` | 开后按 `SSLKEYLOG` 环境变量写 keylog 供 Wireshark；unstable |
| `fast_timeout_to_tokio_threshold_seconds` | `900`（`null` 禁用） | 超阈值改走 Tokio 原生 timeout |
| `daemon_wait_for_ready` / `daemon_ready_timeout_seconds` / `daemon_notify_timeout_seconds` | `false` / 600 / 60 | 父等子 `SIGUSR1` 再退出，systemd 由此推迟 `SIGQUIT` 老进程 |

CLI（`Opt`）：`-u/--upgrade`（收 fd 不 bind）、`-d/--daemon`（唯一回写 conf 的项）、
`-t/--test`（`bootstrap()` 直接 `exit(0)`，只验可启动性）、`-c/--conf`、`--nocapture`（兼容 cargo test）。
`-t` 校验链只做三项数值校验（`max_blocking_threads=0`、histogram buckets/scale/resolution），无 version/路径存在性校验；
升级前推荐 `newbin -t -c conf` 先验。

## 优雅升级三步 runbook

保证：请求必被老/新其一接住（无 `ECONNREFUSED`）；grace 期内能完成的请求不被杀。

- **Step 0 约定 socket**：新老 `upgrade_sock` 同路径；`daemon_wait_for_ready = true` 时同步配 ready 超时。
- **Step 1 起新进程**：`./newbin -u [-d] -c /etc/pingora.conf`（`--upgrade` 使 fd 走 `get_from_sock` 收而非 bind）。
- **Step 2 切流量**：对老 PID `kill -QUIT`。老进程交 fd（`CLOSE_TIMEOUT = 5s` 硬编码休眠给新进程初始化），
  随后广播优雅停；新进程立即接新连接，老进程走 `grace_period → graceful_shutdown_timeout` 排空后退出。
  `daemon_wait_for_ready = true` 时 systemd 等新 daemon `SIGUSR1` 后才发 `QUIT`，避免新未 ready 老已退。

善后/回滚：`kill -TERM` 老 = 全实例优雅停；`Ctrl-C/SIGINT` = `FastShutdown`（`shutdown_timeout = 0`，直接掐）。
`watch_execution_phase()` 可订阅 `Running → GracefulUpgradeTransferringFds → CloseTimeout → ShutdownGracePeriod → ShutdownRuntimes → Terminated`
做 readiness/告警；`collect_listen_addresses` 会剪掉无 service 认领的继承 fd 并 warn。

## 可观测 checklist

- **Prometheus**：加 `pingora-prometheus` 依赖，`prometheus_http_service()` + `add_tcp("0.0.0.0:1234")` 作为 service 挂入；
  业务 `register_int_gauge!` 等 static metrics 自动暴露；该端口本身也要纳入 fd 交接/监控。
- **Logging**：`log` 五级 `error(阻断)/warn(自愈)/info(起停)/debug+trace(release 编译掉)`；
  `error_log` 仅 daemon 落盘，否则 STDERR；业务用 `logging()` 写 access log。
- **Sentry**：`sentry` feature + 代码 `set_sentry_config`，仅 release 生效，guard 存 `Bootstrap` 保活，
  daemon fork 后子进程重 `init`；YAML 无 DSN 项，必须代码配。
- **排障变量**：`upstream_debug_ssl_keylog + SSLKEYLOG` 解密上游流量；`runtime_metrics_poll_time_histogram`
 （需 `tokio_unstable`）查调度延迟；`dial9` 需 feature + 代码覆写，非 YAML。

## 生产坑

1. **`run_forever` 前不建业务线程**：daemon `fork()` 会丢之前创建的线程，所有后台线程必须在 `run()` 之后起。
2. **`threads` 按 service 独享**：4 service × threads 4 = 16 线程 + 池上限同步放大，容量规划别按进程算。
3. **`max_retries = 16` 是 fail-safe 不是策略**：重试语义由 `set_retry` + 幂等判定定，这个数只防无限环。
4. **prom 端口忘交接**：metrics 端口作为 service 挂入才会被 fd 交接；独立起的 metrics server 升级时会断采，告警空窗。

## 可借鉴实现

- pingap 双模式（`pingap-config/README.md`）：`--autoreload` 就地换（容器）/ `--autorestart` 经 `upgrade_sock.ready`
  readiness 交接（`restart_ready_timeout` 默认 1m，失败放弃）+ `pingap -t` 校验。残差（addr/TLS listener）一律走重启。
- pingsix 原子发布（`USER_GUIDE.md`）：`build candidate → validate（上游→服务→路由→SSL）→ publish immutable snapshot → reconcile health`，
  失败留 last-known-good——抄发布流水线顺序。
- zentinel systemd（`deployment.md`）：文件布局表 + `reload（SIGHUP 不断连）/ restart（断连）` + `test/validate --config` 预检。
- aralez 远程推（`aralez.rs/docs/api`）：`POST @upstreams.yaml :3000/conf?key=MASTERKEY`，`?save` 持久否则易失——
  动态推配置的最小形状（含易失/持久语义）。
- `vicanso/pingora-demo` 排障三件：`session.digest()` 计时定位慢段 / 按服务设 threads（`lb.threads = Some(n)`）/
  自定义错误页保持 keepalive（`respond_error` 后不断连接）。

## 相关篇

- `pingora-tls-listeners.md`（监听器与 TLS 后端）· `pingora-lb-failover.md`（健康检查后台任务）· `pingora-gateway-patterns.md`（发布流水线）

## 版本与参考链接

- https://github.com/cloudflare/pingora/blob/main/docs/user_guide/graceful.md
- https://github.com/cloudflare/pingora/blob/main/docs/user_guide/conf.md
- https://github.com/cloudflare/pingora/blob/main/docs/user_guide/systemd.md
- https://github.com/cloudflare/pingora/blob/main/docs/user_guide/prom.md
- https://github.com/cloudflare/pingora/blob/main/pingora-core/src/server/configuration/mod.rs
