import { useCallback, useEffect, useState } from "react";
import { applyUpdate, getVersion, type VersionInfo } from "../../api";
import { ErrorState, LoadingState } from "../../components/ui";

const CONFIRM = "update";

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

/** Wait for the panel to answer again after it re-execs into the new code. */
async function waitForPanel(timeoutMs = 120_000): Promise<boolean> {
  const deadline = Date.now() + timeoutMs;
  // Give the old process a moment to actually go down first, or we'd "succeed"
  // against the very process we are replacing.
  await new Promise((r) => setTimeout(r, 2000));
  while (Date.now() < deadline) {
    try {
      const r = await fetch("/api/health", { cache: "no-store" });
      if (r.ok) return true;
    } catch {
      /* still down — that is expected */
    }
    await new Promise((r) => setTimeout(r, 1500));
  }
  return false;
}

export function UpdatesTab() {
  const [info, setInfo] = useState<VersionInfo | null>(null);
  const [loadError, setLoadError] = useState<string | null>(null);
  const [checking, setChecking] = useState(false);

  // --- applying (this runs new code on the host; treat it with ceremony) ---
  const [confirmText, setConfirmText] = useState("");
  const [applying, setApplying] = useState(false);
  const [applyError, setApplyError] = useState<string | null>(null);
  const [applyLog, setApplyLog] = useState<string[] | null>(null);
  const [phase, setPhase] = useState<"" | "working" | "restarting" | "done">("");

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

  const apply = async () => {
    if (!info?.latest || confirmText !== CONFIRM || applying) return;
    setApplying(true);
    setApplyError(null);
    setApplyLog(null);
    setPhase("working");
    try {
      const res = await applyUpdate(info.latest.commit);
      setApplyLog(res.log);
      if (res.restarted) {
        setPhase("restarting");
        const back = await waitForPanel();
        if (!back) {
          setApplyError(
            "The panel did not come back within two minutes. It may still be " +
              "restarting — reload the page. The database was backed up before the " +
              "update, and a failed update rolls itself back.",
          );
          setPhase("");
          return;
        }
      }
      setPhase("done");
      setConfirmText("");
      await load(true); // re-check: this should now say "up to date"
    } catch (e) {
      setApplyError(e instanceof Error ? e.message : "update failed");
      setPhase("");
    } finally {
      setApplying(false);
    }
  };

  if (loadError) return <ErrorState message={loadError} />;
  if (!info) return <LoadingState />;

  // A non-git install (nix/docker/pip) has no commit to compare — it moves between
  // RELEASES, so that is what it is told about. Reporting "0 commits behind" there
  // would be meaningless, and "could not check" was simply wrong.
  const byRelease = !info.commit && info.latest_release;

  const status = info.update_available
    ? {
        tone: "text-amber",
        text: byRelease
          ? `${info.latest_release} available`
          : `${info.behind_by} commit${info.behind_by === 1 ? "" : "s"} behind`,
      }
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
              value={
                <>
                  {info.commit.slice(0, 8)}
                  {info.dirty && <span className="text-amber"> · local changes</span>}
                </>
              }
            />
          )}
          {info.branch && <Row label="branch" value={info.branch} />}
          <Row label="tracking" value={`${info.repo}@${info.branch_tracked}`} />
          {info.latest_release && (
            <Row label="latest release" value={info.latest_release} />
          )}
          {info.latest && !byRelease && (
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

          {info.self_update ? (
            <div className="space-y-3">
              <div className="label">apply it</div>
              <p className="text-muted text-xs">
                The panel will fast-forward to{" "}
                <span className="metric text-fg">{info.latest?.commit.slice(0, 8)}</span>,
                reinstall its dependencies, rebuild the interface and restart itself. It
                backs up the database first, refuses if you have local changes, and rolls
                back if the new code does not even import.
              </p>
              {/* A dirty tree is refused server-side, so don't offer the button. */}
              {info.dirty ? (
                <p className="text-amber text-xs">
                  this checkout has uncommitted changes, so applying an update is refused —
                  it would overwrite them. Commit or stash them on the host first, then
                  check again.
                </p>
              ) : (
                <div className="flex flex-wrap items-center gap-3">
                  <input
                    className="input metric max-w-[14rem]"
                    value={confirmText}
                    onChange={(e) => setConfirmText(e.target.value)}
                    placeholder={`type "${CONFIRM}"`}
                    disabled={applying}
                    spellCheck={false}
                    autoComplete="off"
                    aria-label={`type ${CONFIRM} to confirm`}
                  />
                  <button
                    className="btn-pink"
                    onClick={() => void apply()}
                    disabled={applying || confirmText !== CONFIRM}
                  >
                    {phase === "working"
                      ? "updating…"
                      : phase === "restarting"
                        ? "restarting…"
                        : "apply update"}
                  </button>
                </div>
              )}
              {phase === "restarting" && (
                <p className="text-amber text-xs" role="status">
                  the panel is restarting into the new version — waiting for it to answer…
                </p>
              )}
              {applyLog && (
                <pre className="well p-3 metric text-xs overflow-x-auto whitespace-pre-wrap">
                  {applyLog.join("\n")}
                </pre>
              )}
              {applyError && (
                <div className="text-red text-xs whitespace-pre-wrap" role="alert">
                  {applyError}
                </div>
              )}
            </div>
          ) : (
            <HowToApply deployment={info.deployment} command={info.command} />
          )}
        </div>
      )}

      {phase === "done" && !info.update_available && (
        <div className="card border-cyan/40 p-3 text-cyan text-xs" role="status">
          updated and running the new version.
        </div>
      )}
    </div>
  );
}

