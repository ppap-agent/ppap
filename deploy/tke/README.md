# TKE current PPAP

qcloud Ingress cannot cookie-split. `edition-proxy` sits behind `ppap.icoastline.com`:

- cookie `ppap_edition=current` → in-cluster `ppap-current-web`
- otherwise → existing `web-base`
- `/ppap/` and `/addon` always current
- `/enter-current` / `/enter-legacy` go to current API (gated there)

Do not change the load balancer ID, TLS secret, or `ppap.api.xixifusi.online`.

TKE qcloud Ingress only accepts NodePort / LoadBalancer Service. `edition-proxy` stays NodePort.

Validation and deployment consume the same rendered object stream from `deploy/tke/render-current.sh`. That script runs the pinned Kustomize 5.6.0 image against this directory. CI pipes the render to kubeconform; deployment first sends the same render through Kubernetes server-side dry-run and then applies that exact stream.

## 飞书回调切换（尚未执行）

新旧系统共用飞书应用时，不要求两个事件回调地址。当前阶段只准备新版代码与测试，不修改飞书控制台、旧 UAC、旧 Redis、现网 Secret 或请求路由，不执行本节切换。

正式发布获批后，同一切换窗口内核对以下内容：

1. 先部署匹配的代码与数据库迁移（含 `0033_feishu_ticket_order.sql`），在新版 API 注入现有应用的 ID、Secret 和 Verification Token。沿用当前 HTTPS + Verification Token 模式；若控制台启用了 Encrypt Key，必须先补齐并验证加密支持，不能临时清空加密策略绕过校验。
2. 记录旧事件回调、应用入口与 OAuth 重定向配置，用于回滚。在切换窗口先阻止新增业务进入旧端，并确认旧端依赖飞书的在途操作已收敛；做不到则不切回调。新版事件地址为 `https://ppap.icoastline.com/ppap/integrations/feishu/events`；既有 `/ppap/` 路由直接到新版 API，不依赖浏览器 cookie，也不经过 UAC。不要把 OAuth 地址当事件地址：新版 OAuth 为 `https://ppap.icoastline.com/ppap/login/feishu/callback`。先核对允许的重定向列表，再在控制台切换事件地址并完成 challenge。
3. 将飞书应用请求入口切到获批的新版入口，并核对旧书签、直接链接和客户端是否仍向旧端新增业务。现有 `/enter-current` 路由负责选择新版并回到 `/audit`，仍执行租户名单与登录检查。不能只切浏览器 cookie，而让服务器事件仍进入旧端；也不能把 API `/ppap/` 根路径当作前端首页。
4. 新版内读取 `capabilities` 建立时间基线，显式请求一次飞书 `app_ticket/resend`，确认新事件实际进入新版、提交并更新时间，再做一次不产生业务效果的 `app_access_token` 交换。不得输出 ticket、Secret、access token 或完整请求体；重推 HTTP 200 只表示飞书受理，不证明新版收到。
5. 在后续自动推送中确认事件时间继续推进，并在 API 重启后再次读取/交换验证持久化。接收端按 provider 事件时间防止倒退；这依赖飞书同一应用的事件时间能反映新旧顺序。只收到 challenge、只导入一次票据或本地协议测试通过，都不能替代连续推送验收。保留旧 UAC 部署用于回滚，但切换后它不再续收票据，不能据此承诺新旧双活；本轮尚未执行切换，因此旧端现有回调和业务不变。

若切换失败，恢复已记录的旧事件地址和旧应用入口，核对旧端重新收到推送；不把新版票据反写旧 Redis，不删除新旧数据，不用转发层掩盖切换未完成。配置恢复本身不证明旧端已恢复，须读回验证。

`edition-proxy.conf` is the only nginx configuration authority. `kustomization.yaml` generates the ConfigMap with `configMapGenerator`; the generated name hash updates the Deployment volume reference and rolls the proxy when the config changes.

## Apply current stack

Secret `ppap-current` is not in git. Create it once from the production env file (passwords, LLM, Feishu secret, S3 keys). Host fields in the ConfigMap already point at in-cluster Services.

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

```sh
export KUBECONFIG="$HOME/Projects/work/hax/runtime/tke-cls-6e71r2cz/headscale-ipv6.kubeconfig"
./deploy/tke/apply-current.sh
```

`apply-current.sh` packages the numbered migration files plus the canonical `migrations/apply.sh` runner into the migration ConfigMap. The filenames are the only migration ordering authority. All workload objects, Services, PVCs, application ConfigMap, and edition-proxy objects are rendered from the canonical Kustomize target. The script performs `kubectl apply --dry-run=server` before the real apply, then waits for the existing Deployments and init Jobs and verifies the expected Services.

No `nodeSelector`. API, Worker, Web, and Deep images must use the same current Git SHA; do not apply the manifest while any referenced image tag is missing. A Deep-only change does not require deleting or re-running the DB/bucket init Jobs.

Build the existing `ppap-worker:<git_sha>` image from this source, then build `ppap-deep:<git_sha>` with `--platform linux/amd64 --build-arg PPAP_APP_IMAGE=ppap-worker:<git_sha> -f deploy/deep/Dockerfile`. Push the Deep image to `unregistry.xixifusi.online/ppap-deep:<git_sha>`, update the four app image tags and `PPAP_BUILD_SHA` in `current.yaml` together, then apply. Preserve the existing Secret, PostgreSQL, Silo, queue, and shared UDS; this is not a new platform or data migration.

The Worker Pod has a dedicated `ppap-deep` sidecar (`PPAP_SERVICE=deep` → `python -m ppap_deep`) and the existing `ppap-worker` container. They share only `emptyDir` `/run/ppap-deep` for the Controller socket. The root Controller reads authorized DB/S3 input and owns a loopback Responses gateway. Each semantic guest drops to UID/GID 10001 with a fresh environment; the provider key is not passed to it. `PPAP_DEEP_EXECUTOR_REVISION=ppap-deep-v2`, `PPAP_DEEP_RUNTIME=container-only`, and `PPAP_DEEP_SANDBOX_PROFILE=off` are explicit. On this kernel, `off` means container-level isolation only: it does not give strong guest filesystem or egress isolation. There is no Docker socket or gVisor dependency. Worker waits up to 60 seconds for the UDS; if unavailable, it logs a warning and still starts so Standard jobs continue. Deep jobs fail explicitly when the Controller is unavailable; they never fall back to Standard.
