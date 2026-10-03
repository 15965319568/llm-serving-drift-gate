# LLM Serving Release Gate — starter v4

This repository is the initial codebase for an offline LLM inference release-control engineering task. It includes a runnable Python CLI and readers for serving telemetry, evaluation results and operational evidence.

The task is to repair and extend the release gate so that its decisions, tenant routing and operational handoff can be reproduced from the supplied evidence. The complete requirements, synthetic inputs, output contracts and evaluation environment are distributed in the Harbor task package.

## Run in the supplied task workspace

```bash
PYTHONPATH=src python -m serving_gate --input fixtures --output output
```

Python 3.11 or newer is required. No GPU or third-party Python runtime dependencies are needed. The `fixtures` directory is supplied by the task environment and is not part of this public starter.

The CLI entry point and arguments are stable. The current implementation is a starting point and does not meet the full release-control contract. Implementations may refactor modules while preserving the documented public interfaces.

Reference solutions, verifier tests, private input bundles, expected results and model-evaluation credentials are not published here.
