# PPAP

Target product repository for PPAP document audit.

This repo is the long-term home of:

- `apps/web` — one web client (PWA and Feishu H5 share it)
- `services/api` — the only public HTTP API

The audit engine stays in [`ppap-agent`](https://github.com/ppap-agent/ppap-agent). This API calls it as an internal worker. Do not expose engine `POST /tasks` to browsers.

Product language is in [`hax/products/ppap/CONTEXT.md`](https://github.com/ppap-agent/hax/blob/main/products/ppap/CONTEXT.md): 资料集, 文件版本, 审核轮次, 检查结果, 证据, 人工复核 (not 正式批准).

## Not this repo

| Repo | Role |
| --- | --- |
| `ppap-host` | Transition InsForge host. Do not grow it as the final API. |
| `ppap-web`, `ppap-uac`, `ppap-biz`, `ppap-foundation` | Production Java/Vue reference. Read-only. |
| `hax` | Product docs and runtime manuals. |

## Runtime

Two processes: `api` and `engine-worker`. One Postgres (two schemas) and one object bucket. Feishu app `cli_a961a27719b81cc9` remains the channel identity.
