# 灰度动作、确认丢失与恢复

POST /v1/control/lease `{holder,ttl_ms}` 获取/续约发布执行权，holder非空、ttl_ms正整数。没有未过期租约时新授予 epoch（严格递增）；同holder未过期续约保留epoch；不同holder且租约未过期409。有效窗口为 now_ms<expires_ms。返回 `{holder,epoch,expires_ms}` 前须使外部router接受对应fence；网络失败503。租约持久化，重启不重置epoch或时间。

POST /v1/control/releases 为 `{action_id,holder,epoch,expected_revision,scenario_id,operation}`。operation 为 PROMOTE/ROLLBACK；epoch和expected_revision非负整数，其余非空字符串。action_id语义幂等：完全相同请求返回已有动作，不重复推进外部配置；同ID不同请求409，即使动作已失败或租约变化也不能覆盖原动作。

新动作先验证本地租约和当前路由版本；租约无效409/error=lease_invalid，版本错误409/error=revision_conflict。必须读取现有 evidence_dir 的正式离线门禁结果。未知scenario400。

PROMOTE 必须离线决策为 CANARY，且 monitoring.md 的实时窗口通过；routes 为离线最优租户候选百分比。ROLLBACK 不要求实时健康，但必须在离线回滚窗口内、稳定容量承载全量，并存在满足全部当前离线约束的零候选可行方案；routes所有租户为0。禁止因“要求回滚”而跳过实际安全条件。

不允许的动作返回422，包含 reasons 数组（按字典序、去重）。原因词汇：offline_gate_closed、live_evidence_incomplete、live_error_rate、live_ttft、live_drift、rollback_unsafe。实时证据不足时，不额外把缺失值记成测得超阈值；实际错误率超阈值应记录，即使另一组证据缺失。拒绝动作不创建外部副作用。

通过预检后须先持久化 PREPARED，再调用 router/apply。接纳返回202和动作快照。正常获得回执后 state=APPLIED，并原子更新本地 routing revision/routes；不能先更新本地 observed 再执行外部。网络错误/5xx时保持 PREPARED/error=router_unavailable。外部409则 ABORTED/error=router_conflict，不修改本地 observed。

动作快照必需字段：action_id,state（PREPARED/APPLIED/ABORTED）,operation,scenario_id,epoch,expected_revision,routes,revision,error。未应用revision为null，正常无错error为null。holder、内部请求指纹、URL不必出现在快照中。

POST /v1/control/reconcile（无必需body）或服务启动恢复处理 PREPARED：

1. 先查外部 /router/actions/{id}。已经应用且回执routes匹配该动作时，接纳回执并更新本地 observed，即使原租约现已过期；因为副作用已发生，不能伪称没有执行。结果必须持久化且重放不重复执行。
2. 外部404表示尚未应用；此时重新校验租约、epoch、版本和当前业务门禁。失去租约/版本则 ABORTED/error=stale_action，门禁关闭则 ABORTED/error=gate_changed。
3. 验证通过才重放原动作。同一 action_id 不得换用新epoch或新路由。暂时网络错误仍PREPARED。

reconcile 返回 `{"actions":[动作快照...]}`。同进程控制动作串行化，最多一个 PREPARED；存在另一个PREPARED时，新action返回409/error=pending_action。已接纳的业务请求始终使用原路由快照；控制动作只改变之后的新请求。
