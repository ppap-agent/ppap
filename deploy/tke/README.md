# TKE current PPAP

qcloud Ingress cannot cookie-split. `edition-proxy` sits behind `ppap.icoastline.com`:

- cookie `ppap_edition=current` → in-cluster `ppap-current-web`
- otherwise → existing `web-base`
- `/ppap/` and `/addon` always current
- `/enter-current` / `/enter-legacy` go to current API (gated there)

Do not change the load balancer ID, TLS secret, or `ppap.api.xixifusi.online`.

TKE qcloud Ingress only accepts NodePort / LoadBalancer Service. `edition-proxy` stays NodePort.

## Apply current stack

Secret `ppap-current` is not in git. Create it once from the production env file (passwords, LLM, Feishu secret, S3 keys). Host fields in the ConfigMap already point at in-cluster Services.

```sh
export KUBECONFIG="$HOME/Projects/work/hax/runtime/tke-cls-6e71r2cz/headscale-ipv6.kubeconfig"
kubectl -n ppap-test create configmap ppap-current-migrations \
  --from-file=migrations --dry-run=client -o yaml | kubectl apply -f -
kubectl -n ppap-test apply -f deploy/tke/current.yaml
# after postgres/silo/api/web are Ready:
kubectl -n ppap-test apply -f deploy/tke/edition-proxy.yaml
```

No `nodeSelector`. App images are `unregistry.xixifusi.online/ppap-{api,worker,web}:<git_sha>`. Re-run init Jobs with `kubectl -n ppap-test delete job ppap-current-init-db ppap-current-init-bucket` then apply again.
