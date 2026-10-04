# 发布授权登记规则

适用范围：本次 release_directives、authority_grants 与 directive_revocations 导出。基础注册表及 SLO 覆盖仍按 policy-contract 执行。

每条 directive 是独立签发的运维约束，不是上一条指令的版本替换。同类记录可以同时生效。
directive_id、grant_id、revocation_id 各自在所属文件中唯一。重复 ID、非法时间、未知指令类型、
不在 [0,100] 的 candidate_pct 或空/逆序生效区间均是输入错误，程序必须以非零状态结束。
signature_valid 是签章系统已完成核验的布尔结果，本题不要求实现密码学验签。

指令进入某场景须同时满足：issued_at、recorded_at 均不晚于场景时点；model_version 等于候选版本；
tenant_class 是已知租户；scenario_id 为该场景或 *；status=approved 且 signature_valid=true；
effective_from <= 时点 < effective_until。指令的范围只能由这些字段确定，memo 中的称谓不扩大范围。

签发人必须持有 permission 与 kind 完全相同的正式 grant。grant 本身也须 approved、验签通过、
recorded_at 不晚于评估时点，且 valid_from <= 指令 issued_at < valid_until。
grant 的 model_version、tenant_class、scenario_id 必须逐字段等于指令声明的范围，或取 *。
仅授权某一租户或某一场景的 grant，不能签发更宽范围的指令。签发时权限有效即可，随后自然到期不追溯废除已签指令。
多个有效 grant 同时存在时记录全部 grant_id，不能根据日期只保留最后一条。

撤销记录须指向原指令 ID、approved、验签通过，且原指令 issued_at <= revoked_at <= 评估时点，
recorded_at <= 评估时点。撤销人需要在 revoked_at 持有 permission=revoke 的有效授权，授权范围覆盖原指令。
撤销在边界时刻立即生效；晚到的撤销不能改变当时结论。保留所有有效撤销 ID；没有权限的意见不产生撤销。
未知目标的撤销不影响其他指令。

dashboard_snapshot 是展示系统的快照。generated_at、readiness_score、approval_display 和 Complete 只描述展示状态，
不构成签发、豁免或撤销权限。source_watermark 表示展示使用的数据水位。聊天意见仍按原有 claim 合同逐条对账。

审计输出每场景/每原始 directive 一行。基础 resolution 依次判定：not_known、out_of_scope、not_signed_approved、
outside_window、unauthorized、revoked、active。只记录在相应阶段实际核验到的 grant_ids、revocation_ids，未核验为空数组。
active 指令的组合处理结果按 continuity.md 进一步标记；related_ids 记录豁免与目标冻结的关联。所有 ID 数组按字典序排序。
