import { UserButton } from "@clerk/clerk-react";
import { AUTH_MODE } from "@/api/config";
import { ArrowLeft } from "@/components/icons";

/**
 * Account control that adapts to the auth mode.
 *
 * - `clerk`: Clerk's `UserButton` (avatar + sign-out menu).
 * - `session`: the host (e.g. the agent_team plugin) owns auth, so we render a
 *   link back to the admin app where sign-out and the rest of the chrome live.
 */
export function AuthButton() {
  if (AUTH_MODE === "session") {
    return (
      <a
        href="/"
        title="Back to admin"
        aria-label="Back to admin"
        className="flex h-7 w-7 items-center justify-center rounded-full bg-surface-3 text-muted-foreground transition-colors hover:text-foreground"
      >
        <ArrowLeft className="h-4 w-4" />
      </a>
    );
  }
  return <UserButton afterSignOutUrl="/" />;
}
