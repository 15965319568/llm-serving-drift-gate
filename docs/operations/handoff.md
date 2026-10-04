# 无解情景的交接证据

运营需要知道无法执行的具体原因，而不仅是一句 HOLD。交付 infeasibility_certificate.json：
顶层 schema_version="4"，scenarios 为按 scenario_id 排序的数组，每项含 scenario_id、domain_plan_count、
feasible_plan_count、minimal_conflicts。

定义域是本场景按基础租户上限与步长得到的完整格点集合，和 route_alternatives 严格相同。
可行方案存在时 minimal_conflicts=[]，并如实记录方案总数与可行数。

无可行方案时，minimal_conflicts 列出这个有限定义域上所有按包含关系最小的冲突约束集合：
集合中的约束共同排除每一个格点；从集合中任意去掉一条，剩余约束都不再能排除所有格点。
集合使用 route_alternatives 的失败原因名称标识约束，不使用自然语言猜测。
这里要求的是包含关系最小，不只要求元素个数最少的那一个集合；所有此类集合都要保留。
各集合内部及集合数组按字典序排列，无重复。

这一证据是相对于既定格点定义域的阻断证明，不声称放宽定义域后仍然无解，也不自动授权放松任何规则。
不允许在无解情景填写一个 selected=true 的替代方案，或将候选 0% 的需求记录当成已验证可执行路由。

运维指令审计、租户约束、全部替代方案、证书、最终决策与执行动作应逐场景相互对账。
程序可以自由选择求解及证书构造方法，验收只检查可见合同的业务行为与可复算结果。
