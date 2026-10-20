/** `3, "bot"` → `"3 bots"`. Thousands separated, because a fleet's closed set runs to five figures. */
export function plural(count: number, noun: string): string {
  return `${count.toLocaleString()} ${noun}${count === 1 ? "" : "s"}`;
}
