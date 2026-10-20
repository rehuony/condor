import { ChevronRight } from "lucide-react";

import { LogsSection } from "@/components/bots/LogsSection";
import { YamlConfigEditor } from "@/components/perf/YamlConfigEditor";
import { IconButton } from "@/components/ui/IconButton";
import type { BotLogEntry, ControllerInfo } from "@/lib/api/bots";
import { controllerKey } from "@/lib/controller-identity";

import type { DrawerKey } from "./ReportActions";

interface ReportDrawerProps {
  openDrawer: DrawerKey;
  server: string;
  activeCtrl: ControllerInfo | undefined;
  configId: string;
  botLogs: BotLogEntry[];
  onConfigSaved: () => void;
  onClose: () => void;
}

/**
 * The right drawer: one column, whichever of the two the scope supports. A
 * controller's config, or a bot's logs — never both, and never one belonging
 * to a scope the user has left.
 */
export function ReportDrawer({
  openDrawer,
  server,
  activeCtrl,
  configId,
  botLogs,
  onConfigSaved,
  onClose,
}: ReportDrawerProps) {
  return (
    <>
      {openDrawer && (
        <div className="w-full max-h-[50%] lg:max-h-none lg:w-[380px] xl:w-[440px] shrink-0 border-l border-[var(--color-border)] flex flex-col bg-[var(--color-surface)]">
          {openDrawer === "config" && activeCtrl ? (
            <YamlConfigEditor
              // Same: an editor keyed on the config id alone kept its unsaved
              // buffer when the user switched to the other bot running it.
              key={controllerKey(activeCtrl)}
              config={activeCtrl.config || {}}
              server={server}
              configId={configId}
              botName={activeCtrl.bot_name}
              onSaved={onConfigSaved}
              onCollapse={onClose}
            />
          ) : (
            <div className="flex h-full min-h-0 flex-col">
              <div className="flex shrink-0 items-center gap-2 border-b border-[var(--color-border)]/50 px-4 py-2">
                <IconButton
                  label="Hide logs"
                  size="xs"
                  onClick={onClose}
                >
                  <ChevronRight className="h-3.5 w-3.5" />
                </IconButton>
                <h3 className="text-xs font-medium uppercase tracking-wider text-[var(--color-text-muted)]">
                  Logs
                </h3>
              </div>
              <div className="flex-1 min-h-0 p-3">
                <LogsSection logs={botLogs} />
              </div>
            </div>
          )}
        </div>
      )}
    </>
  );
}
