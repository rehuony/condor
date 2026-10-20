import { useMutation, useQuery, useQueryClient } from "@tanstack/react-query";
import { AlertTriangle, Check, Copy, RefreshCw, Save } from "lucide-react";
import { useState } from "react";

import { Card as Panel } from "@/components/ui/Card";
import { EmptyState } from "@/components/ui/EmptyState";
import { IconButton } from "@/components/ui/IconButton";
import { Spinner } from "@/components/ui/Spinner";
import { SelectField } from "@/components/ui/select-field";
import { useServer } from "@/hooks/useServer";
import { OWNER_ONLY_HINT, useServerPermission } from "@/hooks/useServerPermission";
import { errorStatus } from "@/lib/api";
import {
  settingsApi,
  type ApiClientConfigUpdate,
  type ApiServerInfo,
  type ApiUpdateCheck,
  type ApiUpdateStatus,
} from "@/lib/api/settings";
import { copyText } from "@/lib/clipboard";

/**
 * Settings → Hummingbot API: what the navbar-selected server runs, an on-demand check of
 * its source against `main`, and the client defaults every bot deployed from it next
 * will inherit (FEAT-121). Updating the API is a manual build and deploy on its host;
 * nothing here changes the deployment.
 *
 * Scoped by `useServer()` alone, like the Gateway and Keys panels beside it — the navbar
 * select is the only picker, so this panel and the rest of the app can never disagree
 * about which server is "the" server. `server` is in every query key, so switching
 * servers refetches rather than showing the previous one's answers.
 */

/** 501: this server's API predates these routes. A newer build, not an outage. */
const TOO_OLD = 501;

function Card({ title, children }: { title: string; children: React.ReactNode }) {
  return (
    <Panel>
      <h3 className="mb-3 text-sm font-medium text-[var(--color-text)]">{title}</h3>
      {children}
    </Panel>
  );
}

function Row({ label, children }: { label: string; children: React.ReactNode }) {
  return (
    <div className="flex items-baseline justify-between gap-4 py-1.5 text-sm">
      <span className="shrink-0 text-[var(--color-text-muted)]">{label}</span>
      <span className="min-w-0 break-all text-right font-mono text-xs text-[var(--color-text)]">
        {children}
      </span>
    </div>
  );
}

/**
 * Why a request failed, in the operator's terms.
 *
 * A 501 is the one failure that is not a failure: the server is healthy and this panel
 * is newer than it, so it gets a neutral notice naming the fix. Everything else is red.
 */
function Problem({ error }: { error: unknown }) {
  const tooOld = errorStatus(error) === TOO_OLD;
  const message = error instanceof Error ? error.message : "Request failed";
  return (
    <div
      className={`flex items-start gap-2 rounded-md border p-3 text-xs ${
        tooOld
          ? "border-[var(--color-border)] text-[var(--color-text-muted)]"
          : "border-red-500/30 text-[var(--color-red)]"
      }`}
    >
      <AlertTriangle className="mt-0.5 h-3.5 w-3.5 shrink-0" />
      <span>{message}</span>
    </div>
  );
}

/** A human label for a MARKET_DATA_* key: `ticker_max_age` → `Ticker max age`. */
function humanize(key: string): string {
  const words = key.replace(/_/g, " ");
  return words.charAt(0).toUpperCase() + words.slice(1);
}

/**
 * An ISO timestamp as `YYYY-MM-DD HH:MM:SS UTC`, with the viewer's local time as the
 * tooltip. Docker reports nanoseconds, which `Date.parse` does not accept everywhere,
 * so the fraction is cut to milliseconds first. Unparseable input is shown as given.
 */
function UtcTime({ value }: { value: string | null | undefined }) {
  if (!value) return <>unknown</>;
  const ms = Date.parse(value.replace(/(\.\d{3})\d+/, "$1"));
  if (Number.isNaN(ms)) return <>{value}</>;
  const utc = new Date(ms).toISOString().replace("T", " ").slice(0, 19);
  return <span title={new Date(ms).toLocaleString()}>{utc} UTC</span>;
}

