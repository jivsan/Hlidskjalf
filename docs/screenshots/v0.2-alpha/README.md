# Screenshots — v0.2-alpha

**Version:** v0.2-alpha

Tokyo Night, all-mono. Shots below are the panel running against the bundled
mock PVE (`dev/mock_pve.py`), so every page renders with live-looking data.

## Fleet

Every VM/LXC at a glance — status dot, CPU, RAM, uptime and month-to-date
traffic, all sortable; quick start/shutdown per row.

![Fleet](fleet.png)

## VM overview

systemd-unit-style status chip (copy-on-click IP), disk usage, since-boot
bandwidth split, CPU/network sparklines, config, and a name-confirmed danger
zone.

![VM overview](vm-overview.png)

## System statistics

Current-utilization gauges plus timeframe-pilled CPU / RAM / disk / disk-I/O /
network charts with humanized units.

![System statistics](graphs-system.png)

## Bandwidth statistics

Per-VM accounting with an optional monthly quota card (limit / utilized /
utilization %) and daily + Jan–Dec monthly traffic charts.

![Bandwidth statistics](graphs-bandwidth.png)

## Provision

Clone a cloud-init template onto a VLAN with a static IP and SSH keys, then
watch the live task log.

![Provision](provision.png)

## Node

hella's own CPU / RAM / storage and host rrd graphs.

![Node](node.png)

## Mobile

The fleet table stays usable on a phone.

<img src="mobile-fleet.png" alt="Mobile fleet" width="390" />

---

*These screenshots document the v0.2-alpha release.*