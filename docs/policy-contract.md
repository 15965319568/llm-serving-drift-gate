# 正式业务合同 v4

## 1. 时间、状态与来源

时间为 UTC ISO 8601，以 Z 结尾，支持亚秒精度。可见性边界包含 evaluation_time；撤销/关闭在该时刻立即生效。
请求按 received_at、评测按 evaluated_at、遥测按 sampled_at 截断。
注册表与两种 override 同时满足 effective_at <= cutoff 和 recorded_at <= cutoff 才可使用；
较晚收到的补录不能改变此前决策。override 只使用 approved，pending 不生效。
同对象按 (effective_at, release_id/revision_id 字典序) 选择最大记录；不累加历次版本。
注册表按 model_version、租户覆盖按 tenant_class 分组。没有 override 使用基础规则；
候选注册表不存在或最新状态不是 active 时禁止分配候选流量。

审批按同候选版本、同 approval_scope、signed_at <= cutoff 筛选，按 (signed_at,approval_id) 取最新。
最新审批非 approved 即不授权；revoked_at <= cutoff 即撤销，不能回退到更早批准。
候选 feature flag 的有效区间是 [enabled_at, disabled_at)，空结束时间表示未关闭。
critical 事故在 [occurred_at,resolved_at) 生效，未解决则持续生效，取 component 字典序最小者记入证据。
policy、正式注册表/override、审批与遥测为依据；聊天和 route_history 不授权。
历史路由按同 scenario、changed_at <= cutoff 选最新，(changed_at,changed_by) 解平局，无记录时为空。

## 2. 请求、质量与费用

每个模型统计所有可见请求。ok/200/success 是成功，其他 status 是错误。错误率分母为全部可见请求。
p95 为 nearest rank，即排序后 ceil(0.95*n) 的位置；空组为 null。
输入 PSI 的桶边界为 [0,256,512,1024,2048,+inf)，左闭右开；稳定版和候选版分别归一化，
比例至少取 1e-6，PSI=sum((candidate_share-baseline_share)*ln(candidate_share/baseline_share))；空组为 0。

按候选版本、slice 聚合所有可见评测 valid=true 的行（不同 evaluation_id 是独立评测批次，不相互覆盖）。
quality_delta 为 (candidate_score-baseline_score) 的非负 weight 加权均值；零总权重为 null。
评测 PSI 将每行配对的 baseline_count/candidate_count 在各自列归一化后按同一公式求和，保持行的配对关系。
统计 evaluated_rows 与 valid_rows；required_slices 缺失也须输出。
valid_rows < min_valid_rows 为证据不足；delta 为 null 或 < max_quality_delta、PSI > max_psi 均使切片失败。
critical 由 policy critical_slices、可见行 critical 标记或 delta < critical_quality_delta 决定。
阈值比较使用未舍入的指标；输出数值的舍入不能改变门禁结论。
任一要求切片失败使 quality_passed=false。总体 SLO 与每个租户 SLO 分别判断，边界相等通过。
租户预算使用截至时点的正式覆盖；无请求租户视为证据不足，租户 SLO 不通过。

历史费用对可见请求按模型聚合 input/output token，各乘 cost_rates 的每百万 token 单价。此费用与预测放量费用分开。

## 3. 两侧容量

replica 必须 authoritative=true，ready_at <= cutoff < retired_at（空 retired_at 表示未退役）。
每个活跃 replica 输出一行；节点必须在 node_inventory 中 authoritative=true。
遥测按同 replica/model、authoritative=true、sampled_at <= cutoff 取最新记录（附件保证权威同时间点唯一）。
依次检查节点、缺失遥测、gpu_mem_pct > max_gpu_mem_pct、queue_depth > max_queue_depth；首个失败作为排除原因。
通过的容量为 min(prefill_tokens_s,decode_tokens_s)*deployment.throughput_multiplier/capacity.mean_tokens_per_request。
此值是本服务的额定请求等价容量，使用统一 token 基准；tenant_demand 的 token 均值用于费用估算。
逐 replica 保留 6 位小数，服务池对未取整容量求和后保留 6 位；对账容差 1e-6。

## 4. 联合灰度分配

