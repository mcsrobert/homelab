# Secret rotation reminders

Annotate an ExternalSecret that uses the `openbao` ClusterSecretStore to opt in:

```yaml
metadata:
  annotations:
    secret-rotation.home.arpa/rotate-every: "90d"
```

- Intervals are positive whole numbers followed by `h`, `d`, or `w`.
- Each distinct `spec.data[].remoteRef.key`, with or without the `homelab/`
  mount prefix, is checked separately.
- Results are cached for four minutes.
- The current KV v2 version's creation time is used, not the key's original
  creation time. Any write to the key resets its age, even if the credential
  did not change.
- Multiple credentials in one KV key share a rotation age.
- `dataFrom`, per-reference stores, and pinned versions are not supported. An
  opted-in unsupported configuration or an inaccessible key raises a collection
  error alert.
- The exporter does not read secret values.
- This does not monitor the OpenBao authentication token itself.

## Existing OpenBao installations

The policy and Kubernetes authentication role in the OpenBao HelmRelease are
used only when OpenBao initializes a new storage backend. Before this feature
can work on the existing installation, an administrator must provision the same
`secret-rotation` ACL policy and Kubernetes auth role through OpenBao's normal
administrative procedure. The policy grants read access only to
`homelab/metadata/*`; the role binds the `secret-rotation` service account in
`observability` and issues five-minute tokens. The exporter will fail its
scrapes until that provisioning is complete. Do not reinitialize OpenBao.

The exporter needs HTTPS trust for the OpenBao endpoint and access to the
Kubernetes API. Check the exporter-down and collection-error alerts after setup,
then opt individual ExternalSecrets in through Git.
