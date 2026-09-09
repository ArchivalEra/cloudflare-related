# 00-key-schema — key 四元组与分片映射规则（RS1 #21）

> 本层只认：不透明字节 + version（etag）+ 长度。任何 range 先换算成分片集合，再无 range 的事。

## 1. key 四元组

```
key = (namespace, object_id, version, seg_idx)
```

| 位 | 含义 | 规则 |
|---|---|---|
| `namespace` | 租户/桶隔离域 | 不同 ns 永不共享分片（防跨租户投毒，GHSA-f93w 同类） |
| `object_id` | 对象标识（视频 id / DB blob 名 / 资源路径） | 不透明字符串，不解析 |
| `version` | 一致性版本（etag/内容 hash/写入代号） | 翻转即新对象（见 03）；key 里必须有它，无 version 的存储由接入层先合成（如 mtime+size，不推荐，标险） |
| `seg_idx` | 分片序号 `floor(offset / S)` | 从 0 起，`S` = 本 ns 段长 |

## 2. 段长 S 取值表

| 场景 | S | 依据 |
|---|---|---|
| 视频点播 | 1–5MB（推荐对齐 CMAF 段长） | 播放器要什么 = 缓存有什么，range 永不跨片 |
| DB blob/备份块读 | 8–16MB | 列块/footer 读落到 1–2 片；全量导出顺序扫，无合并收益也无害 |
| 通用资源站 | 1MB | 小文件（< S）单分片 = 整对象缓存，自动退化零成本 |
| 直播/低延迟 | = GOP/分段时长对应字节 | 跟封装走，不自创粒度 |

S 按 namespace 可配，不按 object 可配（同 ns 同 S，否则分片边界漂移即缓存分裂）。

## 3. range → 分片换算

```
segs(range [a, b)) = { floor(a/S) .. floor((b-1)/S) }
后缀 range（bytes=-N）：先换算 [len-N, len)，len 未知先取尾分片探长度
多重 range（multipart）：逐个换算后并集，一次走完全流程
```

* 换算纯函数，无 IO，可在接入层先做。
* 覆盖分片全命中 → 拼回（见 02）；任一缺失 → 缺失分片走合并窗口（见 01）。

## 4. 小文件退化定理

`len <= S` ⟹ 分片集合恒为 `{0}` ⟹ 本设计退化为“整对象缓存 + single-flight”。
**推论**：资源站小文件场景零额外代码、零额外开销；DB 大文件与视频大文件走全功能。不用分情况写代码。
