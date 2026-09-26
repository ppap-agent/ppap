# Remote development against current TKE data services

`run.sh` keeps Web and API on this Mac and connects them to the current
`ppap-test` PostgreSQL and S3 bucket. It does not run migrations, bucket
initialization, or a local Worker. Existing production Workers consume any jobs
created here. This is live data, not an isolated test database.

The script reads current connection values and credentials from the TKE
ConfigMap/Secret into its process environment. It does not write those values
to a file or print them. Local `.env` still supplies Feishu and LLM settings.
The API uses the same tenant allowlist as the cluster and a `remote-dev`
profile: it uses real Feishu sign-in, but isolated test identities and their
cleanup endpoints are disabled.
Deep availability and executor revision follow the cluster configuration;
accepted Deep jobs are executed by the existing cluster Worker, not locally.

The local API disables URL access logs because Office download URLs contain
short-lived file tickets.

The default kubeconfig is the adjacent Hax TKE Headscale config. Set
`PPAP_REMOTE_DEV_KUBECONFIG` to a different kubeconfig path if needed.

The browser and API retain the existing localhost ports, so stop the isolated
stack first. Stopping it does not delete its database volume:

```sh
docker compose -f deploy/isolated-dev/compose.yaml stop
docker stop isolated-dev-onlyoffice-1
./deploy/remote-dev/run.sh up
```

For live React and Python source reload, use
`./deploy/remote-dev/run.sh up-live` instead. It reuses the isolated-dev Vite overlay; dependency changes
still require rebuilding the API image.

Open <http://127.0.0.1:4173/>. Keep the script running; Ctrl-C removes the
remote-dev containers and closes all tunnels. To restore the isolated stack:

```sh
docker compose -f deploy/isolated-dev/compose.yaml up -d --wait
```

`run.sh` makes two localhost-only `kubectl port-forward` connections for
PostgreSQL and S3. The temporary `ppap-remote-dev` Pod accepts an SSH reverse
forward through a third Kubernetes API port-forward. Its ClusterIP Service lets
the existing OnlyOffice download from this Mac's `/office/content` endpoint;
neither SSH nor the local API receives a public ingress. A dedicated SSH key
lives under the gitignored `.local/remote-dev` directory. Only the public key
is stored in Kubernetes. The reverse tunnel and local API stop together.

The shared OnlyOffice currently does not validate browser JWTs and permits
private-address downloads. A fresh local secret still signs PPAP's short-lived
file tickets. If the shared OnlyOffice policy changes, recheck those two
conditions before using remote-dev.

To remove the temporary Pod, Service, and public-key Secret after development:

```sh
./deploy/remote-dev/run.sh cleanup-bridge
```

Do not run this configuration against incompatible local code/schema, and do
not start a local Worker against the shared production job queue. The local
API can write live drafts, files, reviews, and jobs. Closing the tunnels does
not undo those writes.
