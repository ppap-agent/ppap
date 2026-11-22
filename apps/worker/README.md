# Engine Worker

PPAP 的异步执行进程。isolated-dev 的构建与启动见 [deploy/isolated-dev](../../deploy/isolated-dev/README.md)。Worker 是独立进程；停止 Worker 不应让 API `/health` 失败。

Worker 负责 `engine.jobs` 的 claim / lease / attempt fencing、文档准备与转换、执行 job 中冻结的 `ppap-checker-core` 与 Suite 快照（经 bounded UDS 调用 Deep Controller `python -m agent`），以及把结果写回产品轮次。它不持有 Docker socket，失败不回退其他执行器；不提供公开 HTTP 路由、登录或第二套审核台账。可复用审核逻辑放在 `packages`。

执行边界与深度模式见 [ARCHITECTURE](../../docs/ARCHITECTURE.md#执行)。
