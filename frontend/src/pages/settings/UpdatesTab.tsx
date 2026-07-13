import { useCallback, useEffect, useState } from "react";
import { getVersion, type VersionInfo } from "../../api";
import { ErrorState, LoadingState } from "../../components/ui";

// What this tab is allowed to claim, and what it is not:
//   - it never says "up to date" unless it actually compared two commits;
//   - it never offers to update a checkout that is AHEAD (unpushed work);
//   - it never pretends the panel can update itself. A container cannot replace
//     its own image, a Nix system updates from its flake, and only a git/venv
//     install can pull in place — so we print the command for THIS deployment.

function Row({ label, value }: { label: string; value: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-4 py-1.5">
      <span className="text-muted text-xs shrink-0">{label}</span>
      <span className="metric text-xs text-fg text-right break-all">{value}</span>
    </div>
  );
}

export function UpdatesTab() {
  const [info, setInfo] = useState<VersionInfo | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);

  const load = useCallback(async (force = false) => {
    setChecking(true);
    try {
      setInfo(await getVersion(force));
      setLoadError(null);
    } catch (e) {
      setLoadError(e instanceof Error ? e.message : "could not check for updates");
    } finally {
      setChecking(false);
    }
  }, []);

  useEffect(() => {
    void load();
  }, [load]);

  if (loadError) return <ErrorState message={loadError} />;
  if (!info) return <LoadingState />;

  const status = info.update_available
    ? { tone: "text-amber", text: `${info.behind_by} commit${info.behind_by === 1 ? "" : "s"} behind` }
    : info.error
      ? { tone: "text-muted", text: "could not check" }
      : { tone: "text-cyan", text: "up to date" };

  return (
    <div className="space-y-6">
      <div className="card p-5 space-y-4">
        <div className="flex items-center gap-3">
          <div className="eyebrow">this install</div>
          <span className={`ml-auto text-xs metric ${status.tone}`}>● {status.text}</span>
        </div>

        <div className="well p-3">
          <Row label="version" value={info.version} />
          <Row label="deployment" value={info.deployment} />
          {info.commit && (
            <Row
              label="commit"
              value={`${info.commit.slice(0, 8)}${info.dirty ? " (uncommitted changes)" : ""}`}
            />
          )}
          {info.branch && <Row label="branch" value={info.branch} />}
          <Row label="tracking" value={`${info.repo}@${info.branch_tracked}`} />
          {info.latest && (
            <Row label="latest upstream" value={info.latest.commit.slice(0, 8)} />
          )}
        </div>

        {info.error && <p className="text-muted text-xs">{info.error}</p>}

        <div className="flex flex-wrap items-center gap-3">
          <button className="btn-cyan" onClick={() => void load(true)} disabled={checking}>
            {checking ? "checking…" : "check now"}
          </button>
          <a
            className="text-cyan text-xs hover:underline"
            href={info.notes_url}
            target="_blank"
            rel="noreferrer"
          >
            view commits on GitHub →
          </a>
        </div>
      </div>

      {info.update_available && (
        <div className="card p-5 space-y-4">
          <div className="eyebrow">update available</div>

          {info.commits.length > 0 && (
            <div className="well p-3 space-y-1">
              {info.commits.map((c) => (
                <div key={c.sha} className="flex items-baseline gap-3 text-xs">
                  <span className="metric text-muted shrink-0">{c.sha}</span>
                  <span className="text-fg truncate">{c.message}</span>
                </div>
              ))}
            </div>
          )}

          <div>
            <div className="label">apply it</div>
            <pre className="well p-3 metric text-xs overflow-x-auto whitespace-pre-wrap">
              {info.command}
            </pre>
            <p className="text-muted text-xs mt-2">
              The panel deliberately does not update itself — an endpoint that runs new
              code on demand is a bigger hole than anything it protects. Run the command
              above on the host.
            </p>
          </div>
        </div>
      )}

      {info.dirty && (
        <p className="text-amber text-xs">
          this checkout has uncommitted changes — you are ahead of the release, not behind
        </p>
      )}
    </div>
  );
}
