# range-shield — 通用 Range 风暴盾 spec（服务端，内容无关）

> map [#20](https://github.com/ArchivalEra/cloudflare-related/issues/20)。只认：不透明字节 + version + 长度。
> 视频 / DB blob / 资源站通吃；客户端、CDN 配置、对方 binary 实施全部 out of scope。

## 导航

| 篇 | 内容 | ticket |
|---|---|---|
| 00-key-schema | key 四元组、S 取值表、换算规则、小文件退化定理 | [#21](https://github.com/ArchivalEra/cloudflare-related/issues/21) |
| 01-coalescing | 合并窗口 + single-flight、竞态五条 | [#22](https://github.com/ArchivalEra/cloudflare-related/issues/22) |
| 02-progressive | 首分片 unblock、取消复用、等待上界 | [#23](https://github.com/ArchivalEra/cloudflare-related/issues/23) |
| 03-coherence | version 翻转、唯一冷路径、重验三 outcomes | [#24](https://github.com/ArchivalEra/cloudflare-related/issues/24) |
| 04-admission | 三级准入、参数三档、热点发现 | [#25](https://github.com/ArchivalEra/cloudflare-related/issues/25) |

## 三场景退化矩阵

| 场景 | 请求形态 | 走的路 | 不走的路 |
|---|---|---|---|
| 视频点播/滑动 | 同文件多 range 并发 | 全功能：分片→合并窗→首片 unblock→热点常驻 | — |
| DB 备份（大冷块） | 大 range 顺序扫 / footer 点读 | 分片 + flight（W 可设 0）；footer 段可常驻 | 合并窗基本无用（无害）；L1 可设 0 直落盘 |
| 资源小文件 | 整对象 GET（< S） | 单分片 = 整对象缓存 + flight；自动退化 | 窗/流/分级全部自然空转，零额外代码 |

## 与周边的位置

* `origin-front/`：front 纯透传，风暴与它无关（§4 非目标）；本盾摆在业务面（`origin_cache` 一侧）。
* `skills/pingora-cache.md`：pingora 侧缓存语义参考；本盾是存储无关的通用层，可用 pingora-cache 实现，也可自研。
* U2/H3 落点：本 spec 即 H3/U2 方案的正文执行（key/合并/一致性/准入四节）。
