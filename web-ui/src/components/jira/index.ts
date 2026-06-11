/**
 * Reusable Jira-flavoured UI primitives.
 *
 * These are presentational building blocks modelled on the community
 * `jira-clone-angular` `jira-control` set (avatars, breadcrumbs, issue-type
 * glyphs, page chrome) so the whole app shares one consistent enterprise look.
 */
export { JiraAvatar, AvatarGroup } from "./Avatar";
export type { JiraAvatarProps, AvatarGroupItem } from "./Avatar";
export { Breadcrumbs } from "./Breadcrumbs";
export type { Crumb } from "./Breadcrumbs";
export {
  IssueTypeIcon,
  issueTypeFromTask,
  taskIssueType,
  ISSUE_TYPE_META,
  ISSUE_TYPE_ORDER,
} from "./IssueTypeIcon";
export type { IssueType } from "./IssueTypeIcon";
export { PageHeader } from "./PageHeader";
export { JiraIcon } from "./JiraIcon";