/** A long identifier shown short, with the full value on hover and a copy button. */
function Identifier({ value, length }: { value: string; length: number }) {
  const [copied, setCopied] = useState(false);
  return (
    <span className="inline-flex items-center gap-1">
      <span title={value}>{value.slice(0, length)}</span>
      <IconButton
        label={copied ? "Copied" : "Copy full value"}
        size="xs"
        onClick={async () => setCopied(await copyText(value))}
      >
        {copied ? <Check className="h-3 w-3" /> : <Copy className="h-3 w-3" />}
      </IconButton>
    </span>
  );
}

/** `sha256:` plus the first 12 hex digits, the length `docker images` shows. */
function shortImageId(id: string): string {
  const hex = id.startsWith("sha256:") ? id.slice(7) : id;
  return `sha256:${hex.slice(0, 12)}`;
}

function WorkingTree({ dirty }: { dirty: boolean | null | undefined }) {
  if (dirty === true) return <span className="text-amber-500">uncommitted changes</span>;
  if (dirty === false) return <>clean</>;
  return <>unknown</>;
}

function ContainerRows({ info }: { info: ApiServerInfo }) {
  const container = info.container;
  if (container === null) {
    return (
      <p className="pt-3 text-xs text-[var(--color-text-muted)]">
        {info.docker_available
          ? "The API could not identify its own container, so image and build details are unknown."
          : "Docker is not reachable from this API, so image and build details are unknown."}
      </p>
    );
  }
  const build = container.build;
  const digest = container.digest;
  return (
    <>
      <Row label="Image">{container.image ?? "unknown"}</Row>
      <Row label="Image ID">
        {container.image_id ? (
          <Identifier value={container.image_id} length={shortImageId(container.image_id).length} />
        ) : (
          "unknown"
        )}
      </Row>
      {/* A registry digest only exists for a pulled image; a local build has none, and
          the image ID is not a substitute for it. */}
      <Row label="Registry digest">
        {digest ? digest.slice(digest.indexOf("sha256:")).slice(0, 19) : "none (local build)"}
      </Row>
      {build?.revision ? (
        <>
          <Row label="Source revision">
            <Identifier value={build.revision} length={12} />
          </Row>
          <Row label="Branch">{build.branch ?? "unknown"}</Row>
          <Row label="Working tree">
            <WorkingTree dirty={build.dirty} />
          </Row>
          <Row label="Built (start)">
            <UtcTime value={build.created} />
          </Row>
        </>
      ) : (
        <Row label="Source revision">not recorded in this image</Row>
      )}
      <Row label="Image created (Docker)">
        <UtcTime value={container.image_created} />
      </Row>
      {container.compose_project && <Row label="Compose project">{container.compose_project}</Row>}
    </>
  );
}

function VersionCard({ server }: { server: string }) {
  const { data, isLoading, error } = useQuery({
    queryKey: ["api-server-info", server],
    queryFn: () => settingsApi.getApiServerInfo(server),
  });

  if (isLoading) {
    return (
      <Card title="Version">
        <Spinner tone="muted" />
      </Card>
    );
  }
  if (error) {
    return (
      <Card title="Version">
        <Problem error={error} />
      </Card>
    );
  }
  if (!data) return null;

  return (
    <Card title="Version">
      <div className="divide-y divide-[var(--color-border)]">
        <Row label="hummingbot-api">{data.api_version}</Row>
        <Row label="hummingbot">{data.hummingbot_version ?? "not installed"}</Row>
        <ContainerRows info={data} />
      </div>
      <p className="mt-3 text-xs text-[var(--color-text-muted)]">
        Describes the image of the running container. Rebuilding on the host changes it
        only after the next deploy.
      </p>
    </Card>
  );
}

/** The fields a person has typed into; absent means "whatever the server says". */
interface Draft {
  source?: string;
  tokenName?: string;
  tokenSymbol?: string;
  sharePct?: string;
}

