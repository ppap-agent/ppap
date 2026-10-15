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

`run.sh` resolves the current git SHA into `PPAP_BUILD_SHA`, builds the API/Web images through the repository `docker-bake.hcl`, then starts Compose with `--no-build`. Remote Compose does not define a second build graph.

For live React and Python source reload, use
`./deploy/remote-dev/run.sh up-live` instead. It reuses the isolated-dev Vite overlay; dependency changes are picked up by rerunning the script, which rebuilds the canonical Bake targets.

Open <http://127.0.0.1:4173/>. Keep the script running; Ctrl-C removes the
remote-dev containers and closes all tunnels. To restore the isolated stack:

```sh
export PPAP_BUILD_SHA="${PPAP_BUILD_SHA:-local}"
docker buildx bake
docker compose -f deploy/isolated-dev/compose.yaml up --no-build -d --wait
```

`run.sh` makes two localhost-only `kubectl port-forward` connections for
PostgreSQL and S3. The temporary `ppap-remote-dev` Pod accepts an SSH reverse
forward through a third Kubernetes API port-forward. Its ClusterIP Service lets
the existing OnlyOffice download from this Mac's `/office/content` endpoint;
neither SSH nor the local API receives a public ingress. A dedicated SSH key
lives under the gitignored `.local/remote-dev` directory. Only the public key
is stored in Kubernetes. The reverse tunnel and local API stop together.

Remote-dev uses its own ONLYOFFICE Community 10.0 service, pinned by image digest
in `onlyoffice.yaml`. It does not replace the shared `le3` instance. `run.sh`
creates a separate JWT Secret once and reads it into the local API environment
without printing or saving its value. Both browser configuration and PPAP's
short-lived file tickets are signed. Macros stay disabled in the API config.

The service has no public ingress. A fourth localhost-only port-forward exposes
it at `http://127.0.0.1:8082`; close the local stack to close this access. Private
address downloads are needed to fetch exact originals through the existing API
bridge. Metadata-address downloads and WOPI are disabled. Only preview caches
live in this service; PPAP originals, audit rounds and reviews remain in their
existing stores. No shared database migration is involved.

The preview Deployment and its JWT Secret remain in the cluster when the local
stack stops. Local cleanup of the bridge does not delete this separate service.

To remove the temporary Pod, Service, and public-key Secret after development:

```sh
./deploy/remote-dev/run.sh cleanup-bridge
```

Do not run this configuration against incompatible local code/schema, and do
not start a local Worker against the shared production job queue. The local
API can write live drafts, files, reviews, and jobs. Closing the tunnels does
not undo those writes.
