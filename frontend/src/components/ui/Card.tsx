import type { ComponentPropsWithRef, ElementType } from "react";

/**
 * The panel surface every card, KPI tile and settings section shares: a
 * 1px border on `--color-surface` with `p-4` inside and `rounded-lg` outside.
 *
 * Exported as a class string too, for elements that cannot be a `<Card>`
 * (a `<p>`, a `<button>` that is the whole card, a `<textarea>`).
 */
export const CARD_CLASS =
  "rounded-lg border border-[var(--color-border)] bg-[var(--color-surface)] p-4";

type CardProps<T extends ElementType> = { as?: T } & Omit<ComponentPropsWithRef<T>, "as">;

export function Card<T extends ElementType = "div">({ as, className, ...rest }: CardProps<T>) {
  const Tag = (as ?? "div") as ElementType;
  return <Tag className={className ? `${CARD_CLASS} ${className}` : CARD_CLASS} {...rest} />;
}
