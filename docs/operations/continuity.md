# 发布窗口的服务连续性约定

本约定作用于各场景的候选版本。权限和指令有效性采用 authority.md；基础安全门禁采用 policy-contract.md。
四种 kind 的业务含义如下。

- routine_freeze：指定租户暂不向候选分配新流量，该租户候选份额必须为 0。
- continuity_floor：迁移期间为指定租户保留至少 candidate_pct 的候选份额。多个有效保底共同约束，取最大值。
- freeze_waiver：只解除 target_id 指向的同租户、当前场景内有效的那一条 routine_freeze；其他冻结继续生效。
  目标不存在、不是当前有效冻结、或属于另一租户时标记 target_inapplicable，不产生豁免。有效豁免仍为 active，
  对应冻结标记 waived；双方 related_ids 记录关联。豁免不放宽租户基础上限、容量、预算、正式发布审批或质量门禁。
- emergency_stop：任一适用于本场景的有效紧急停止，要求整个候选版本流量为 0，并要求业务回滚。
  该场景所有租户的 continuity_floor 和 freeze_waiver 暂停，标记 suspended_by_emergency；常规冻结保持 active。
  紧急停止不豁免稳定容量、成本预算或回滚执行窗口。

没有紧急停止时，冻结和保底均有约束力；不得自行选择优先级、静默把保底降到 0，或因为它们冲突就删除其中一条。
有有效定向豁免时解除对应冻结后，保底继续生效；存在未解除冻结且保底大于 0 时，没有同时满足二者的分配。
其他发布门禁关闭也不自动撤销保底。此时可以记录业务回滚要求，但必须识别路由约束是否仍然无解。

tenant_controls 按场景/租户输出：raw_floor_pct 保留暂停前的保底，effective_floor_pct 是当前实际保底，
frozen 表示至少一条有效冻结尚未解除，emergency_stop 表示场景整体存在有效紧急停止。
freeze_ids、floor_ids 是经权限和撤销检查后仍有效的相应指令；waiver_ids 是目标有效且未被紧急停止暂停的豁免；
stop_ids 是该场景所有有效紧急停止，须在每个租户行中相同。

路由定义域仍是原合同按基础租户上限形成的全部格点。不能提前删除违反运维指令的格点，否则无法审计失败原因。
每个方案额外按租户记录全部失败项：tenant_floor:<租户>、tenant_freeze:<租户>；
有紧急停止且总候选 RPS>0 时记录 emergency_stop。紧急停止本身不另记 release_gate，其他基础门禁仍独立检查。
决策存在有效紧急停止时记录 emergency_stop_active，状态为 ROLLBACK。

执行动作的新增条件：APPLY_ROLLBACK 除原窗口和稳定容量条件外，还必须有满足全部当前约束的零候选方案。
若保底、费用或其他条件使零候选方案不合法，执行动作是 MANUAL_INTERVENTION。
