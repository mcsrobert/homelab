'use strict';
const https = require('https');
const http = require('http');
const fs = require('fs');
const sa = '/var/run/secrets/kubernetes.io/serviceaccount/';
const kubeCA = fs.readFileSync(sa + 'ca.crt');
const api = 'https://kubernetes.default.svc';
const bao = process.env.BAO_ADDR;
const annotation = 'secret-rotation.home.arpa/rotate-every';

function request(url, opts = {}, body) {
  return new Promise((resolve, reject) => {
    const req = https.request(url, { ...opts, timeout: 10000 }, res => {
      res.setEncoding('utf8');
      let data = '';
      res.on('data', chunk => {
        data += chunk;
      });
      res.on('end', () => {
        if (res.statusCode < 200 || res.statusCode >= 300) {
          return reject(new Error(`HTTP ${res.statusCode}`));
        }
        try {
          resolve(JSON.parse(data));
        } catch (err) {
          reject(err);
        }
      });
    });
    req.on('timeout', () => req.destroy(new Error('timeout')));
    req.on('error', reject);
    if (body) {
      req.write(JSON.stringify(body));
    }
    req.end();
  });
}
function duration(value) {
  const match = /^(\d+)(h|d|w)$/.exec(value || '');
  if (!match || +match[1] === 0) {
    throw new Error('invalid rotation interval');
  }
  return +match[1] * { h: 3600, d: 86400, w: 604800 }[match[2]];
}
function label(value) {
  return String(value)
    .replace(/\\/g, '\\\\')
    .replace(/\n/g, '\\n')
    .replace(/"/g, '\\"');
}
async function scrape() {
  let errors = 0;
  const lines = [];
  const jwt = fs.readFileSync(sa + 'token', 'utf8');
  const auth = await request(
    `${bao}/v1/auth/kubernetes/login`,
    { method: 'POST', headers: { 'Content-Type': 'application/json' } },
    { role: 'secret-rotation', jwt },
  );
  const token = auth.auth.client_token;
  let next = '';
  do {
    const query = next ? `&continue=${encodeURIComponent(next)}` : '';
    const result = await request(
      `${api}/apis/external-secrets.io/v1/externalsecrets?limit=500${query}`,
      { ca: kubeCA, headers: { Authorization: `Bearer ${jwt}` } },
    );
    for (const item of result.items) {
      const interval = item.metadata.annotations?.[annotation];
      if (interval === undefined) {
        continue;
      }
      try {
        const seconds = duration(interval);
        const store = item.spec.secretStoreRef;
        if (store?.kind !== 'ClusterSecretStore' || store.name !== 'openbao' || item.spec.dataFrom?.length) {
          throw new Error('unsupported store or dataFrom');
        }
        const refs = item.spec.data || [];
        if (!refs.length) {
          throw new Error('no remote references');
        }
        if (refs.some(ref => !ref.remoteRef?.key || ref.sourceRef || ref.remoteRef.version)) {
          throw new Error('unsupported reference');
        }
        const keys = [...new Set(refs.map(ref => ref.remoteRef.key))];
        await Promise.all(keys.map(async key => {
          const path = key.replace(/^homelab\//, '').split('/').map(encodeURIComponent).join('/');
          const response = await request(
            `${bao}/v1/homelab/metadata/${path}`,
            { headers: { 'X-Vault-Token': token } },
          );
          const version = response.data.versions[String(response.data.current_version)];
          const timestamp = Date.parse(version?.created_time) / 1000;
          const deleted = version?.deletion_time && Date.parse(version.deletion_time) <= Date.now();
          if (!version || version.destroyed || deleted || !Number.isFinite(timestamp)) {
            throw new Error('current version unavailable');
          }
          const labels = [
            `namespace="${label(item.metadata.namespace)}"`,
            `externalsecret="${label(item.metadata.name)}"`,
            `key="${label(key)}"`,
          ].join(',');
          lines.push(`openbao_secret_rotation_due_timestamp_seconds{${labels}} ${timestamp + seconds}`);
        }));
      } catch (err) {
        errors++;
        console.error(`rotation metadata error for ${item.metadata.namespace}/${item.metadata.name}: ${err.message}`);
      }
    }
    next = result.metadata?.continue || '';
  } while (next);
  lines.push(`openbao_secret_rotation_collection_errors ${errors}`);
  return lines.join('\n') + '\n';
}
// Reuse a result for most of the 5m scrape interval and share one in-flight scrape.
const cacheTtl = 4 * 60 * 1000;
let cached = { metrics: null, at: 0 };
let inflight = null;
function cachedScrape() {
  if (cached.metrics !== null && Date.now() - cached.at < cacheTtl) {
    return Promise.resolve(cached.metrics);
  }
  inflight ??= scrape()
    .then(metrics => {
      cached = { metrics, at: Date.now() };
      return metrics;
    })
    .finally(() => {
      inflight = null;
    });
  return inflight;
}
http.createServer(async (req, res) => {
  if (req.url !== '/metrics') {
    res.writeHead(404).end();
    return;
  }
  try {
    const metrics = await cachedScrape();
    res.writeHead(200, { 'Content-Type': 'text/plain; version=0.0.4' }).end(metrics);
  } catch (err) {
    console.error(`rotation scrape failed: ${err.message}`);
    res.writeHead(500).end();
  }
}).listen(8080, '0.0.0.0');
process.on('SIGTERM', () => {
  process.exit(0);
});
