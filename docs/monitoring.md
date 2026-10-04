# 运行监控、证据时点与安全导出

POST /v1/telemetry 接收 `{"events":[...]}`，用于导入已有网关的合成历史。每事件包含 `event_id,request_id,tenant,model_version,kind,event_ms,recorded_ms,attempt,data`。时间和 attempt 为非负整数；kind 为 accepted、attempt_started、first_token、finished；tenant/model_version 必须已配置。data 是对象：accepted/attempt_started/first_token 可为空；finished 必须含 status（SUCCEEDED/FAILED/CANCELLED/EXPIRED）、input_tokens/output_tokens（非负整数）。

event_id 全局唯一；完全相同事件重放不增加记录，相同ID不同内容返回409，整批不提交。格式错400，整批不提交。未知额外顶层字段拒绝；data 仅允许 input_tokens/output_tokens/status，防止导入任意 prompt、URL 或 secrets。不要求导入时事件已到达当前时点，报告查询同时限制业务时间和 recorded_ms。内部请求也产生同结构事件，记录业务时钟；不存原始 prompt/key/text。event_id 的 internal: 前缀保留给内部事件，外部导入该前缀返回400。

GET /v1/metrics?start_ms=S&end_ms=E&as_of_ms=A，要求 0<=S<E<=A。默认 E=A=当前时钟、S=max(0,E-monitor.window_ms)；当前时钟为0导致空区间时返回400。响应含 `start_ms,end_ms,as_of_ms,groups,input_psi`。groups 按 tenant/model_version 排序，为所有配置组合保留一行。

证据仅限 event_ms<=A 且 recorded_ms<=A。每个 request_id 的 finished 取最早 (event_ms,event_id) 一条作为业务终态；只把终态 event_ms 位于 [S,E) 的请求计入该窗口。每个请求的 accepted 和 first_token 同样取最早一条（first_token不能早于accepted；不合法的配对不产生时延）。重复业务事件使用不同event_id也不能重复计数。attempts 是这些已终结请求的不同 (request_id,attempt) attempt_started 数量，不能以尝试数充当错误率分母。

每组必需字段：tenant,model_version,requests,successes,failures,cancelled,expired,attempts,billed_input_tokens,billed_output_tokens,error_rate,p95_ttft_ms。requests=四类终态之和；failures仅FAILED，expired单独列出；错误率=(failures+expired)/(successes+failures+expired)，没有分母为null。取消不算服务失败。账单token只计成功终态。p95 TTFT仅成功请求中有效 first_token-accepted，nearest rank，空为null。

input_psi 按租户输出 `{tenant,value}`：成功请求的input_tokens，按 [0,256,512,1024,2048,+inf) 左闭右开分桶，稳定/候选各自归一化，概率下限1e-6，再 sum((c-b)*ln(c/b))；任一侧无成功样本时value=null。报告数值容差1e-6，不要求固定小数位。指标标签仅租户/模型等有限配置域；禁止 request_id/prompt/原始键作为 series 标签。

monitor 配置：window_ms、min_completed（正整数）、max_error_rate（0..1）、max_p95_ttft_ms（非负数）、max_input_psi（非负数）。PROMOTE 使用当前时钟的上述窗口，要求每个租户候选模型 successes+failures+expired >= min_completed，错误率和有效p95不超阈值，且每租户PSI有值且不超阈值。缺证据不能算健康；边界相等通过。

GET /v1/snapshot 和 inspect 导出中 requests 只保留 runtime-api.md 请求快照除 text 外的字段；invoices 只含 request_id/input_tokens/output_tokens；actions 按 release-runtime.md。模型生成内容只在用户请求结果和流接口暴露。不得在导出中包含 prompt、原始幂等键、worker URL、认证、绝对路径或错误堆栈。
