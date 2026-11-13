# TKE current PPAP

qcloud Ingress cannot cookie-split. `edition-proxy` sits behind `ppap.icoastline.com`:

- cookie `ppap_edition=current` → in-cluster `ppap-current-web`
- otherwise → existing `web-base`
- `/ppap/` and `/addon` always current
- `/enter-current` / `/enter-legacy` go to current API (gated there)

Do not change the load balancer ID, TLS secret, or `ppap.api.xixifusi.online`.

TKE qcloud Ingress only accepts NodePort / LoadBalancer Service. `edition-proxy` stays NodePort.

`kustomization.yaml` owns the Kubernetes manifests. CI renders them with pinned Kustomize 5.6.0 through `render-current.sh` and validates them with kubeconform. Operators deploy with the native commands below.

## Alpha 入口边界

ppap-web 仍是未选择版本时的默认界面，不能把 edition proxy 的 `default legacy` 改为 current。新版选择入口 `/enter-current` 在登录和租户校验通过后，默认返回 `/tasks/new`。可用 `return_to=<同源完整地址>` 保留任务、报告或历史轮次深链；外部地址及指回版本选择入口的地址回退到 `/tasks/new`。旧 `/audit` 和合法 Draft／Round／报告深链继续兼容。`/enter-legacy` 仍选择旧版并返回 `/`。版本 cookie 不是任务或报告的授权凭据。

## 飞书回调切换（尚未执行）

新旧系统共用飞书应用时，不要求两个事件回调地址。当前阶段只准备新版代码与测试，不修改飞书控制台、旧 UAC、旧 Redis、现网 Secret 或请求路由，不执行本节切换。

切换前排查票据时，区分旧端来源与新版存储：

- 旧 UAC 的 `RedisUtils` 将 `feishu-app-ticket` 的键和值按 Java 序列化保存；同名普通字符串键可能是失效旧值。先核对 UAC 的实际 Redis 连接和数据库，再按二进制键读取并解码值。`redis-cli --scan` 可在序列化键的空字节处截断，不能用字符串扫描未命中证明票据不存在。
- 新版正常运行从自己的 PostgreSQL `product.app_tickets` 读取，不查询 UAC 或旧 Redis。本地客户端模式也须核对当前 API 连接的数据库；有票据记录不代表票据仍有效，须用同一应用的真实飞书接口验证。

切换前的已授权联调可只读共享 UAC 现网接收的同一应用票据，向飞书验证有效后初始化新版票据存储；不修改旧缓存，也不伪造事件时间。此过渡方式不接入新版业务代码，不建设 UAC／Redis 自动回退；它只证明当前票据可用，不证明新版已直接收到推送或完成连续更新验收。切换后由下述新版回调独立接收和更新。

正式发布获批后，同一切换窗口内核对以下内容：

1. 先部署匹配的代码与数据库迁移（含 `0033_feishu_ticket_order.sql`），在新版 API 注入现有应用的 ID、Secret 和 Verification Token。沿用当前 HTTPS + Verification Token 模式；若控制台启用了 Encrypt Key，必须先补齐并验证加密支持，不能临时清空加密策略绕过校验。
2. 记录旧事件回调、应用入口与 OAuth 重定向配置，用于回滚。在切换窗口先阻止新增业务进入旧端，并确认旧端依赖飞书的在途操作已收敛；做不到则不切回调。新版事件地址为 `https://ppap.icoastline.com/ppap/integrations/feishu/events`；既有 `/ppap/` 路由直接到新版 API，不依赖浏览器 cookie，也不经过 UAC。不要把 OAuth 地址当事件地址：新版 OAuth 为 `https://ppap.icoastline.com/ppap/login/feishu/callback`。先核对允许的重定向列表，再在控制台切换事件地址并完成 challenge。
3. 将飞书应用请求入口切到获批的新版入口，并核对旧书签、直接链接和客户端是否仍向旧端新增业务。现有 `/enter-current` 路由负责选择新版并回到 `/tasks/new`，仍执行租户名单与登录检查。不能只切浏览器 cookie，而让服务器事件仍进入旧端；也不能把 API `/ppap/` 根路径当作前端首页。
4. 新版内读取 `capabilities` 建立时间基线，显式请求一次飞书 `app_ticket/resend`，确认新事件实际进入新版、提交并更新时间，再做一次不产生业务效果的 `app_access_token` 交换。不得输出 ticket、Secret、access token 或完整请求体；重推 HTTP 200 只表示飞书受理，不证明新版收到。
5. 在后续自动推送中确认事件时间继续推进，并在 API 重启后再次读取/交换验证持久化。接收端按 provider 事件时间防止倒退；这依赖飞书同一应用的事件时间能反映新旧顺序。只收到 challenge、只导入一次票据或本地协议测试通过，都不能替代连续推送验收。保留旧 UAC 部署用于回滚，但切换后它不再续收票据，不能据此承诺新旧双活；本轮尚未执行切换，因此旧端现有回调和业务不变。

