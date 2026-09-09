# quiche-build-ship — 构建矩阵、FFI 与发货清单

> 一句话职责：各平台把 quiche 编出来、装进产物、能观测。
> 前置：无（先看本篇再写代码，省构建税）。适用版本：`quiche 0.29.3`（Rust ≥1.88）。

## 构建矩阵

| 平台 | 要件 | 命令/备注 |
|---|---|---|
| Linux x86_64 | Rust 1.88+，cmake，C 编译器 | `cargo build --examples`；BoringSSL 经 `boring ^4.3` 自动编 |
| 自带 BoringSSL | `BORING_BSSL_PATH` | 指向自建库；版本漂移构建失败先对 lockfile |
| Android | NDK ≥19（21 推荐）+ `cargo-ndk` v2+ | `cargo ndk --features ffi -t arm64-v8a build`（DoH3 同款路） |
| iOS | `cargo-lipo` | `--features ffi` 出静态库进 Xcode |
| Docker | — | `cloudflare/quiche`（client/server）/ `quiche-qns`（interop runner）官方镜像 |
| Windows | NASM（汇编） + cmake | BoringSSL 编译要汇编器，无则死 |

feature 门：`boringssl-boring-crate`（默认）/ `ffi`（默认关，见附录）/ `pkg-config-meta` / `qlog` / `custom-client-dcid`（危险，改 DCID 生成，别碰）。

## 可观测（发货必带）

* `qlog`：`set_qlog(path, level)` + `QlogLevel`，wireshark 可视化对照（调参/排障第一工具）。
* `keylog`：`set_keylog` + `SSLKEYLOG` 环境变量，解密对包（对照 origin-front 排障三件）。
* `stats()` 定时采样：rtt/cwnd/丢包率进 metrics（对照 01 篇方法）。

## 生产坑

1. **BoringSSL 构建税**：cmake/go/perl 缺一即死；钉死 lockfile，CI 镜像预装三件套。
2. **0.29.2+ 必升**：FFI 连接 ID 迭代器 UAF（CVE-2026-11941，0.29.2 修）+ `ReusedSourceConnectionId` 限流（0.29.3，CVE-2026-12707）——用 FFI 的无条件升 0.29.3。
3. **apps 非生产重申**：`quiche-client/server` 无性能/安全保证，发货前换 tokio-quiche 或自写 worker（见 tokio-apps 篇）。

## 附录：FFI（C 集成）

* 开门：`--features ffi` 出 `libquiche.a` 全静态；`quiche/include/quiche.h` 与 Rust API 一一对应
  （`quiche_config_*/quiche_conn_*/quiche_h3_*` + 迭代器 + path events + stats + dgram + varint）。
* 坑：C 侧迭代器内存管理（CVE-2026-11941 就是 UAF 实证，0.29.2 前别碰）；
  `custom-client-dcid` 改客户端 DCID 生成——除非写 interop 测试，否则别开。
* 对照：curl quiche 后端即此路（EXPERIMENTAL）；kiche 的 pin+submodule+Gradle 编排（Kotlin MPP 版 FFI 工程样板）；
  `goburrow/quiche`（Go binding，2019 停更）只证明可行，勿复用。

## 相关篇

* 前 6 篇 quiche（本篇是发货单）

## 版本与参考链接

* https://github.com/cloudflare/quiche（README Building 章）
* https://github.com/curl/curl/blob/master/docs/HTTP3.md#quiche-version
