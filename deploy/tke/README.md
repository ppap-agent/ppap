# TKE current PPAP

qcloud Ingress cannot cookie-split. `edition-proxy` sits behind `ppap.icoastline.com`:

- cookie `ppap_edition=current` → in-cluster `ppap-current-web`
- otherwise → existing `web-base`
- `/ppap/` and `/addon` always current
- `/enter-current` / `/enter-legacy` go to current API (gated there)

Do not change the load balancer ID, TLS secret, or `ppap.api.xixifusi.online`.

TKE qcloud Ingress only accepts NodePort / LoadBalancer Service. `edition-proxy` stays NodePort.

Validation and deployment consume the same rendered object stream from `deploy/tke/render-current.sh`. That script runs the pinned Kustomize 5.6.0 image against this directory. CI pipes the render to kubeconform; deployment first sends the same render through Kubernetes server-side dry-run and then applies that exact stream.

## Alpha 入口边界

ppap-web 仍是未选择版本时的默认界面，不能把 edition proxy 的 `default legacy` 改为 current。新版选择入口 `/enter-current` 在登录和租户校验通过后，默认返回 `/workbench`。可用 `return_to=<同源完整地址>` 保留任务、报告或历史轮次深链；外部地址及指回版本选择入口的地址回退到 `/workbench`。旧 `/audit` 和合法 Draft／Round／报告深链继续兼容。`/enter-legacy` 仍选择旧版并返回 `/`。版本 cookie 不是任务或报告的授权凭据。

卡片确认由 API 在 `engine.jobs` 中持久记录 `task_confirmation`，再执行既有受理事务；回调的“正在核对”不代表审核已开始。发布此能力时，先升级 Worker，使其不领取 `task_confirmation`，再升级 API。旧 Worker 不能消费 API 新增的确认工作。本地客户端连接共享数据库时，不对旧 Worker 创建此类验收工作；使用同候选的隔离数据库或 CI 验证。此发布顺序不授权切换旧飞书回调，通知发送开关也不控制已点击确认请求的处理。

这是获批的代码边界，不代表本轮执行了集群发布、飞书入口或事件回调切换。以下生产切换步骤仍需单独放行。

## 飞书回调切换（尚未执行）

新旧系统共用飞书应用时，不要求两个事件回调地址。当前阶段只准备新版代码与测试，不修改飞书控制台、旧 UAC、旧 Redis、现网 Secret 或请求路由，不执行本节切换。

切换前排查票据时，区分旧端来源与新版存储：

- 旧 UAC 的 `RedisUtils` 将 `feishu-app-ticket` 的键和值按 Java 序列化保存；同名普通字符串键可能是失效旧值。先核对 UAC 的实际 Redis 连接和数据库，再按二进制键读取并解码值。`redis-cli --scan` 可在序列化键的空字节处截断，不能用字符串扫描未命中证明票据不存在。
- 新版正常运行从自己的 PostgreSQL `product.app_tickets` 读取，不查询 UAC 或旧 Redis。本地客户端模式也须核对当前 API 连接的数据库；有票据记录不代表票据仍有效，须用同一应用的真实飞书接口验证。

切换前的已授权联调可只读共享 UAC 现网接收的同一应用票据，向飞书验证有效后初始化新版票据存储；不修改旧缓存，也不伪造事件时间。此过渡方式不接入新版业务代码，不建设 UAC／Redis 自动回退；它只证明当前票据可用，不证明新版已直接收到推送或完成连续更新验收。切换后由下述新版回调独立接收和更新。

正式发布获批后，同一切换窗口内核对以下内容：

1. 先部署匹配的代码与数据库迁移（含 `0033_feishu_ticket_order.sql`），在新版 API 注入现有应用的 ID、Secret 和 Verification Token。沿用当前 HTTPS + Verification Token 模式；若控制台启用了 Encrypt Key，必须先补齐并验证加密支持，不能临时清空加密策略绕过校验。
2. 记录旧事件回调、应用入口与 OAuth 重定向配置，用于回滚。在切换窗口先阻止新增业务进入旧端，并确认旧端依赖飞书的在途操作已收敛；做不到则不切回调。新版事件地址为 `https://ppap.icoastline.com/ppap/integrations/feishu/events`；既有 `/ppap/` 路由直接到新版 API，不依赖浏览器 cookie，也不经过 UAC。不要把 OAuth 地址当事件地址：新版 OAuth 为 `https://ppap.icoastline.com/ppap/login/feishu/callback`。先核对允许的重定向列表，再在控制台切换事件地址并完成 challenge。
3. 将飞书应用请求入口切到获批的新版入口，并核对旧书签、直接链接和客户端是否仍向旧端新增业务。现有 `/enter-current` 路由负责选择新版并回到 `/workbench`，仍执行租户名单与登录检查。不能只切浏览器 cookie，而让服务器事件仍进入旧端；也不能把 API `/ppap/` 根路径当作前端首页。
4. 新版内读取 `capabilities` 建立时间基线，显式请求一次飞书 `app_ticket/resend`，确认新事件实际进入新版、提交并更新时间，再做一次不产生业务效果的 `app_access_token` 交换。不得输出 ticket、Secret、access token 或完整请求体；重推 HTTP 200 只表示飞书受理，不证明新版收到。
5. 在后续自动推送中确认事件时间继续推进，并在 API 重启后再次读取/交换验证持久化。接收端按 provider 事件时间防止倒退；这依赖飞书同一应用的事件时间能反映新旧顺序。只收到 challenge、只导入一次票据或本地协议测试通过，都不能替代连续推送验收。保留旧 UAC 部署用于回滚，但切换后它不再续收票据，不能据此承诺新旧双活；本轮尚未执行切换，因此旧端现有回调和业务不变。