若切换失败，恢复已记录的旧事件地址和旧应用入口，核对旧端重新收到推送；不把新版票据反写旧 Redis，不删除新旧数据，不用转发层掩盖切换未完成。配置恢复本身不证明旧端已恢复，须读回验证。

`edition-proxy.conf` is the only nginx configuration authority. `kustomization.yaml` generates the ConfigMap with `configMapGenerator`; the generated name hash updates the Deployment volume reference and rolls the proxy when the config changes.

## Apply current stack

Secret `ppap-current` is not in git. Create it when absent, then update its managed keys from the confirmed production configuration on each release; creating it once is insufficient. It also contains passwords, Feishu secret and S3 keys, so never replace the entire Secret to update one capability. Host fields in the ConfigMap already point at in-cluster Services. Text-model credentials use Secret `ppap-current-text-model-v1`, key `PPAP_MODEL_TEXT_API_KEY`; API and Worker read it through explicit references. `PPAP_MODEL_TEXT_BASE_URL` and `PPAP_MODEL_TEXT_MODEL` belong to ConfigMap `ppap-current`. Updating only the Deep model does not switch rule extraction or other text calls. Existing images support the Codex Responses endpoint; model configuration changes require a process restart, not an image rebuild.

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

本节是现有栈的部署入口。部署直接使用 `kubectl`；不运行 Python 部署编排、不自动预加载镜像、不创建临时测试环境。默认先执行本次需要的更新，遇到实际错误再诊断。构建与部署分开，配置变更不触发构建。

以下命令在仓库根目录执行，需要 `kubectl`、Python 3 和 Mike Farah `yq` v4。设置集群连接：

```sh
export KUBECONFIG="$HOME/Projects/work/hax/runtime/tke-cls-6e71r2cz/headscale-ipv6.kubeconfig"
```

### 更新镜像或应用清单

每次发布同时同步最新的 ConfigMap、Secret 和容器配置引用，然后执行滚动更新。更新配置属于发布步骤，不能只更新镜像而遗漏新版本必需的凭据或端点。流程保持直接：**整理本次配置 → 按授权同步配置 → 应用清单／滚动更新**；失败后再诊断，不增加预演环境或完整预检。

配置来源使用用户本次指定或此前已确认的生产配置。现网由用户更新的模型、端点和 Secret 引用是有效变更，仓库旧默认值不能覆盖它们；先将这些最新值反映到本次部署清单。不要把开发 `.env` 全量当作生产配置，也不要用示例文件中的空值清除生产值。缺失项与明确删除是不同操作，删除或重命名必须有明确授权。

交互环境中，尚未获确认的配置变更可以在更新前展示并确认：公开参数列出旧值和新值；密钥只列 Secret 名、key 和是否变更，不展示值或散列。已获明确授权的配置不重复确认。非交互部署只自动同步已确认配置源中明确管理的 key，不猜测新凭据、不擅自切换模型。

当前配置归属：

| 配置项 | 存放位置 | 容器引用 |
|---|---|---|
| `PPAP_MODEL_TEXT_MODEL`、`PPAP_MODEL_TEXT_BASE_URL` | ConfigMap `ppap-current` | API、Worker；Deep 沿用其需要的公共配置 |
| `PPAP_MODEL_TEXT_API_KEY` | Secret `ppap-current-text-model-v1` | API、Worker、Deep Controller（Checker 试跑的文本调用经本地网关）|
| `PPAP_MODEL_CODING_*` | 当前未设置：Coding 使用文本模型 | 需要 Deep 专用端点时，新建 Secret 存放 `PPAP_MODEL_CODING_API_KEY` 并只在 Deep Controller 显式引用 |
| `PPAP_MODEL_VISION_MODEL`、`PPAP_MODEL_VISION_BASE_URL`、`PPAP_MODEL_VISION_EXTRA_BODY_JSON` | Secret `ppap-current` | API、Worker、Deep 的配置身份 |
| `PPAP_MODEL_VISION_API_KEY` | Secret `ppap-current` | API、Worker、Deep Controller；不向 Deep guest 注入 |
| `PPAP_TENANT_MODEL_ALLOWED_BASE_URLS`、`PPAP_DEEP_MODEL_MAX_REQUESTS` | ConfigMap `ppap-current` | API、Worker、Deep（企业 Key 的允许地址；每个 Deep Attempt 的请求次数熔断）|
| 企业 Key（企业 BYOK） | 表 `product.tenant_model_credentials`（密文） | 运营用 `python -m product.tenant_models` 设置；无客户接口 |

