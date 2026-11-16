# Metrics server ownership cutover

This PR stages Flux ownership; it does **not** complete the live migration.
`ks.yaml` is deliberately suspended. Merging only creates the suspended Flux
Kustomization and changes the desired Ansible configuration. Git/Flux does not
write host configuration or restart k3s. Do not remove the suspension until
all checks below pass. Do not let Helm adopt the packaged objects.

## Evidence and sizing

Read-only checks on 2026-11-16 found the `metrics-server-deployment` AddOn in
`kube-system` owns the Deployment, running
`rancher/mirrored-metrics-server:v0.9.0` with requests of 100m CPU and 70Mi
memory and no memory limit. The APIService was Available.

VictoriaMetrics queries through `vmauth-readonly`:

```promql
max_over_time(container_memory_working_set_bytes{namespace="kube-system",pod=~"metrics-server-.*",container="metrics-server"}[24h])
max_over_time(container_memory_rss{namespace="kube-system",pod=~"metrics-server-.*",container="metrics-server"}[24h])
```

The maxima were 94,171,136 bytes (89.8Mi working set) and 53,125,120 bytes
(50.7Mi RSS). A 256Mi limit leaves about 2.85 times the observed working set;
requests stay at 100m/70Mi. This is a one-day sample, not a future peak guarantee;
review usage and OOM/restart counts after cutover and as the cluster grows.

The Docker Hub registry manifest for the existing Rancher v0.9.0 tag returned
multi-platform digest
`sha256:d9862115e7c7881280d3d75ca26bda8ffc0fc213315979575bf23ce9826205c0`,
including linux/arm64/v8 digest
`sha256:d0341e07a1cf2b751e684cba68e2a0c62d3c5d2d23501fd67dc384d517ec65de`.
Pin the index so Renovate can maintain the same image. No version is changed.

## Phase 1: merge the staged configuration

Merge this PR with `spec.suspend: true` intact. Check the Flux Kustomization is
suspended and no metrics-server HelmRelease has been created. Reserve a
maintenance window and prepare the small activation PR before starting host
work, but leave it unmerged. No new OpenBao secrets are required.

## Phase 2: operator disables the packaged component

These are coordinated **manual operator steps**, not commands executed by this
PR or by Flux. K3s `--disable=metrics-server` actively uninstalls the packaged
Deployment, ServiceAccount, RBAC, Service and APIService. Removing files or using
a `.skip` file alone does not uninstall them. Keep Flux suspended throughout.

1. Apply the merged `server_config_yaml` through the existing Ansible site
   workflow (`mise run ansible-site -- --limit server`) during the maintenance
   window. This writes `/etc/rancher/k3s/config.yaml` and restarts servers;
   it is not a dry run. Use the pinned k3s version, without upgrading it.
2. Update/restart **all three servers**, one at a time: `turingpi-rk1-1`,
   `turingpi-rk1-2`, `turingpi-rk1-3`. The task uses `--forks=1`; check server
   health/API availability and maintain etcd quorum between restarts. The
   raspberrypi is an agent and does not deploy packaged components.
3. Privately verify the `disable` list includes `metrics-server` on every
   server and the running service has restarted with that configuration.
   Do not publish full host config, which includes credentials. Verify no
   CLI flags or config drop-ins override it. If the workflow stops halfway,
   keep Flux suspended and finish host consistency before proceeding.
4. Wait for k3s removal to complete. Read-only `kubectl get` checks must show
   these AddOns gone from `kube-system`: `metrics-server-deployment`,
   `metrics-server-service`, `metrics-apiservice`, `aggregated-metrics-reader`,
   `auth-delegator`, `auth-reader`, `resource-reader`.
   Confirm their owned objects are gone as well: Deployment/ServiceAccount/
   Service `metrics-server`, RoleBinding `metrics-server-auth-reader`,
   ClusterRoles `system:metrics-server` and `system:aggregated-metrics-reader`,
   ClusterRoleBindings `system:metrics-server` and
   `metrics-server:system:auth-delegator`, APIService `v1beta1.metrics.k8s.io`.
   Do not activate on partial deletion or lingering terminating resources;
   diagnose k3s cleanup first. Do not delete shared authentication roles or
   the separate k3s `rolebindings` AddOn.

The metrics API and `kubectl top` will be unavailable between packaged removal
and Flux readiness. HPAs using resource metrics cannot calculate fresh scaling
recommendations and may report missing metrics; existing workloads keep running.
Plan sufficient capacity for that interval, including the activation PR's merge
and normal Flux reconciliation delays.

## Phase 3: activate through a follow-up PR

After **every** server is consistent and cleanup checks pass, merge the prepared
follow-up PR removing `spec.suspend: true` from `ks.yaml`. Flux then creates the
RBAC, APIService and app-template HelmRelease. Names are recreated after removal,
not adopted in place. The Service selector is generated with its Deployment;
the APIService and RBAC point to the fixed `metrics-server` names.

Wait for the Kustomization and HelmRelease to become Ready, the Deployment to
have an available replica, and `v1beta1.metrics.k8s.io` to be Available. Check
`kubectl top nodes`, `kubectl top pods -A`, HPA conditions, kubelet scrape errors,
and container memory/restarts. Confirm the v0.9.0 image digest and 100m/70Mi
requests with the 256Mi limit. Verify packaged AddOns stay absent after all
servers have restarted; never re-enable the package alongside Flux.

## Recovery

Before activation, leave Flux suspended if host work fails; recover server
health and complete cleanup before retrying. If packaged metrics must be
restored, revert the disable entry in Git and have the operator consistently
apply/restart all servers, while Flux remains suspended.

After activation, prefer fixing the Flux app. Returning to k3s requires a
separate Git change removing the Flux app and its resources, waiting for Helm
uninstall and Flux pruning to finish, and only then reverting the disable entry
in Git and applying/restarting all servers manually. Suspension alone does not
uninstall Flux resources. Never run both owners together.

## Upstream references

- [K3s packaged components: disable uninstalls; server files are not synchronized](https://docs.k3s.io/installation/packaged-components)
- [K3s server flags](https://docs.k3s.io/cli/server)
- [Metrics Server v0.9.0 RBAC, Service, APIService and runtime defaults](https://github.com/kubernetes-sigs/metrics-server/releases/download/v0.9.0/components.yaml)
- [Metrics Server requirements and scaling](https://github.com/kubernetes-sigs/metrics-server/blob/v0.9.0/README.md)
- [Rancher image registry tag](https://hub.docker.com/r/rancher/mirrored-metrics-server/tags?name=v0.9.0)
- [App-template chart](https://bjw-s-labs.github.io/helm-charts/docs/app-template/)
- [Flux suspension and reconciliation](https://fluxcd.io/flux/components/kustomize/kustomizations/#suspend)
- [Flux HelmRelease remediation and drift detection](https://fluxcd.io/flux/components/helm/helmreleases/)
- [HPA behavior with missing metrics](https://kubernetes.io/docs/tasks/run-application/horizontal-pod-autoscale/#algorithm-details)