若切换失败，恢复已记录的旧事件地址和旧应用入口，核对旧端重新收到推送；不把新版票据反写旧 Redis，不删除新旧数据，不用转发层掩盖切换未完成。配置恢复本身不证明旧端已恢复，须读回验证。

`edition-proxy.conf` is the only nginx configuration authority. `kustomization.yaml` generates the ConfigMap with `configMapGenerator`; the generated name hash updates the Deployment volume reference and rolls the proxy when the config changes.

## Apply current stack

Secret `ppap-current` is not in git. Create it once from the production env file (passwords, Feishu secret, S3 keys). Host fields in the ConfigMap already point at in-cluster Services. Text-model credentials use Secret `ppap-current-text-model-v1`, key `LLM_API_KEY`; API and Worker read it through explicit references. `LLM_BASE_URL` and `LLM_MODEL` belong to ConfigMap `ppap-current`. Updating only the Deep model does not switch rule extraction or other text calls. Existing images support the Codex Responses endpoint; model configuration changes require a verified rollout, not an image rebuild.

API reads only its API database password; Worker and Deep read only the Worker password. Runtime containers must use explicit `secretKeyRef` entries, not `envFrom` for the whole Secret. Bootstrap and migrator credentials belong to database initialization, not application processes. Adding a new secret setting requires an explicit reference in its owning container.

ONLYOFFICE Community 10.0 has a separate Deployment, ClusterIP Service and Secret
`ppap-current-office`; it does not replace the legacy instance or depend on
remote-dev. Create its JWT Secret once, without putting its value in Git:

```sh
openssl rand -hex 48 | tr -d '\n' | kubectl -n ppap-test create secret generic ppap-current-office --from-file=jwt-secret=/dev/stdin
```

The API and Document Server read the same key through `secretKeyRef`. The browser
uses `https://ppap.icoastline.com/office`; edition-proxy forwards that virtual path
including WebSocket upgrades independently of the edition cookie. Document Server
downloads exact originals from `http://ppap-current-api:8080/office/content` using
short-lived signed tickets. No Mac, port-forward, new DNS name or TLS certificate
is needed. Only preview caches are ephemeral; originals remain in PPAP storage.

## Authoritative release procedure

This page and `release-current.py` own the existing-stack release procedure. Platform access facts remain in hax `runtime/tke-cls-6e71r2cz/YUNXIAO-FACTS.md`; do not copy this procedure there. Source/build authority is `docker-bake.hcl`; Kubernetes authority is `kustomization.yaml` through `render-current.sh`. The procedure does not initialize a new cluster or authorize a customer-entry/callback switch.

| Route | Build | Image repository | Priority |
|---|---|---|---|
| Yunxiao | Versioned `yunxiao.yaml`, Flow 5339663, Hong Kong runner | private TCR `ppap-feishu/current-{api,worker,web,deep}` | Default |
| unregistry | Same Bake `release` graph on the registry build host | `unregistry.xixifusi.online/ppap-{api,worker,web,deep}` | Explicit backup |

There is no automatic route switch after failure. Both routes build the four `linux/amd64` targets in one Bake graph; Deep consumes that graph's Worker target. GHCR application publishing and node-to-node archive transfer are removed. GHCR tool dependencies such as uv and kubeconform remain.

### Build and review

Use a clean checkout containing the selected full commit and Python 3.12+, Git, `gh`, Docker/Buildx and `kubectl`. Set `KUBECONFIG` for this cluster. `gh` must read the PPAP repository and its Actions runs. Build requires an available full source commit and can run in parallel with CI. Deployment requires a successful latest `Tests` push/manual run for that exact SHA; a PR merge-ref test alone is insufficient. Do not substitute the current branch HEAD or a configuration commit for the selected application source.

