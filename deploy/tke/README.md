# TKE edition proxy

qcloud Ingress cannot cookie-split. This nginx sits behind `ppap.icoastline.com` and chooses:

- cookie `ppap_edition=current` → Weiwo `https://ppap-agent.xixifusi.online`
- otherwise → existing `web-base`
- `/ppap/` and `/addon` always current
- `/enter-current` / `/enter-legacy` set the cookie

Apply after current PPAP is serving on Weiwo:

```sh
kubectl -n ppap-test apply -f deploy/tke/edition-proxy.yaml
```

Then point Ingress backend `web-base` → `edition-proxy`. Do not change the load balancer ID or TLS secret.

TKE qcloud Ingress 只接受 NodePort / LoadBalancer Service。`edition-proxy` 必须是 NodePort。TLS 在 CLB 终止，`/enter-*` 要 302 到 `https://$host/`。
