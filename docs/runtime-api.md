# Serving 运行接口 v5

本项目为合成维护工程。离线业务合同继续适用；本组运行合同定义同一次发布的数据面和控制面。所有依赖均在本地，CPU worker 模拟生成计算，HTTP、持久化、取消、恢复和发布动作必须实际执行。

## 启动与配置

`python -m serving_gate serve --config CONFIG --state SQLITE --host 127.0.0.1 --port 0 --ready-file FILE` 启动网关。端口 0 由系统分配，ready 文件为 JSON，至少包含 `url`。启动迁移和恢复成功后才写 ready。支持 SIGTERM/正常进程终止；强制终止后也须恢复已提交状态。

`python -m serving_gate worker --config CONFIG --state SQLITE --host 127.0.0.1 --port 0 --ready-file FILE` 启动本地 worker/router。worker 是联调工具，公开协议见 worker-protocol.md。

`python -m serving_gate inspect --state SQLITE --output DIRECTORY` 在网关停止后导出 `runtime_snapshot.json`，结构等同 GET /v1/snapshot，结果不包含原始 prompt、幂等键、配置中的 URL 或磁盘绝对路径。不得修改数据库业务内容。离线 `--input/--output` CLI 和 BoundedMetrics 接口保持兼容。

网关配置字段：`stable_version`、`candidate_version`、`worker_urls`（两个版本到本地 HTTP URL 的映射）、`router_url`、`evidence_dir`（相对配置文件解析）、`tenants`（租户到 `{max_running,max_queued}` 的映射）、`max_running`、`max_attempts`、`worker_timeout_ms`、`clock_start_ms`、`monitor`。所有数量参数是正整数，clock_start_ms 为非负整数；monitor 见 monitoring.md。worker URLs 只用本地 HTTP，不需要凭据。未知租户/版本、缺少必需配置或非法数值启动失败。

本地回放时钟以整数毫秒表示，由数据库持久化。第一次建库用 clock_start_ms；重启保留数据库时钟。POST /v1/clock `{"advance_ms":N}` 原子增加非负整数 N，返回 `{"now_ms":...}` 并触发截止时间检查。流式线程中的操作读取同一时钟。真实 socket 超时只用于检测 worker 失联，不应改变回放时钟或伪造业务完成。不能在后台自动推进回放时钟。

## HTTP 通用行为

请求和 JSON 响应均使用 UTF-8。必需字段缺失、错误类型、非有限数、负数、非法 JSON/非法操作枚举返回 400；未知对象或端点 404。JSON 错误至少包含 `error` 字符串，可以附带安全诊断。额外响应字段允许，不能与合同事实矛盾。不依赖 JSON 键顺序。HTTP 对象大小上限为 1 MiB，超过返回 413。该大小是接口协议限制，不是模型运行资源设置。

GET /health 返回 `{"status":"ok"}`。GET /v1/snapshot 返回下面六个字段：`schema_version:2`、`now_ms`、`requests`、`invoices`、`routing`、`actions`。列表按 request_id/action_id 字典序排序。routing 为 `{revision, routes}`，routes 为每租户的候选百分比（整数 0..100）。actions 见 release-runtime.md。

## 请求接纳

POST /v1/requests 的对象为 `{tenant,idempotency_key,prompt,max_tokens,deadline_at_ms?}`。前三项为非空字符串，max_tokens 是 1..4096 整数；显式 deadline_at_ms 是非负整数。省略截止时间表示无截止时间。首次接纳返回 202 和请求快照，合法重放返回 200 和同一请求快照，相同租户/幂等键而 prompt、max_tokens 或显式 deadline_at_ms 不同返回 409。不同租户的相同键互不冲突。超出该租户 max_running + max_queued 的未终结请求数返回 429，不持久化被拒请求，也不占用幂等键。未知租户400。

幂等键仅在租户内作用。并发相同键必须形成一个逻辑请求。输入指纹不包含路由版本或当前时间；相同请求在发布之后重放仍指向原请求。调度前已到期的请求可以被接纳，但立即进入 EXPIRED，不执行 worker。

路由在接纳事务中固定，后续排队、重试和重启不得重新选择。桶值为 SHA256(UTF8(tenant + U+0000 + idempotency_key)) 的前 8 个十六进制字符作为整数 mod 100；桶小于当前该租户候选百分比则用候选模型，否则稳定模型。记录当时 routing revision。request_id 可自由选择，但必须持久、唯一且不泄漏原始键。

## 生命周期和输出

状态枚举：QUEUED、RUNNING、SUCCEEDED、FAILED、CANCELLED、EXPIRED。最后四项是不可逆终态。全局 RUNNING 不超过 max_running，各租户不超过其 max_running。选择最早接纳的可调度请求（并列按 request_id）；满额租户不阻塞其他租户。重试保持原接纳顺序。

GET /v1/requests/{request_id} 返回快照，至少含 `request_id,tenant,model_version,route_revision,status,attempts,created_ms,finished_ms,output_tokens,text,error`。未终结 finished_ms 为 null；无错误 error 为 null。attempts 是实际开始的 worker 尝试数。text 仅包含已持久化并向流订阅开放的 delta。快照和事件接口可包含生成文本；监控、审计和 inspect 导出必须省略 text/prompt/原始键。

POST /v1/requests/{id}/cancel 不需要请求体，返回快照：未终结者原子变为 CANCELLED，error=`cancelled`；已终结者保持原结果。截止时间 now_ms >= deadline_at_ms 时未终结者变为 EXPIRED，error=`deadline_exceeded`。并发终结以先提交的终态为准。取消/到期立即释放逻辑并发名额，worker 迟到事件不得产生 delta、账单或改变终态。

GET /v1/requests/{id}/events 返回 SSE，实时等待到终结。每个持久事件具有递增整数 id（从1起）、event（delta 或 terminal）、JSON data。delta data 为 `{text,token_count}`，terminal data 为 `{status,error}`。每请求恰有一个 terminal；重订阅可重放已提交事件，不承诺网络端到端恰好一次。`after` 查询参数或 Last-Event-ID 表示排除该 id 及以前的事件，query 优先；必须非负整数，否则400。终态且没有后续事件时干净结束。慢/断开的订阅者不能阻塞生成、取消或其他请求。

worker 断流/可重试错误只在本请求尚无持久 delta 且尝试数 < max_attempts 时重试。已出现 delta 后不得自动重新生成；保留已生成文本，进入 FAILED。协议错误不可重试。模型版本与 route_revision 在重试中固定。账单仅在 SUCCEEDED 时生成一次，含 `request_id,input_tokens,output_tokens`；失败、取消、到期没有成功账单。

强制终止后恢复：保留终态与事件。对上次 RUNNING，若已持久 delta，终结为 FAILED/error=`restart_after_output`；否则尚有尝试额度则恢复排队，额度耗尽则 FAILED/error=`retry_exhausted`。原 QUEUED 保留。先处理已到期请求。不能把恢复本身当作新尝试或重复生成账单。

## 旧状态兼容

须支持 fixtures/runtime/legacy-v1.sql 所描述的 user_version=1 数据库，保留旧请求 request_id、键作用域、指纹、模型版本、状态、生成事件、成功账单和时钟。旧请求没有 route_revision 时取0，缺失的 error 取null。迁移至 user_version=2；重复启动迁移幂等。新建库使用同一外部行为。现有 SQL 表名是旧输入格式，迁移后的内部表结构不受判分约束。
