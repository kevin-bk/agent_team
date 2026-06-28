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
        className="flex h-9 cursor-pointer items-center gap-2 rounded-md px-3 text-[13.5px] font-medium text-nav-foreground/80 transition-colors duration-150 hover:bg-nav-hover hover:text-white"
      >
        <ArrowLeft className="h-[18px] w-[18px] shrink-0" />
        <span>Back to admin</span>
      </a>
    );
  }
  return <UserButton afterSignOutUrl="/" />;
}
