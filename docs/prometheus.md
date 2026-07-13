# Prometheus metrics datasource (optional)

By default the panel reads graphs straight from PVE's `rrddata`
(`HLIDSKJALF_METRICS_SOURCE=rrd`). That is fine for hour/day/week, but the RRAs
consolidate hard at long range (the `month` timeframe is 12h-granular, `year` is
1-week-granular) and PVE keeps no history beyond the RRD.

If heimdall's Prometheus scrapes hella, the panel can read the same graphs from
it instead — same charts, same rows, finer long-range resolution:

```
HLIDSKJALF_METRICS_SOURCE=prometheus
HLIDSKJALF_PROMETHEUS_URL=http://10.0.20.17:9090
```

It is a drop-in swap (`datasources/prometheus.py` implements the same
`MetricsSource` protocol as `datasources/rrd.py`): nothing else changes, and if
you set none of these vars the panel behaves exactly as before.

## 1. prometheus-pve-exporter on heimdall

The exporter talks to PVE with the **same** `hlidskjalf@pve!panel` token the panel
uses (`PVEAuditor` alone is enough for it — see docs/bootstrap.md §1).

```nix
# hosts/heimdall/modules/services/… (nixos-dotfiles repo)
services.prometheus.exporters.pve = {
  enable = true;
  port = 9221;
  # secrets file, 0400, NOT in the store:
  #   default:
  #     user: hlidskjalf@pve
  #     token_name: panel
  #     token_value: <the token secret>
  #     verify_ssl: false      # hella's API cert is self-signed
  configFile = "/run/secrets/pve-exporter.yml";
};

services.prometheus.scrapeConfigs = [{
  job_name = "pve";
  metrics_path = "/pve";
  params.target = [ "10.0.20.10" ];   # hella
  scrape_interval = "60s";            # 15-60s is fine; see the rate() note below
  static_configs = [{ targets = [ "127.0.0.1:9221" ]; }];
}];
```

Sanity check once it is up:

```bash
curl -s 'http://127.0.0.1:9090/api/v1/query?query=pve_cpu_usage_ratio' | jq '.data.result[].metric.id'
# "node/hella", "qemu/105", "lxc/130", …
```

The `id` label (`qemu/<vmid>`, `lxc/<vmid>`, `node/<node>`) is how the panel
selects a series, and `<node>` must equal `HLIDSKJALF_PVE_NODE`.

## 2. Panel environment

| Variable | Default | Meaning |
| --- | --- | --- |
| `HLIDSKJALF_METRICS_SOURCE` | `rrd` | `rrd` or `prometheus`. Anything else is a startup error. |
| `HLIDSKJALF_PROMETHEUS_URL` | — | Base URL of the Prometheus HTTP API, **without** `/api/v1`. Required when the source is `prometheus` (startup fails with a clear error otherwise). |
| `HLIDSKJALF_PROMETHEUS_TOKEN` | — | Optional bearer token (`Authorization: Bearer …`), for a Prometheus behind an auth proxy. |
| `HLIDSKJALF_PROMETHEUS_USERNAME` / `_PASSWORD` | — | Optional HTTP basic auth instead of the bearer token. |
| `HLIDSKJALF_PROMETHEUS_FINGERPRINT` | — | SHA-256 cert fingerprint (colon-hex) to pin, for an `https://` URL with a self-signed cert — same mechanism as `HLIDSKJALF_PVE_FINGERPRINT`. |
| `HLIDSKJALF_PROMETHEUS_VERIFY` | `true` | With no fingerprint, verify TLS against system CAs. `false` knowingly disables verification (logs a warning). Irrelevant for a plain-http in-LAN URL. |
| `HLIDSKJALF_PROMETHEUS_TIMEOUT` | `15` | Seconds per `query_range` call. |
| `HLIDSKJALF_PROMETHEUS_NODE_QUERIES` | `{}` | JSON map of node field → PromQL, for the node fields the PVE exporter does not publish (see below). |

## 3. What maps to what

Guest series (`id="qemu/105"` / `id="lxc/130"`), one PromQL query per row field:

| Row field | Metric | Note |
| --- | --- | --- |
| `cpu` | `pve_cpu_usage_ratio` | 0..1, like rrddata |
| `maxcpu` | `pve_cpu_usage_limit` | cores |
| `mem` / `maxmem` | `pve_memory_usage_bytes` / `pve_memory_size_bytes` | |
| `disk` / `maxdisk` | `pve_disk_usage_bytes` / `pve_disk_size_bytes` | |
| `diskread` / `diskwrite` | `pve_disk_read_bytes` / `pve_disk_write_bytes` | counters → `rate()` |
| `netin` / `netout` | `pve_network_receive_bytes` / `pve_network_transmit_bytes` | counters → `rate()` |

rrddata reports the traffic/IO fields as **bytes/sec** while the exporter
publishes cumulative counters, so the panel wraps those in `rate()` (which also
absorbs the counter reset when a guest reboots). The rate window is
`max(step, 300s)`, so the exporter must be scraped **at least every ~60s** or the
short timeframes will have holes. Gauges are consolidated over the step with
`avg_over_time` (`cf=AVERAGE`, the default) or `max_over_time` (`cf=MAX`).

Node series (`id="node/hella"`): `cpu`, `maxcpu`, `memused`, `memtotal`,
`rootused` (`pve_disk_usage_bytes`), `roottotal` (`pve_disk_size_bytes`).

**`iowait`, `loadavg`, `netin`, `netout` do not exist for a node in
prometheus-pve-exporter** — PVE's `/cluster/resources` has no such node fields —
so those node chart series are empty (`null`) with this source. If a
`node_exporter` runs on hella, fill them in yourself; `$node` and `$step` (the
step in seconds) are substituted:

```
HLIDSKJALF_PROMETHEUS_NODE_QUERIES={"loadavg":"node_load1{instance=\"hella:9100\"}","iowait":"rate(node_cpu_seconds_total{mode=\"iowait\",instance=\"hella:9100\"}[$steps])","netin":"rate(node_network_receive_bytes_total{device=\"vmbr0\",instance=\"hella:9100\"}[$steps])","netout":"rate(node_network_transmit_bytes_total{device=\"vmbr0\",instance=\"hella:9100\"}[$steps])"}
```

## 4. Timeframes

Windows are identical to PVE's rrd timeframes so a chart covers the same span
whichever source is selected; only the long-range steps get finer:

| Timeframe | Window | Step (prometheus) | Step (rrd) |
| --- | --- | --- | --- |
| `hour` | ~70 min | 60s | 60s |
| `day` | ~35 h | 30 min | 30 min |
| `week` | ~5.8 d | 2 h | 2 h |
| `month` | ~35 d | **1 h** | 12 h |
| `year` | ~1.3 y | **1 d** | 1 w |

## 5. Failure behaviour

Prometheus being down, slow or rejecting a query never 500s the metrics endpoint:
each field is queried independently, failures are logged as warnings and that
field becomes `null` (a total outage simply yields an empty series and an empty
chart). Missing series are never back-filled with invented data.

## 6. Local development

`dev/mock_prometheus.py` is a stand-in Prometheus HTTP API (it recognises the
exporter's metric names and answers `query_range` with plausible matrix data; it
does not evaluate PromQL):

```bash
cd dev && uvicorn mock_prometheus:app --port 19090
HLIDSKJALF_METRICS_SOURCE=prometheus HLIDSKJALF_PROMETHEUS_URL=http://127.0.0.1:19090 \
  uvicorn hlidskjalf.main:app --port 8787
```

`backend/tests/test_prometheus.py` drives the source against it offline.