已有 Secret 使用按 key 合并更新，保留其余数据。例如，`PPAP_SECRET_ENV_FILE` 指向权限为 `0600`、不入 Git 的生产文件，且**只包含本次已确认要更新到 `ppap-current` 的 key**。下面的管道自动更新这些 key，不将密钥放入命令参数或输出：

```sh
set -euo pipefail
set +x
kubectl -n ppap-test create secret generic ppap-current \
  --from-env-file="$PPAP_SECRET_ENV_FILE" --dry-run=client -o json \
  | python3 -c 'import json, sys; json.dump({"data": json.load(sys.stdin)["data"]}, sys.stdout)' \
  | kubectl -n ppap-test patch secret ppap-current --type=merge --patch-file=/dev/stdin
```

Secret 不存在时，先用同一个已确认文件创建；其他 Secret 按表中归属分别更新，不把整份 `ppap-current-text-model-v1` 覆盖到 `ppap-current`。新配置 key 还必须出现在所属容器的明确 `secretKeyRef`／`configMapKeyRef` 中；只写入 Secret 而没有容器引用，同样属于未完成配置。

将本次镜像 digest、`PPAP_BUILD_SHA` 和非敏感配置写入 `current.yaml`，然后直接应用现有应用的 ConfigMap 与 Deployment：

```sh
kubectl kustomize deploy/tke \
  | yq 'select((.kind == "ConfigMap" and .metadata.name == "ppap-current") or (.kind == "Deployment" and (.metadata.name == "ppap-current-api" or .metadata.name == "ppap-current-worker" or .metadata.name == "ppap-current-web")))' \
  | kubectl -n ppap-test apply -f -
```

镜像变化会自动触发对应 Deployment 的滚动更新。命令成功表示更新已提交，不表示所有 Pod 已就绪。提交更新的目标时间为 20 秒内；下载镜像、调度和应用启动可能更久。

需要报告“部署完成”时，对受影响的 Deployment 各读取一次滚动结果；未受影响组件不检查。不把 `apply` 成功当成 Pod 已就绪，也不循环追加环境变量、日志或业务验收检查：

```sh
kubectl -n ppap-test rollout status deployment/ppap-current-worker --timeout=180s
kubectl -n ppap-test rollout status deployment/ppap-current-api --timeout=180s
kubectl -n ppap-test rollout status deployment/ppap-current-web --timeout=180s
```

API／Web readiness 和 Deep 本地通信探针由 Kubernetes 持续执行，不在发布命令中重复实现。Worker 当前没有独立 readinessProbe；Pod Ready 不等于已验证队列消费。仅在本次修改涉及消费链路或出现异常时检查 Worker 心跳。普通发布不调用真实模型或通知接口。


此 apply 命令只更新应用的 ConfigMap 与 Deployment；Secret 已按前述步骤单独同步。不运行初始化 Job、数据库迁移，也不修改旧版入口、Office 或存储卷。变更 Worker 协议或数据库结构时，按该变更的兼容要求安排更新顺序。

### 仅修改配置或重启

修改 `current.yaml` 后执行上面的 apply 命令。ConfigMap 和 Secret 的环境变量需要重建 Pod 才能生效；仅配置内容变化、尚未触发滚动时，直接重启受影响的进程：

```sh
kubectl -n ppap-test rollout restart deployment/ppap-current-worker deployment/ppap-current-api
```

只有 Web 配置变化时才重启 Web：

```sh
kubectl -n ppap-test rollout restart deployment/ppap-current-web
```

不重建镜像，不等待完整验收。修改 Secret 时使用明确的 key 或新版本 Secret，通过 `secretKeyRef` 引用；密钥不写入 Markdown、Git 或命令输出。更新凭据引用后同样会触发滚动更新。

清单变更已经触发滚动时，不再重复 `rollout restart`；配置内容未变化时不为同步配置额外重启。配置注入完成表示服务具备调用条件，真实服务可达性和识别效果是另一项验收，不在默认发布中调用模型。

### 出错后诊断

更新命令报错、应用不可用或用户报告异常时，按受影响组件读取状态和日志：

```sh
kubectl -n ppap-test get pods
kubectl -n ppap-test rollout status deployment/ppap-current-worker --timeout=60s
kubectl -n ppap-test logs deployment/ppap-current-worker -c worker --since=10m --tail=100
kubectl -n ppap-test logs deployment/ppap-current-api -c api --since=10m --tail=100
```

