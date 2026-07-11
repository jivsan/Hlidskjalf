// API types — match the backend contract exactly.

export type GuestKind = "qemu" | "lxc";

export interface VmListItem {
  vmid: number;
  name: string;
  kind: GuestKind;
  status: string; // "running" | "stopped" | ...
  cpu: number; // fraction 0..1 of one core-sum
  maxcpu: number;
  mem: number;
  maxmem: number;
  disk: number;
  maxdisk: number;
  uptime: number; // seconds
  netin: number; // cumulative bytes
  netout: number; // cumulative bytes
  tags?: string;
  protected: boolean;
  rescue: boolean;
}

export interface VmConfig {
  cores?: number;
  memory?: number;
  onboot?: number | boolean;
  boot?: string;
  ostype?: string;
  description?: string;
}

export interface VmDetail {
  vmid: number;
  name: string;
  kind: GuestKind;
  status: string;
  uptime: number;
  cpu: number;
  maxcpu: number;
  mem: number;
  maxmem: number;
  disk: number;
  maxdisk: number;
  netin: number;
  netout: number;
  diskread: number;
  diskwrite: number;
  agent: boolean;
  ips: string[];
  vlan: string | null;
  mac?: string;
  bridge?: string;
  config: VmConfig;
  protected: boolean;
  rescue: boolean;
  rescue_since?: number | string | null;
}

export type PowerAction = "start" | "shutdown" | "reboot" | "stop" | "reset";

export interface TaskStatus {
  status: "running" | "stopped";
  exitstatus?: string;
  type?: string;
  [k: string]: unknown;
}

export interface RecentTask {
  upid: string;
  type: string;
  id: string;
  user: string;
  starttime: number;
  endtime?: number;
  status?: string;
}

export type Timeframe = "hour" | "day" | "week" | "month";

export interface VmMetricPoint {
  t: number; // unix seconds
  cpu: number | null; // 0..1 fraction
  maxcpu: number | null;
  mem: number | null;
  maxmem: number | null;
  disk: number | null;
  maxdisk: number | null;
  diskread: number | null;
  diskwrite: number | null;
  netin: number | null; // bytes/sec
  netout: number | null; // bytes/sec
}

export interface NodeStorage {
  storage: string;
  type: string;
  used: number;
  total: number;
  avail: number;
  content: string;
}

export interface NodeInfo {
  name: string;
  status: {
    cpu: number;
    maxcpu: number;
    mem: number;
    maxmem: number;
    uptime: number;
    loadavg?: number[] | string[];
    [k: string]: unknown;
  };
  storage: NodeStorage[];
}

export interface NodeMetricPoint {
  t: number;
  cpu: number | null;
  maxcpu: number | null;
  memused: number | null;
  memtotal: number | null;
  iowait: number | null;
  netin: number | null;
  netout: number | null;
  loadavg: number | null;
  rootused: number | null;
  roottotal: number | null;
}

export interface BandwidthDay {
  date: string; // YYYY-MM-DD
  bytes_in: number;
  bytes_out: number;
}

export interface BandwidthRange {
  days: BandwidthDay[];
  totals: { bytes_in: number; bytes_out: number; total: number };
  quota_gb: number | null;
  utilization: number | null; // fraction of quota
}

export interface BandwidthMonthly {
  year: number;
  months: Array<{ month: number; bytes_in: number; bytes_out: number }>;
}

export interface BandwidthSummary {
  month: string;
  vms: Record<string, { bytes_in: number; bytes_out: number; total: number }>;
}

export interface TemplateInfo {
  vmid: number;
  name: string;
}

export interface ProvisionDefaults {
  vlans: string[];
  vlan_gateways: Record<string, string>;
  default_ssh_keys: string;
  next_vmid: number;
  storages: string[];
}

export interface ProvisionRequest {
  name: string;
  template_vmid: number;
  cores: number;
  memory_mb: number;
  disk_gb: number;
  vlan: string;
  ip_cidr: string;
  gateway: string;
  ssh_keys: string;
  start: boolean;
}

export interface ConsoleInfo {
  ws_path: string;
  password: string;
}
