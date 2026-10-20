import { useAuthedImage } from "@/hooks/useAuthedImage";
import type { AdminPerson } from "@/lib/api/admin";

import { initials } from "./identity";

/**
 * A person's Telegram profile photo, or their initials until one arrives.
 *
 * The photo is proxied by the backend (the Telegram file URL carries the bot
 * token) and cached there for a day. Initials hold the circle while it loads
 * and stay when there is no photo to show: none set, hidden from bots by the
 * person's privacy settings, or Telegram unreachable.
 */
export function PersonAvatar({ person }: { person: AdminPerson }) {
  const { src } = useAuthedImage(`/api/v1/admin/people/${person.user_id}/photo`);
  if (src) {
    return <img src={src} alt="" aria-hidden className="h-8 w-8 shrink-0 rounded-full object-cover" />;
  }
  return (
    <span
      className={`flex h-8 w-8 shrink-0 items-center justify-center rounded-full text-[11px] font-semibold ${
        person.known
          ? "bg-[var(--color-primary)]/15 text-[var(--color-primary)]"
          : "bg-[var(--color-surface-hover)] text-[var(--color-text-muted)]"
      }`}
      aria-hidden
    >
      {initials(person)}
    </span>
  );
}