/** How to apply an update on a deployment the panel is not allowed to update itself.
 *
 *  Nix is the interesting case: the panel runs from an immutable /nix/store path, so
 *  "update" means moving the flake input in the CONFIGURATION repo and rebuilding the
 *  system. That is two commands in two places, and printing a single line would be a
 *  half-truth. A NixOS system's source of truth is its flake; a service that rewrites
 *  its own store path is lying to the thing that manages it.
 */
function HowToApply({ deployment, command }: { deployment: string; command: string }) {
  const [copied, setCopied] = useState(false);
  const nix = deployment === "nix";

  const steps = nix
    ? [
        "# 1. where your NixOS config lives (the repo with flake.nix):",
        "nix flake update hlidskjalf",
        "",
        "# 2. rebuild the host that runs the panel:",
        "sudo nixos-rebuild switch --flake .#<host>",
      ].join("\n")
    : command;

  const copy = () => {
    void navigator.clipboard.writeText(steps).then(() => {
      setCopied(true);
      window.setTimeout(() => setCopied(false), 1500);
    });
  };

  return (
    <div>
      <div className="flex items-center gap-3">
        <div className="label">apply it</div>
        <button type="button" className="ml-auto text-xs text-cyan hover:underline" onClick={copy}>
          {copied ? "copied" : "copy"}
        </button>
      </div>
      <pre className="well p-3 metric text-xs overflow-x-auto whitespace-pre-wrap">{steps}</pre>
      <p className="text-muted text-xs mt-2">
        {nix ? (
          <>
            This panel runs from <span className="metric">/nix/store</span>, which is
            immutable — it cannot update itself, and should not: your flake is the source of
            truth, and a service that rewrote its own store path would be lying to the system
            that manages it. Move the input, rebuild, and the panel restarts on the new
            version. If your config repo is on another machine, commit and push there first,
            then pull and rebuild on this host.
          </>
        ) : (
          <>
            Applying from the panel is <span className="text-fg">off</span>: it runs code
            fetched from the internet, so it stays disabled unless the operator sets{" "}
            <span className="metric">HLIDSKJALF_ALLOW_SELF_UPDATE=true</span> on the host —
            and it is never possible for a Docker install, which cannot replace its own
            image. Run the command above instead.
          </>
        )}
      </p>
    </div>
  );
}
