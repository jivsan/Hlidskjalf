"""PrometheusSource — Phase 2 stub.

TODO: implement against heimdall's Prometheus HTTP API once
prometheus-pve-exporter is scraping hella (see plan.md §8). Long-range graphs
(rrddata month granularity is coarse) are the motivation. Select with
HLIDSKJALF_METRICS_SOURCE=prometheus.
"""


class PrometheusSource:
    def __init__(self, *args, **kwargs):
        raise NotImplementedError("Prometheus datasource is Phase 2 — use HLIDSKJALF_METRICS_SOURCE=rrd")