function BotDefaultsCard({ server }: { server: string }) {
  const qc = useQueryClient();
  // Predicts the refusal only; `_require_owner` on PUT /settings/api/client-config is
  // what enforces it.
  const { isOwner } = useServerPermission();

  const { data, isLoading, error } = useQuery({
    queryKey: ["api-client-config", server],
    queryFn: () => settingsApi.getApiClientConfig(server),
  });

  // Only the fields actually typed into, laid over the server's answer for display.
  // The alternative — copying the answer into state when it arrives — needs an effect
  // that writes state on every refetch, and would quietly overwrite a half-typed field
  // the moment a background refetch landed.
  const [draft, setDraft] = useState<Draft>({});
  const edit = (patch: Draft) => setDraft((d) => ({ ...d, ...patch }));

  const saveMut = useMutation({
    mutationFn: (changes: ApiClientConfigUpdate) =>
      settingsApi.updateApiClientConfig(server, changes),
    onSuccess: () => {
      // Drop the draft first: once the refetch lands, the server's answer *is* the form.
      setDraft({});
      qc.invalidateQueries({ queryKey: ["api-client-config", server] });
    },
  });

  if (isLoading) {
    return (
      <Card title="Bot defaults">
        <Spinner tone="muted" />
      </Card>
    );
  }
  if (error) {
    return (
      <Card title="Bot defaults">
        <Problem error={error} />
      </Card>
    );
  }
  if (!data) return null;

  const savedSource = data.rate_oracle_source?.name ?? "";
  const savedTokenName = data.global_token?.global_token_name ?? "";
  const savedTokenSymbol = data.global_token?.global_token_symbol ?? "";
  const savedSharePct = String(data.rate_limits_share_pct ?? "");

  const source = draft.source ?? savedSource;
  const tokenName = draft.tokenName ?? savedTokenName;
  const tokenSymbol = draft.tokenSymbol ?? savedTokenSymbol;
  const sharePct = draft.sharePct ?? savedSharePct;

  const share = Number(sharePct);
  const shareValid = sharePct !== "" && Number.isFinite(share) && share > 0 && share <= 100;

  // Only what actually changed is sent, so a save that moves the token does not write
  // back whatever else the form happened to be holding.
  const changes: ApiClientConfigUpdate = {};
  if (source !== savedSource) changes.rate_oracle_source = source;
  if (tokenName !== savedTokenName) changes.global_token_name = tokenName;
  if (tokenSymbol !== savedTokenSymbol) changes.global_token_symbol = tokenSymbol;
  if (share !== data.rate_limits_share_pct) changes.rate_limits_share_pct = share;

  const dirty = Object.keys(changes).length > 0;
  const canSave = isOwner && dirty && shareValid && tokenName.trim() !== "";
  const field =
    "w-full rounded-md border border-[var(--color-border)] bg-[var(--color-bg)] px-2 py-1.5 text-sm text-[var(--color-text)] disabled:cursor-not-allowed disabled:opacity-50";

  return (
    <Card title="Bot defaults">
      <div className="space-y-3">
        <label className="block">
          <span className="text-xs text-[var(--color-text-muted)]">Rate oracle source</span>
          {/* The server's own list: which sources exist is whatever its bundled
              hummingbot knows. A source already persisted but absent from that list
              still appears, so the form never silently changes it on save. */}
          <SelectField
            size="field"
            label="Rate oracle source"
            value={source}
            onChange={(value) => edit({ source: value })}
            disabled={!isOwner}
            title={isOwner ? undefined : OWNER_ONLY_HINT}
            options={[
              ...(!data.available_sources.includes(source) && source !== "" ? [source] : []),
              ...data.available_sources,
            ].map((name) => ({ value: name, label: name }))}
          />
        </label>

        <div className="grid grid-cols-2 gap-3">
          <label className="block">
            <span className="text-xs text-[var(--color-text-muted)]">Global token</span>
            <input
              value={tokenName}
              onChange={(e) => edit({ tokenName: e.target.value })}
              disabled={!isOwner}
              title={isOwner ? undefined : OWNER_ONLY_HINT}
              aria-label="Global token"
              className={field}
            />
          </label>
          <label className="block">
            <span className="text-xs text-[var(--color-text-muted)]">Token symbol</span>
            <input
              value={tokenSymbol}
              onChange={(e) => edit({ tokenSymbol: e.target.value })}
              disabled={!isOwner}
              title={isOwner ? undefined : OWNER_ONLY_HINT}
              aria-label="Token symbol"
              className={field}
            />
          </label>
        </div>

        <label className="block">
          <span className="text-xs text-[var(--color-text-muted)]">
            Rate limit share (%)
          </span>
          <input
            type="number"
            min={1}
            max={100}
            value={sharePct}
            onChange={(e) => edit({ sharePct: e.target.value })}
            disabled={!isOwner}
            title={isOwner ? undefined : OWNER_ONLY_HINT}
            aria-label="Rate limit share (%)"
            className={field}
          />
          {!shareValid && sharePct !== "" && (
            <span className="text-xs text-[var(--color-red)]">
              Must be more than 0 and at most 100.
            </span>
          )}
        </label>

        <div className="flex items-center justify-between gap-3 pt-1">
          <p className="text-xs text-[var(--color-text-muted)]">
            Applies to bots deployed after saving. Running bots keep their current
            settings.
          </p>
          <button
            onClick={() => saveMut.mutate(changes)}
            disabled={!canSave || saveMut.isPending}
            title={isOwner ? undefined : OWNER_ONLY_HINT}
            className="flex shrink-0 items-center gap-1.5 rounded-md bg-[var(--color-primary)] px-3 py-1.5 text-xs font-medium text-[var(--on-primary)] transition-colors hover:bg-[var(--color-primary)]/80 disabled:cursor-not-allowed disabled:opacity-50"
          >
            {saveMut.isPending ? (
              <Spinner size="xs" />
            ) : (
              <Save className="h-3 w-3" />
            )}
            Save
          </button>
        </div>

        {!isOwner && (
          <p className="text-xs text-[var(--color-text-muted)]">{OWNER_ONLY_HINT}</p>
        )}
        {saveMut.error != null && <Problem error={saveMut.error} />}
      </div>
    </Card>
  );
}

