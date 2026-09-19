# Deployment

## 当前 main

当前仓库提供 isolated-dev 运行方式：

```sh
python tools/stack.py up
```

以及可选的 `deploy/isolated-dev/compose.yaml` 基础设施。它们用于开发，不代表 production deployment 已完成。

## 目标生产组成

1. Web static application
2. API process
3. Worker process
4. PostgreSQL
5. S3-compatible object storage
6. 仅在当前 DOC 支持需要时存在的受限 converter sandbox

API 和 Worker 是两个核心应用进程；基础设施与可选安全 sandbox 不改变产品边界。

Production 必须外部注入 Secret，不能继承 isolated-dev credential 或 loopback endpoint。部署 artifact 必须能关联到明确 Git revision/build identity。

## Production delivery

真正的 production build、deploy、upgrade、backup、restore、rollback 命令由 #15 交付并验收。命令存在并实际运行之前，不在本文件编造 production runbook。

终态不得把 `ppap-host`、Java PPAP 服务或独立部署的 `ppap-agent` HTTP 作为永久 runtime dependency。
