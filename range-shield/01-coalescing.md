# 01-coalescing — 合并窗口与 single-flight 语义（RS2 #22）

> 目标：N 并发同对象 range ⇒ 每分片恰好 1 次上游拉取。

## 1. 两层机制

* **single-flight（每分片）**：同 `(ns, object, version, seg)` 在途只允许一个上游拉取，其余等待者挂接同一结果。这是底线，无窗口时也成立。
* **合并窗口（每对象版本）**：短窗 `W`（默认 50–100ms，可配）内到达的同 `(ns, object, version)` 请求，
  其分片集合取并集后统一调度：已在途的挂接，未覆盖的分片新起 flight。窗口解决“同一时刻 N 个不同 range 各缺不同分片”的合并；
  flight 解决“同一分片 N 人等”。

## 2. 窗口语义（伪形）

```
on_request(req):
  segs = map(req.range)                       # 00 换算
  hit, missing = lookup(segs)                 # 段缓存查
  if missing empty: return assemble(hit)
  win = window_for(ns, object, version)       # 无则新建，W 后关闭
  win.add(missing, waiter=req)                # 并集；关闭时统一调度
on_window_close(win):
  for seg in win.missing_union:
    single_flight(seg, fetch_from_upstream)   # 每分片恰一次
  fan_out(results, win.waiters)
```

## 3. 竞态规则

| 情况 | 语义 |
|---|---|
| 窗内新到请求缺的分片已有 flight | 挂接，不新建 |
| 窗关闭瞬间到达 | 进下一窗，不追旧窗（旧窗结果进缓存，新请求大概率直接命中） |
| flight 失败 | 同窗等待者同失败（错一次）；下窗重试（不熔断，熔断是 LB 层的事） |
| version 翻转落在窗内 | 旧窗按旧 version 结算并丢弃结果（不回填，见 03）；新请求进新窗 |
| 超时 | 每 waiter 独立超时（其 SLA），窗与 flight 不设全局超时；首分片超时走 02 的整体失败 |

## 4. 参数

| 项 | 默认 | 说明 |
|---|---|---|
| `W` | 50–100ms | 滑动场景突发密度实测后再调；DB 顺序扫可设 0（退化为纯 flight） |
| 单对象并发 flight 上限 | 不限（靠段数自然收敛） | 真要限走 LB/限流层，本层不限 |