tenant_demand 每个 scenario/tenant_class 恰好一条，traffic_rps 非负，三租户之和等于场景 traffic_rps > 0。
租户按 tenant_class 字典序排序。各租户 candidate_pct 是从 0 起 routing.step_pct 的整倍数，且 <= 该租户 max_canary_pct。
全局候选比例上限取 policy.target_canary_pct、最新正式 autoscaling override（或基础 max_canary_pct）、scenario.route_cap_pct 的最小值。
全局比例为 sum(tenant_rps*candidate_pct/100)/scenario.traffic_rps*100，不要求是整数，也不是租户百分比的简单平均。

每个分配必须满足：候选与稳定流量分别乘 (1+headroom) 后 <= 对应服务池容量；有候选流量时 usable_replicas >= autoscaling.min_ready_replicas。
预测费用按每个租户的需求 RPS、该租户 mean_input_tokens/mean_output_tokens、两模型的分配份额及 cost_rates 相乘求和；
单位 USD/s，必须 <= scenario.max_projected_usd_per_s。
候选审批、开关、注册表、质量、请求 SLO、输入 PSI、租户 SLO 任一不通过，或存在 critical 事故/force_rollback 时，候选份额只能为 0。
零候选份额仍需检查稳定池容量与成本，不自动视为可行。

在全部可行方案中，依次最大化候选 RPS、最小化预测 USD/s、最大化按租户字典序排列的百分比元组；后一级只解上一级平局。
用未四舍五入值作比较。route_alternatives 交付所有租户格点组合及每个组合的所有失败原因：
global_route_cap、release_gate、min_ready_replicas、candidate_capacity、stable_capacity、projected_cost。
plan_id 为字典序租户百分比用 / 连接。仅最优方案 selected=true。
无可行解时所有 selected=false，决策 selected_plan_id 为空，费用为 null，分配表候选为 0、稳定为需求全量并 plan_feasible=false；
该分配只是阻断后的需求记录，不是已验证可执行路由。

## 5. 决策与执行

有正候选流量的最优可行方案为 CANARY。
显式 force_rollback、已撤销审批、critical 事故、已测得的请求/租户 SLO 或输入漂移失败要求 ROLLBACK；
任一 valid_rows 达标的切片发生质量失败也要求 ROLLBACK，即使另一切片证据不足。
缺失请求/SLO 证据本身只阻断候选，不构成已测得失败；缺失评测不能覆盖明确的撤销、事故或其他已测得失败。
其他不满足条件的情况为 HOLD。记录所有门禁原因；无其他门禁原因且无可行分配时加 no_feasible_allocation，
有零候选可行方案但不能灰度时加 candidate_capacity_insufficient。

执行动作：CANARY -> APPLY_CANARY；普通 HOLD -> KEEP_STABLE；无可行分配的 HOLD -> MANUAL_INTERVENTION。
ROLLBACK 仅在回滚窗口 [window_start,window_end) 有效且稳定池能承载全量需求及 headroom 时 APPLY_ROLLBACK，
否则 MANUAL_INTERVENTION。业务回滚要求与物理执行可行性必须分别展示。
本条执行条件还需同时满足 operations/continuity.md 中的运维约束。

## 6. 对账与可观测性

operator claim 按 claim_id 保留。candidate_version、target_canary_pct、approval_scope 与 policy 相等为 agrees_with_formal_policy，
不等为 overridden_by_formal_policy；其他主张为 untrusted，不能推定其已经证实。
trace_audit 的 visible_scenarios 按 (evaluation_time,scenario_id) 排序，first_visible_scenario 为其中第一项。
证据 ledger 记录所选版本，tenant_budget_revision_count 是所有已知、已生效 approved 变更行数（含被较新变更覆盖的历史行）。

BoundedMetrics 保留 inc/observe_ms/snapshot 接口；labels 仅 model_family/model_version/status/scenario，未知 label 必须抛 ValueError，
新 series 超过 32 必须抛 ValueError。桶为 [50,100,250,500,1000,2000,5000]ms 加溢出，非累计计数。
每个场景记录一次决策 counter 和一次候选 p95 TTFT 观测，空 p95 按 0。审计仅事件/场景/状态/数量，manifest 仅非敏感版本、输入文件名、计数等元数据；具体结构见 output-contract.md。

## 7. 窗口内运维证据

release_directives.ndjson、authority_grants.csv、directive_revocations.ndjson 和 dashboard_snapshot.json 均为必需附件。
它们的授权、生效、组合和展示口径由 operations/authority.md 与 operations/continuity.md 约束；
无解证据由 operations/handoff.md 约束。基础安全门禁和上述约定共同适用，不能用单一文件的最新日期代替全部来源核验。
