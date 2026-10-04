# Serving Drift Gate — maintenance starter v5

Synthetic Python standard-library maintenance project for multi-tenant LLM inference serving. It includes an offline release gate, HTTP streaming gateway, SQLite persistence, worker protocol adapters, monitoring, and a versioned release controller.

This is an intentionally incomplete/defective starting point, not a production deployment or the reference repair. Normal ASCII requests can run; the published contracts describe the required behavior under retries, cancellations, historical evidence changes and recovery.

Read TASK.md and docs/runtime-runbook.md. Set PYTHONPATH=src and use Python 3.11+. The CPU workers require no model weights, GPU, credentials or external services. Raw fixtures and operational narratives are synthetic.

Private author reference solutions, acceptance tests, scoring and measured-model logs are not included in this repository. Internal implementation is free to change while the published interfaces and input contracts remain compatible.
