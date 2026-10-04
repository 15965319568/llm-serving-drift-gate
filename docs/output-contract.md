# 输出合同 v4

字段名称与最小字段集合由 `output-schema.json` 给出，CSV 为 UTF-8 带表头。字段允许附加但文件集合为规定的 21 个。
复合字段（reasons、excluded、tenant_percentages、visible_scenarios，以及各类 *_ids）为 CSV 单元格中的 JSON。
布尔为 True/False 或 true/false；null 数值在 CSV 中为空、JSON 中为 null。数值采用绝对误差容差（含边界）：比例/指标/容量 1e-6，历史费用 1e-8，预测费用 1e-9。
计数必须是精确的非负整数值；CSV 的数值按值比较，5 与 5.0 等价。ID、状态、原因码按字符串比较。plan_id 用无小数点的整数百分比连接，例如 5/10/15。
输出容差仅用于序列化后的数值比较，不能用于放宽业务门禁或改写最优方案；决策与优化仍使用原始未舍入值。
固定输入的全部输出必须逐字节确定性；不要使用当前时间或绝对路径。

| 文件 | 行粒度/含义 |
|---|---|
| decision_matrix.csv | 每场景一次发布结论、候选/稳定比例、两侧容量、原因、最优方案与费用 |
| capacity_report.csv | 每场景/模型的活跃可用 replica、容量、排除清单、权威遥测数 |
| capacity_replica_detail.csv | 每场景/活跃 replica 的容量、是否纳入、首个排除原因 |
| drift_findings.csv | 每场景/评测切片的有效行、加权差值、PSI、critical、通过与原因 |
| tenant_health.csv | 每场景/候选租户的可见请求、错误率、p95、SLO |
| cost_report.csv | 每场景/模型的已发生 token 与 USD |
| model_metrics.csv | 每场景/模型的请求/错误率/两种 p95/token/输入 PSI；稳定版 PSI=0 |
| evidence_ledger.csv | 每场景的正式审批、注册表、override、最新权威遥测时间、历史路由对照 |
| tenant_routing.csv | 每场景/租户的需求、两侧百分比及 RPS、整体方案可行性 |
| route_alternatives.csv | 每场景/租户格点组合的候选/稳定 RPS、成本、失败原因、可行及选中状态 |
| execution_plan.csv | 每场景的决策、负责人、目标、窗口、回滚容量安全性、执行动作 |
| trace_audit.csv | 每请求一次可见性记录，含全部可见场景和最早场景，不含原始 payload |
| directive_audit.csv | 每场景/原始指令的权限、生效、撤销与组合处理结果 |
| tenant_controls.csv | 每场景/租户的原始与实际保底、冻结、紧急停止和证据 ID |
| source_reconciliation.csv | 每 claim 一行来源对账，保留 ID、subject、value、claimed_at、source、resolution |

JSON/NDJSON 含义：input_validation 为输入计数与重复检查；observability_contract 为有限标签计数器和直方图；
run_manifest 为 schema/model/scenario/input filename 等元数据；audit.ndjson 为场景处理事件；
reconciliation_report 为 checks、counts、all_pass。checks 必须反映真实跨表核验：容量明细汇总、token/费用行一致、
候选请求与租户请求一致、路由与决策一致、请求/claim 不丢失、所需对象覆盖完整。不能仅写固定 true。
CSV 主键按字典序排序，trace 按 request_id；visible_scenarios 按时间排序，reasons 按字典序排序。

infeasibility_certificate.json 的字段及严格语义见 operations/handoff.md。它必须与独立计算的完整路由格点集合一致；不能只返回空列表或一个未经验证的原因。

## 状态与原因码

以下名称属于输出协议；与对应条件匹配时使用列出的字面值。相同业务状态在不同表之间必须一致。
这只是字段与取值约定，不提供本题各场景的答案。

decision_matrix.approval 与 evidence_ledger.approval_state 使用相同审批状态：

| 值 | 含义 |
|---|---|
| approval_missing_at_cutoff | 截止时点没有符合候选版本、审批范围和签发时间条件的审批 |
| approval_not_approved | 按合同选出的最新审批 status 不是 approved |
| approval_revoked_at_cutoff | 最新审批 status=approved，但 revoked_at 已到达截止时点 |
| approval_active | 最新审批 status=approved，且截止时点尚未撤销 |

按上述顺序判定；审批失败时，将同一个审批状态码加入 decision_matrix.reasons，approval_active 不加入原因。
decision_matrix.reasons 还须记录以下全部适用原因，去重后按字典序排序：