const UPDATE_STATUS: Record<ApiUpdateStatus, { label: string; tone: string }> = {
  up_to_date: { label: "Up to date", tone: "text-[var(--color-green)]" },
  update_available: { label: "Update available", tone: "text-[var(--color-primary)]" },
  ahead: { label: "Ahead of main", tone: "text-amber-500" },
  diverged: { label: "Diverged from main", tone: "text-amber-500" },
  unknown: { label: "Cannot compare", tone: "text-[var(--color-text-muted)]" },
  error: { label: "Check failed", tone: "text-[var(--color-red)]" },
};

function UpdateResult({ result }: { result: ApiUpdateCheck }) {
  const { label, tone } = UPDATE_STATUS[result.status] ?? UPDATE_STATUS.unknown;
  return (
    <div className="mt-3 space-y-1 rounded-md border border-[var(--color-border)] p-3 text-xs">
      <p className={`font-medium ${tone}`}>{label}</p>
      {result.reason && <p className="text-[var(--color-text-muted)]">{result.reason}</p>}
      <div className="divide-y divide-[var(--color-border)]">
        <Row label="Running">
          {result.current_revision ? <Identifier value={result.current_revision} length={12} /> : "unknown"}
        </Row>
        <Row label="main">
          {result.latest_revision ? <Identifier value={result.latest_revision} length={12} /> : "unknown"}
        </Row>
        <Row label="Checked">
          <UtcTime value={result.checked_at} />
        </Row>
      </div>
      {result.compare_url && (
        <a
          href={result.compare_url}
          target="_blank"
          rel="noopener noreferrer"
          className="inline-block text-[var(--color-primary)] underline"
        >
          Compare on GitHub
        </a>
      )}
    </div>
  );
}

