import { useQuery } from "@tanstack/react-query";
import { useEffect, useId, useState } from "react";

import { SelectField as MenuSelect } from "@/components/ui/select-field";
import { routinesApi, type RoutineFieldInfo } from "@/lib/api/routines";
import { useServer } from "@/hooks/useServer";
import { formatSlug } from "@/lib/routineUtils";

interface Props {
  fields: Record<string, RoutineFieldInfo>;
  values: Record<string, unknown>;
  onChange: (key: string, value: unknown) => void;
}

function SelectField({
  id,
  descriptionId,
  label,
  field,
  value,
  onChange,
}: {
  id: string;
  descriptionId?: string;
  label: string;
  field: RoutineFieldInfo;
  value: unknown;
  onChange: (v: unknown) => void;
}) {
  const { server } = useServer();
  const { data, isLoading } = useQuery({
    queryKey: ["routine-field-options", field.options_from, server],
    queryFn: () => routinesApi.getRoutineFieldOptions(field.options_from!, server!),
    enabled: !!field.options_from && !!server,
    staleTime: 30_000,
  });

  const options = data?.options ?? [];

  // Auto-select first option ONLY when no value is set. A non-empty persisted
  // value is preserved even if it's absent from the currently loaded options
  // (e.g. server/pair temporarily unavailable, server-scoped options).
  useEffect(() => {
    if (options.length === 0) return;
    if (value != null && value !== "") return;
    onChange(options[0]);
    // onChange is an unstable inline closure from the parent; depending on it
    // would re-run this effect every parent render. The auto-select trigger
    // only depends on options/value, so we intentionally omit it.
    // eslint-disable-next-line react-hooks/exhaustive-deps
  }, [options, value]);

  // A persisted value the server no longer lists stays visible and selected,
  // so opening the form never silently swaps it for the first option.
  const current = String(value ?? field.default ?? "");
  const items = [
    ...(current && !options.includes(current) ? [current] : []),
    ...options,
  ].map((opt) => ({ value: opt, label: opt }));

  return (
    <MenuSelect
      size="field"
      id={id}
      describedBy={descriptionId}
      label={label}
      value={current}
      onChange={onChange}
      options={items}
      placeholder={isLoading ? "Loading..." : items.length === 0 ? "No options available" : undefined}
    />
  );
}

function NumberField({
  id,
  descriptionId,
  fieldType,
  value,
  onChange,
}: {
  id: string;
  descriptionId?: string;
  fieldType: string;
  value: unknown;
  onChange: (v: number) => void;
}) {
  const [draft, setDraft] = useState(String(value ?? ""));

  // Sync from parent when value changes externally
  useEffect(() => {
    setDraft(String(value ?? ""));
  }, [value]);

  return (
    <input
      id={id}
      aria-describedby={descriptionId}
      type="number"
      step={fieldType === "float" ? "any" : "1"}
      value={draft}
      onChange={(e) => setDraft(e.target.value)}
      onBlur={() => {
        const v = fieldType === "int" ? parseInt(draft) : parseFloat(draft);
        if (!isNaN(v)) onChange(v);
        else setDraft(String(value ?? ""));
      }}
      className="h-9 w-full rounded-md border border-[var(--color-border)] bg-[var(--color-surface)] px-2.5 text-sm text-[var(--color-text)] focus:border-[var(--color-primary)] focus:outline-none focus:ring-1 focus:ring-[var(--color-primary)]"
    />
  );
}

export function RoutineConfigForm({ fields, values, onChange }: Props) {
  const formId = useId();

  return (
    <div className="@container min-w-0 text-left">
      <div className="grid grid-cols-1 gap-x-5 gap-y-3.5 @min-[640px]:grid-cols-2">
        {Object.entries(fields).map(([key, field]) => {
          const id = `${formId}-${key}`;
          const descriptionId = field.description ? `${id}-description` : undefined;
          const isBoolean = field.type === "bool" && !(field.widget === "select" && field.options_from);
          const label = (
            <label htmlFor={id} className="mb-1 block text-xs font-medium text-[var(--color-text)]">
              {field.title ?? formatSlug(key)}
            </label>
          );
          const description = field.description ? (
            <p id={descriptionId} className="mt-1 text-[11px] leading-snug text-[var(--color-text-muted)]">
              {field.description}
            </p>
          ) : null;

          return (
            <div key={key} className={`min-w-0 break-words ${isBoolean ? "col-span-full flex items-center justify-between gap-4" : ""}`}>
              {isBoolean ? (
                <>
                  <div className="min-w-0">{label}{description}</div>
                  <button
                    id={id}
                    type="button"
                    role="switch"
                    aria-checked={!!values[key]}
                    aria-describedby={descriptionId}
                    onClick={() => onChange(key, !values[key])}
                    className="flex h-9 shrink-0 items-center gap-2 rounded-md px-2 text-xs font-medium text-[var(--color-text-muted)] transition-colors hover:bg-[var(--color-surface-hover)] focus-visible:outline-none focus-visible:ring-2 focus-visible:ring-[var(--color-primary)]"
                  >
                    <span aria-hidden="true" className={`relative h-5 w-9 rounded-full transition-colors ${values[key] ? "bg-[var(--color-primary)]" : "bg-[var(--color-border)]"}`}>
                      <span className={`absolute top-0.5 h-4 w-4 rounded-full bg-[var(--color-surface)] ${values[key] ? "right-0.5" : "left-0.5"}`} />
                    </span>
                    <span aria-hidden="true">{values[key] ? "On" : "Off"}</span>
                  </button>
                </>
              ) : (
                <>
                  {label}
                  {field.widget === "select" && field.options_from ? (
                    <SelectField
                      id={id}
                      descriptionId={descriptionId}
                      label={field.title ?? formatSlug(key)}
                      field={field}
                      value={values[key]}
                      onChange={(v) => onChange(key, v)}
                    />
                  ) : field.type === "int" || field.type === "float" ? (
                    <NumberField
                      id={id}
                      descriptionId={descriptionId}
                      fieldType={field.type}
                      value={values[key] ?? field.default ?? ""}
                      onChange={(v) => onChange(key, v)}
                    />
                  ) : (
                    <input
                      id={id}
                      aria-describedby={descriptionId}
                      type="text"
                      value={String(values[key] ?? field.default ?? "")}
                      onChange={(e) => onChange(key, e.target.value)}
                      className="h-9 w-full rounded-md border border-[var(--color-border)] bg-[var(--color-surface)] px-2.5 text-sm text-[var(--color-text)] focus:border-[var(--color-primary)] focus:outline-none focus:ring-1 focus:ring-[var(--color-primary)]"
                    />
                  )}
                  {description}
                </>
              )}
            </div>
          );
        })}
      </div>
    </div>
  );
}