For the default route, supply `ALIYUN_DEV_OPS_TOKEN` only in the operator process. The hax environment file owns that credential. The script never prints it or stores it in Git. TCR verification reuses the existing `web-base` pull Secret binding. Flow uses its existing Codeup and TCR service connections, not the operator PAT in build logs.

```sh
python3 deploy/tke/release-current.py build <full-source-sha>
```

Routine use is **build the selected SHA, then deploy the reviewed pins**. Build does not query or wait for CI. Deploy reuses the existing successful exact-SHA CI; neither operation launches another test workflow.

The build operation pushes only a new `codex/ppap-current-release-<full-sha>` ref in the private Codeup `ppap-web` repository; it never forces a ref or changes `master`. Existing history makes later pushes incremental. The script updates only the dedicated build-only Flow from `yunxiao.yaml`, with manual triggers and a source identity check. Do not run two operators against this pipeline concurrently. Matching successful/running builds are reused across Flow history pages; a failed build never permits deployment. New Yunxiao builds import/export [inline cache](https://docs.docker.com/build/cache/backends/inline/) through each component’s separate `build-cache` tag. That tag uses the same image blobs and is never a deployment pin. A first-build cache miss is allowed. The application Dockerfile installs OS packages before copying source, so source edits do not invalidate that layer. Registry manifest/config checks run in parallel and do not download application layers. Resume a known run without reconfiguration or dispatch:

```sh
python3 deploy/tke/release-current.py build <full-source-sha> --run-id <run-id>
```

The script verifies the successful run's actual source, four emitted digests, private TCR access, manifest/config hashes, platform, revision label and baked SHA. Only then does it update the four immutable image pins and `PPAP_BUILD_SHA` in `current.yaml`. Review and commit that diff in the authorized PR. A later configuration commit does not change the application source identity. Build never applies Kubernetes objects.

Yunxiao pulls Docker Hub base images through `docker.m.daocloud.io` using Bake named contexts. The context keys retain the exact Dockerfile image versions and any existing digest; GHCR tool images and the Deep-to-Worker build dependency are unchanged. This changes base-image transport only: builds still run in Yunxiao and publish to private TCR. A mirror failure stops the build rather than switching the release provider. When a Docker Hub `FROM` reference changes, update its matching context in `yunxiao.yaml`.

For backup, run the same entrypoint **on the unregistry build host** with the selected commit and existing local Docker registry access:

```sh
python3 deploy/tke/release-current.py build <full-source-sha> --provider unregistry
```

It first reuses the four images if their existing SHA tags verify. Only confirmed missing images cause a build; an authentication, transport or identity error stops. A build archives tracked source only, uses the canonical four-target graph and pushes through `localhost:5001`. It verifies the public registry and writes immutable public digest pins. Do not add a developer-machine image relay. Existing full-SHA unregistry tags remain accepted for rollback, with the same source/image identity checks and automatic full-check selection. A registry switch requires full checks. Keep previous images and configuration until rollback responsibility ends.

### Deploy and read back

Deploy only with explicit release authorization and reviewed `current.yaml` pins:

```sh
./deploy/tke/apply-current.sh
# Same operation: python3 deploy/tke/release-current.py deploy
```

The default routine path is short:

1. Reuse exact-SHA Tests, verify four registry identities in parallel, and check schema/in-flight jobs. No test workflow or build is started.
2. Render once and server-dry-run once. Only the application ConfigMap and API/Web/Worker Deployments may change. Apply its application ConfigMap and Worker Deployment first and wait for the Worker rollout before applying the complete fixed render, so API cannot enqueue new confirmation work to an old Worker. Each scheduled Kubelet pulls directly with `IfNotPresent` and reuses existing layers. If nothing changes, skip apply and rollout waiting.
3. Wait for application rollouts in parallel, then verify actual image IDs, Ready/restarts, public API/Web SHA, readiness, fresh Worker heartbeat, Deep socket, schema and the unchanged default legacy page. Routine checks do not claim a full historical-data comparison.

Full checks run automatically when the live-to-target diff is unknown, runtime/registry or non-model configuration changes, a live Deployment is not ready, or a same-source image is replaced. A proven source/test/doc-only diff with unchanged deployment runtime uses the routine path. A pin-record commit in `current.yaml` does not force full checks by itself: actual rendered configuration is compared with the live runtime. Dockerfiles, lockfiles, dependency manifests and unfamiliar paths require full checks. To force them for an environment investigation:

```sh
./deploy/tke/apply-current.sh --full-check
```

Full checks add direct pulls/CRI identities and isolated `Never` startup on both nodes in parallel, followed by fresh read-only history/queue and non-target resource baselines. Both nodes need a free Pod slot. Preloading proceeds directly without a fixed free-space threshold or a separate DiskPressure gate; actual image-pull and startup failures are reported through Kubernetes. Temporary Pod-local PostgreSQL/Silo and an empty queue prevent production data, model, notification or charge probes. Cleanup must succeed before apply. After slow checks the script dry-runs again and compares full baselines after rollout; concurrent legitimate writes require reconciliation.

Every path requires the migration payload/schema to match the numbered files. Schema upgrades remain a separately reviewed #15 operation with backups and stopped writes; completed init Jobs are not deleted or treated as proof that new migrations ran. Active business jobs block rollout. Notification jobs are never reset or resent.

Database preflight and history readback use the existing PostgreSQL Pod, with `SET LOCAL ROLE ppap_api` inside a read-only, repeatable-read transaction. They do not require an API Pod to be running. A migration that requires stopping old API credential consumers can therefore keep them stopped until the reviewed Worker-first release starts the new API. This does not perform migrations or relax schema, image, CI or post-release API checks.

No nodeSelector, node service/network reconfiguration, workload eviction or image pruning is part of release. The procedure keeps existing Secrets, PVCs, TLS/LB, default legacy route, Office and old UAC/Feishu callbacks. A healthy socket proves startup only, not a real Deep audit or stronger isolation.

Each invocation prints a private temporary operation directory. It contains only recovery identity, hashes, the fixed render and readback results, not credentials or full environment/Secret dumps. If dispatch/apply response is lost, **do not rerun the mutation blindly**. Inspect Flow runs and resume the exact run, or read back the saved deployment attempt:

```sh
python3 deploy/tke/release-current.py verify --attempt <printed-operation-directory>
```

A failed preload cleans up its own temporary resources and prevents apply. Cleanup failure prints the exact resource name and blocks rollout. A partial apply is not rolled back automatically: inspect live state, reconcile writes and schema compatibility, then explicitly authorize any correction/rollback. Use the previous reviewed config through this same deploy path, never delete/recreate PVCs or completed init Jobs to force progress. Remove operation directories only after reconciliation is complete.

When `PPAP_PIP_INDEX_URL` is set, builds retain the frozen lockfile, package hashes and dependency checks. Optional Debian mirrors must serve both Debian and its `-security` sibling. These are transport settings, not permission to change locked dependencies.

## Deep runtime boundary

The Worker Pod has a dedicated `ppap-deep` sidecar (`PPAP_SERVICE=deep` → `python -m agent`) and the existing `ppap-worker` container. They share only `emptyDir` `/run/ppap-deep` for the Controller socket. The root Controller reads authorized DB/S3 input and owns a loopback Responses gateway. Each semantic guest drops to UID/GID 10001 with a fresh environment; the provider key is not passed to it. `PPAP_DEEP_EXECUTOR_REVISION=ppap-deep-v2`, `PPAP_DEEP_RUNTIME=container-only`, and `PPAP_DEEP_SANDBOX_PROFILE=off` are explicit. On this kernel, `off` means container-level isolation only: it does not give strong guest filesystem or egress isolation. There is no Docker socket or gVisor dependency. Worker waits up to 60 seconds for the UDS; if unavailable, it logs a warning and still starts so Standard jobs continue. Deep jobs fail explicitly when the Controller is unavailable; they never fall back to Standard.

Deep readiness requires the Controller socket. Core `/ready` checks only PostgreSQL and object storage; neither check proves a real Deep audit. After rollout, also inspect container restart counts.

## Deep configuration releases

The canonical renderer adds `ppap.io/configuration-digest` to application Pod templates. Non-secret application configuration rolls API and Worker; Web only tracks its public API address. Updating a fixed-name ConfigMap alone is insufficient for process environment changes.

Deep uses `PPAP_DEEP_LLM_BASE_URL`, `PPAP_DEEP_LLM_MODEL`, and `PPAP_DEEP_REASONING_EFFORT` (default `medium`). API and Worker compute this non-secret execution policy without provider credentials. Only the Deep Controller receives `PPAP_DEEP_LLM_API_KEY`, from the separately versioned Secret `ppap-current-deep-model-v1`. Rotate credentials by creating a new Secret version and updating that reference; never put a key or its hash in the ConfigMap or a Pod annotation. Credential rotation does not change execution semantics.

An unchanged source and identical images with only these supported model settings changed can use the routine release checks. Unknown changes, dependency or image changes, readiness failures and other runtime changes retain full checks. All releases still require exact-source CI, immutable image verification, schema and queue preflight, server dry-run, Worker-first rollout, and actual configuration readback. The release verifies original process environment in each application container, the Controller's loaded policy over its existing UDS, and the installed Pi version. Stale Pods or differing policies cannot report success. Model/effort changes alter the frozen execution revision; old tasks are not rewritten.
