# 本地 worker 与 router 协议

worker CLI 配置含 `protocol`（v1/v2）、`cases` 对象、`default_case`。cases 按请求 prompt 查找，用于 CPU 确定性回放；不执行真实模型。POST /generate JSON 为 `{request_id,attempt,model_version,prompt,max_tokens}`。返回 text/event-stream。协议数据可能在任意字节边界分片，包括多字节 UTF-8、CRLF 和 JSON；注释/心跳不产生 token。一个 SSE 事件可有多条 data 行，按标准用换行连接；未知 SSE 属性忽略。

v1：`event: token` 的 data 为 `{seq,text,tokens}`；`event: done` 为 `{input_tokens,output_tokens}`；`event: error` 为 `{code,retryable}`。

v2：event 名为 message，data 的 type 为 delta/complete/failure。delta 含 `index,content,token_count`；complete 含 `usage:{input_tokens,output_tokens}`；failure 含 `code,can_retry`。

HTTP 响应头 X-Worker-Protocol 声明版本，v1/v2 均须支持。成功流必须收到 complete/done；EOF 不代表成功。5xx/网络断开属于可重试故障，但仍受“没有持久 delta、未超尝试数”的限制；其他非200响应是不可重试 worker_http_error。未知版本/未知业务事件、字段类型错、序号缺口、同序号不同内容、负或非整数 token、超出 max_tokens、完成 usage 与累计 output_tokens 不等均为不可重试 protocol_error。

delta 序号从0起，每次 attempt 独立。完全相同的重复序号是传输重放，忽略；不能重复显示/计费。text 是字符串，tokens 为正整数。usage 两字段均为非负整数。成功终止后不再接受业务帧。

## 确定性回放工具

case 为 `{attempts:[{chunks:[...],gate_before?,gate_after?,outcome?,fragment_bytes?,input_tokens?}]}`。尝试超过列表长度时复用最后项。chunks 为 `{text,tokens,duplicate?}`；duplicate=true 会重复发送同帧。outcome 为 success（默认）、disconnect、retryable_error、fatal_error、bad_sequence、conflicting_duplicate、bad_usage。fragment_bytes 为正整数，指定写出的最大字节片段；input_tokens 默认4。

gate_before 在首帧前等待同名信号；gate_after 在全部 delta 后、结束帧前等待。POST /admin/signals/{name} 放行信号，返回 released=true；信号保持已放行。GET /admin/stats 返回 calls（request_id、attempt、model_version）、router（revision、routes、epoch、apply_count）。这些端点供复现故障，不能进入正式监控指标。

worker 同时模拟外部路由配置存储，使用 --state 独立持久化，网关重启不影响它：

- POST /router/fence `{epoch}`：epoch 为非负整数，低于已知最大epoch返回409，其余更新最大epoch。
- POST /router/apply `{action_id,epoch,expected_revision,routes}`：先检查同 action_id 的语义幂等（整个对象相同返回原 applied 回执，不相同409），再检查 epoch 不低于已知最大值和 expected_revision 等于当前 revision。成功将 routes 整体替换并使 revision 加1；记 applied 回执并增加 apply_count。回执含 `action_id,state:"APPLIED",revision,routes`。
- GET /router/actions/{id}：已应用返回回执，不存在404。GET /router/state 返回当前 revision/routes/epoch/apply_count。
- POST /admin/faults `{drop_ack_once?:bool,fail_before_apply_once?:bool}`：前者在下一次成功持久化 apply 后断开连接，后者在下一次 apply 持久化前返回503；一次性消费。

初始 router revision=0、routes={}、epoch=0。网关初始所有租户候选比例为0，与该状态等价。worker 可在网关准备完成之前独立重启。router 动作幂等与 fencing 是外部协议事实，不能用只改本地报表模拟执行成功。
