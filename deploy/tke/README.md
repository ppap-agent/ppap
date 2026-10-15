# TKE current PPAP

qcloud Ingress cannot cookie-split. `edition-proxy` sits behind `ppap.icoastline.com`:

- cookie `ppap_edition=current` → in-cluster `ppap-current-web`
- otherwise → existing `web-base`
- `/ppap/` and `/addon` always current
- `/enter-current` / `/enter-legacy` go to current API (gated there)

Do not change the load balancer ID, TLS secret, or `ppap.api.xixifusi.online`.

TKE qcloud Ingress only accepts NodePort / LoadBalancer Service. `edition-proxy` stays NodePort.

Validation and deployment consume the same rendered object stream from `deploy/tke/render-current.sh`. That script runs the pinned Kustomize 5.6.0 image against this directory. CI pipes the render to kubeconform; deployment first sends the same render through Kubernetes server-side dry-run and then applies that exact stream.

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