日志仅用于本地诊断，对外引用前去除密钥和原文。命令响应丢失时，先读取对应资源确认结果，再决定是否补做。不要自动重试审核受理、收费、通知等业务写操作。

需要回退时，将已知兼容的镜像和配置恢复到 `current.yaml`，再执行同一 apply 命令；仅配置回退后也要重启受影响进程。不要删除 PVC 或重建初始化 Job 来强行恢复。

### 构建镜像

构建工具只负责构建和写入四个镜像 pin，不操作部署。源文件与测试由正常 CI 负责，不在每次部署前同步重跑。

默认使用云效 Flow 5339663，在香港 Runner 构建并发布到私有 TCR。只在操作进程中注入 `ALIYUN_DEV_OPS_TOKEN`，凭据来源见 hax 环境配置。

```sh
python3 deploy/tke/build-current.py <full-source-sha>
# 已有构建继续读取同一次运行，不重复发起：
python3 deploy/tke/build-current.py <full-source-sha> --run-id <run-id>
```

构建使用固定 Codeup 分支 `codex/ppap-current-release`；完整 SHA 通过单次运行参数传入，作业检出该 commit 后再构建。分支后续移动不改变本次源码，流水线允许并发，不按提交创建分支。`yunxiao.yaml` 变更时同步云效定义一次；普通构建只启动和读取自己的运行，不修改共享流水线。构建复用 `docker-bake.hcl` 的四目标图和缓存，核对构建回执与镜像源码身份、digest 后写入 `current.yaml`；若清单在构建期间被改动则保留新内容，不覆盖其他操作的选择。提交这些 pin 后，使用上面的原生部署命令。

构建保持四组件统一源码 SHA，一次 Bake 共享步骤并按依赖调度；不增加按文件选择组件的发布分支。固定系统工具放在应用源码之前，Deep 的 npm 安装及签名检查只依赖锁文件，runner 测试在源码层执行。依赖校验和打包后的 import 检查继续保留。

云效公共 Runner 使用原生 Docker 驱动和 inline 缓存，导出到现有各组件的 `:build-cache` 镜像；缓存 tag 不能作为部署 pin。该节点实际拒绝 `docker-container` BuildKit 启动（`flow not support`），因此不启用依赖该驱动的 registry `mode=max` 中间阶段缓存，也不为缓存新增构建服务。固定依赖分层继续减少源码变更时的重装。临时 Runner 的本地 uv cache mount 不保证跨运行持久化；实际缓存收益由构建步骤耗时确认。

默认入口先验证该 SHA 的四个镜像；全部存在且身份正确则直接复用，不读取历史成功运行。仅有镜像明确缺失时查询活动流水线：同源码跟进原 run，不同源码活动时停止；历史运行通过 `--run-id` 显式恢复。查询使用云效 [ListPipelineRuns 的 RUNNING 过滤](https://help.aliyun.com/en/yunxiao/developer-reference/listpipelineruns)，不再逐条读取成功历史。

每次保留 digest／config 字节、平台及源码身份验证；仓库匿名访问检查移至 TCR 创建或访问权限变更时，由权限维护者确认仓库及镜像不能匿名拉取。日常构建只执行正常认证拉取，不重复申请匿名 token、探测公开权限。网络、认证或身份验证失败不当作“镜像不存在”，不能据此重新触发构建。


显式选择备用 unregistry 时，在已有 registry 登录的构建主机运行：

```sh
python3 deploy/tke/build-current.py <full-source-sha> --provider unregistry
```

工具先复用已存在且身份匹配的镜像，仅在确认镜像缺失时构建。未知写入结果先读回，不重复发起。云效拉取 Docker Hub 基础镜像时使用 `yunxiao.yaml` 中的镜像代理；这是构建传输设置，不是部署前检查。

## Deep 配置

现网 Deep 沿用 `PPAP_MODEL_TEXT_BASE_URL`、`PPAP_MODEL_TEXT_MODEL` 与 `PPAP_MODEL_TEXT_API_KEY`。只有需要 Deep 专用模型时才同时设置 `PPAP_MODEL_CODING_BASE_URL`、`PPAP_MODEL_CODING_MODEL`，并在 Deep Controller 显式引用 `PPAP_MODEL_CODING_API_KEY`；代码拒绝只有 Deep 密钥而没有 Deep 端点的配置。`PPAP_MODEL_CODING_REASONING_EFFORT` 属于 Deep。

Worker 与 Deep Controller 共享 `/run/ppap-deep` 的 UDS。配置变更后直接重启其 Deployment；UDS 可用只表示进程连通，不证明真实 Deep 审核成功。当前 `container-only` / sandbox `off` 的能力边界仍见 `deploy/deep/README.md`。