/**
 * Comparing the running API's source revision with `main` (via the API's own
 * `/system/update-check`).
 *
 * The check contacts GitHub, so it runs only when the button is pressed: the query is
 * disabled, which also keeps mounting, window focus and invalidation from firing it.
 * `server` is in the key, so a result — or a late answer — belongs to the server it was
 * asked about and never shows under another one.
 */
function UpdateCheckCard({ server }: { server: string }) {
  const check = useQuery({
    queryKey: ["api-update-check", server],
    queryFn: () => settingsApi.checkApiUpdate(server),
    enabled: false,
    retry: false,
    gcTime: Infinity,
  });

  const unsupported = errorStatus(check.error) === TOO_OLD;
  const failed = (check.error != null && !unsupported) || check.data?.status === "error";

  return (
    <Card title="Source updates">
      <div className="flex items-center justify-between gap-3">
        <p className="text-xs text-[var(--color-text-muted)]">
          Compares the running source revision with <code>main</code> of
          rehuony/hummingbot-api. Runs only when you ask.
        </p>
        <button
          onClick={() => void check.refetch()}
          disabled={check.isFetching || unsupported}
          className="flex shrink-0 items-center gap-1.5 rounded-md border border-[var(--color-border)] px-3 py-1.5 text-xs font-medium text-[var(--color-text)] transition-colors hover:bg-[var(--color-surface-hover)] disabled:cursor-not-allowed disabled:opacity-50"
        >
          {check.isFetching ? <Spinner size="xs" /> : <RefreshCw className="h-3 w-3" />}
          {failed ? "Retry check" : "Check for updates"}
        </button>
      </div>

      {check.error != null && (
        <div className="mt-3">
          <Problem error={check.error} />
        </div>
      )}
      {check.data && check.error == null && <UpdateResult result={check.data} />}

      <div className="mt-3 text-xs text-[var(--color-text-muted)]">
        <p>To update, on the API host in the hummingbot-api checkout:</p>
        <ol className="mt-1 list-decimal space-y-0.5 pl-5">
          <li>
            <code>make check-update</code>
          </li>
          <li>
            <code>make update</code>
          </li>
          <li>Back up the database and configuration as its maintenance guide describes.</li>
          <li>
            <code>make deploy</code> — builds the current source, then deploys it.
          </li>
        </ol>
        <p className="mt-1">Condor does not update, deploy or restart the API.</p>
      </div>
    </Card>
  );
}

function MarketDataCard({ server }: { server: string }) {
  const { data, error } = useQuery({
    queryKey: ["api-server-info", server],
    queryFn: () => settingsApi.getApiServerInfo(server),
  });

  // The version card above already reports the failure; a second copy of it says
  // nothing new.
  if (error || !data) return null;

  const entries = Object.entries(data.market_data ?? {});
  if (entries.length === 0) return null;

  return (
    <Card title="Market data">
      <div className="divide-y divide-[var(--color-border)]">
        {entries.map(([key, value]) => (
          <Row key={key} label={humanize(key)}>
            {String(value)}
          </Row>
        ))}
      </div>
      <p className="mt-3 text-xs text-[var(--color-text-muted)]">
        Read-only. Set in the server&apos;s <code>.env</code> as <code>MARKET_DATA_*</code>;
        takes effect on API restart.
      </p>
    </Card>
  );
}

export function ApiServerSettings() {
  const { server } = useServer();

  if (!server) {
    return (
      <EmptyState>
        Select a server in the top bar.
      </EmptyState>
    );
  }

  return (
    <div className="space-y-4">
      <VersionCard server={server} />
      <UpdateCheckCard server={server} />
      {/* Keyed by server: a draft typed for one server must not survive a switch to
          another, where it would read as that server's current values. */}
      <BotDefaultsCard key={server} server={server} />
      <MarketDataCard server={server} />
    </div>
  );
}
