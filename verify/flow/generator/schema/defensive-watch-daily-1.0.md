# defensive-watch-daily/1.0

公开契约说明。这里没有观察名单、资金数字或研究结论。生产输入只在构建时读入，加密后嵌入 `verify/flow/index.html`。

## 构建输入

| 文件 | schema | 是否必须 |
| --- | --- | --- |
| `web/latest.json` | `defensive-watch-daily/1.0` | 必须 |
| `web/history.json` | `defensive-watch-history/1.0` | 发布时必须；预览可缺，页面标未提供 |
| `web/pool.json` | `defensive-watch-pool/1.0` | 可选 |
| `web/review.json` | `defensive-watch-candidate-review/1.0` | 可选 |

百分比字段是百分数值（1 表示 1%）。权重 `weight` 不是百分数字段，原值 1 表示 100%。缺失必须是 JSON `null`。

`latest` 顶层至少包括：`schema`、`pool_version`、`pool_status`、`date`、`timezone`、`fetched_at`、`revision`、`run_id`、`mode`、`status`、`coverage`、`metrics`、`stocks`（20）、`benchmarks`（3）、`market_breadth`、`interpretation`、`method`。可另有 `data_finality`、`last_verified_at`、`provider_finality_flag_available`、`post_update_verification`。

`coverage.status` 只表示完整度：`complete_core` / `partial` / `pending`。计数不足时，即使写了 `complete_core`，页面也按未齐全展示。

`stocks[].moneyflow.net_mf_amount` 是主净额（原始单位万元）。页面主数字用已经换算的 `net_flow_yuan`（= 原值 × 10000）。分档 `class_net_yuan` 只作分类差额，不能相加后替换主净额，也不能写成机构、游资或散户比例。`amount_yuan` 已由日成交额千元 × 1000 换成元，页面不再乘一次。

`windows` 的键是 `"3"`、`"5"`、`"10"`。窗口不完整时，累计净额和占比应为 `null`。`inflow_days` 为 `null` 时页面显示缺失，不显示 0。

`interference` 里回购、重大公告、指数调仓的 `unreviewed` 表示尚未核验，页面不得写成没有干扰。`adjustment_factor_changed` 是已核对的布尔值，与那三项分开。

`history.series[].mode=backfill` 是入池前回填，不能当成当时选股或样本外验证。未满窗口的 5 日、20 日收益保持 `null`。

## 页面不得自行作出的结论

- `pool_status=proposed_for_confirmation` 或 `mode=candidate_baseline`：显示候选核验、建议采用、待定稿。不显示已正式锁定。
- 不因等权收益为正而改写成全面修复或广泛回暖。只展示输入里的 `interpretation`；没有该字段就不归类。
- 不重算组收益或个股权重，也不宣称与输入等权一致。组收益只用 `metrics.sectors`。
- 不把覆盖齐全写成供应商终值。`post_schedule_snapshot_verified_20of20` 只表示约定时点后的复核。
- 候选基线和回填不生成 5/10/20 日样本外表现。

## 合成夹具

测试数据带顶层 `"fixture": "synthetic"`，代码以 `SYN` 开头。发布入口拒绝这类输入。
