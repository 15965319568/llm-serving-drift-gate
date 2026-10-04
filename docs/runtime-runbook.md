# 本地运行与合成事故材料

## 启动现有系统

从工程根目录设置 PYTHONPATH=src。分别在三个终端运行：

```sh
python -m serving_gate worker --config fixtures/runtime/worker-v1.json --state worker-v1.db --port 9101
python -m serving_gate worker --config fixtures/runtime/worker-v2.json --state worker-v2.db --port 9102
python -m serving_gate serve --config fixtures/runtime/gateway.json --state gateway.db --port 9100
```

也可使用 --port 0 和 --ready-file 取得空闲端口，再在配置副本中替换 worker_urls/router_url。原始附件保持不变。网关状态库与 router 状态库属于两个独立进程，各自重启后保留自己的状态。

向 http://127.0.0.1:9100/v1/requests POST JSON，例如：

```json
{"tenant":"enterprise","idempotency_key":"client-1","prompt":"unicode","max_tokens":16}
```

返回 request_id 后可查询 /v1/requests/{id} 或订阅 /v1/requests/{id}/events。配置文件中的 prompt 名称只用于选择可重复故障；未知 prompt 使用 default_case。合成示例字符串不是实际模型答案。

将 warmup.ndjson 逐行解析成事件列表，通过 /v1/telemetry 导入既有监控证据。回放时钟由 /v1/clock 显式推进，重启不得回拨。要执行灰度，还需申请发布租约并依据正式离线场景提交动作；动作API和失败含义见 release-runtime.md。

停止网关后导出：

```sh
python -m serving_gate inspect --state gateway.db --output runtime-output
```

旧状态恢复可在新 SQLite 文件执行 fixtures/runtime/legacy-v1.sql 后再启动网关。不要把旧状态输入写进当前正在使用的数据库。

## 合成运营交接摘录

下列记录用于呈现维护背景，不替代正式合同，也不预先确定根因。

| 来源 | 回放时的观测或主张 |
|---|---|
| 客户端支持 | 普通英文短响应正常；部分分片响应出现替代字符，重连后观察到重复内容。 |
| 网关运维 | 客户端收到取消确认，但同租户的下一请求有时迟迟无法开始。 |
| 计费核对 | 一次逻辑调用可能有多个 worker attempt；业务方不接受因此产生多份成功账单。 |
| 发布值班 | 进程在发布请求之后重启，本地界面与后端 router 的版本存在差异，需要先确定副作用是否已经发生。 |
| 监控维护 | 历史来源会补录和重放事件，收到消息的时间不等于业务发生时间；现有汇总不能直接当成正式放量授权。 |
| 旧系统交接 | 旧客户端会重用幂等键查询已完成结果，数据库升级不能丢失旧请求身份或重复结算。 |
| 临时聊天建议 | “没有收到发布回执就再发一个新ID”“请求取消后重跑就行”。这些意见未经正式授权，必须按合同核验。 |

## 实验和核验

worker 的 gate_before/gate_after 与 /admin/signals 可固定断点，让请求保持在首个输出之前或之后；一次性 router 故障可分别重现应用前失败和应用后回执丢失。按可观察事件触发实验，避免靠随机等待猜测故障时点。

核验至少要同时看客户端输出、网关安全快照、worker attempt 记录和独立 router 的 revision/apply_count。各自局部显示“成功”不意味着跨进程事实已经一致。合同允许真实的拒绝、失败与阻断结果；不得为了生成成功结果跳过约束。