| 值 | 条件 |
|---|---|
| candidate_route_flag_inactive | 候选路由开关不在有效窗口 |
| candidate_registry_inactive | 候选注册记录缺失或最新 state 不是 active |
| quality_gate_failed | 至少一个要求切片未通过质量门禁；证据不足也属于未通过 |
| quality_evidence_incomplete | 至少一个要求切片 valid_rows 小于 min_valid_rows |
| latency_slo_failed | 候选总体 p95 TTFT/E2E 超预算，或相关观测缺失 |
| error_rate_slo_failed | 候选总体错误率超预算，或没有可见请求 |
| tenant_slo_failed | 至少一个租户 SLO 未通过，包括没有可见请求 |
| input_distribution_drift | 候选输入 PSI 大于 drift.max_psi |
| scenario_forces_rollback | scenario.force_rollback=true |
| critical_incident_active | 截止时点存在有效 critical 事故 |
| emergency_stop_active | 存在有效紧急停止指令 |
| no_feasible_allocation | 没有其他门禁原因、候选份额为零且不存在可行方案 |
| candidate_capacity_insufficient | 没有其他门禁原因、最优可行方案的候选份额为零 |

缺失观测虽然产生上述门禁原因，仍须按 policy-contract 第5节区分证据不足与已经测得的失败；不能仅凭原因名称把缺失观测判为 ROLLBACK。
route_alternatives.reasons 使用 policy-contract 第4节及 continuity.md 已列出的约束名，和上表的决策原因码是两个字段集合。

drift_findings.reasons 使用全部适用的以下原因码：
insufficient_valid_eval_rows（有效行数不足）、quality_delta_below_gate（delta=null 或低于门槛）、
quality_distribution_drift（PSI 超门槛）。通过时为空数组。

capacity_replica_detail.reason 依既定排除顺序使用 node_not_authoritative、telemetry_missing、
gpu_memory_over_limit、queue_over_limit；纳入容量时为 eligible。
capacity_report.excluded 是按 replica_id 排序的对象数组，每项含 replica_id、reason；
telemetry_replicas 是该场景/模型全部可见权威遥测涉及的去重 replica 数，不因活跃性或容量排除而删去。
candidate_registry_state 保留最新正式记录的 state，无记录时为 missing；未选到的审批/版本 ID、遥测时间、事故组件使用空字符串。
directive_audit.resolution 与 source_reconciliation.resolution 的完整字面值分别沿用 authority.md/continuity.md 和 policy-contract 第6节。

## JSON 嵌套结构与诊断元数据

observability_contract.counters 是对象数组，每项含 name（字符串）、labels（对象）、value（非负整数值）。
histograms 的每项含 name、labels、buckets_ms=[50,100,250,500,1000,2000,5000]、counts（8个非负整数值）。
第一个桶含 <=50 的观测，随后各桶左开右闭，最后一个桶为 >5000；计数非累计。
series_count 是 counter 与 histogram 的 (name, labels) 唯一组合总数；两类指标宜使用不同 name，name 的具体字面值由实现选择。
labels 的合法名称和32条series上限沿用 policy-contract；每场景一次counter增量、一次候选p95 TTFT观测。

input_validation 的 trace_rows、eval_rows、telemetry_rows 是原始输入总行数，duplicate_request_ids 是重复请求ID数量，
required_scenarios、required_slices 分别是policy中的场景数和必需切片数，均为非负整数值。
run_manifest 的 baseline_version、candidate_version 来自policy，scenario_count 为场景数，
route_history_rows 为原始历史路由行数，input_files 为按字典序排列的输入文件名数组。
run_manifest 和 reconciliation_report 的 schema_version 为字符串版本标识，不要求采用参考实现的具体版本号；无解证书的版本固定为 handoff.md 中的 "4"。
audit.ndjson 每场景一行，scenario/status 与决策表一致，reason_count 为该场景决策原因数；event 的具体非敏感名称由实现选择。
reconciliation_report.checks 是命名检查到布尔值的对象，counts 是命名计数到非负整数的对象，
具体键名由实现选择，但应覆盖本合同要求的跨表核验，all_pass 是实际检查结果的合取。

允许在规定文件中增加可复算、非敏感的诊断字段，例如原始附件行数；不能增加第22个文件。
新增未来/晚到记录、修改非权威遥测或展示快照时，已授权的业务结果应保持相同，但忠实记录原始输入变化的附加诊断字段可以改变。
验收的此类变形比较只核对合同中的业务字段，不要求所有诊断元数据逐项相等；相同输入的重复运行仍必须逐字节一致。
完整验收使用 task.toml 中的 verifier 总时限；没有另设未公开的单次 CLI 30秒或接口检查15秒淘汰条件。
