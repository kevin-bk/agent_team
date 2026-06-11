/**
 * Atlassian Design System icon adapter.
 *
 * The app was written against `lucide-react` (icons sized with `h-4 w-4`
 * utility classes and coloured via `currentColor`). ADS `@atlaskit/icon` core
 * icons instead size via a `size` prop (12/16px only) and require a `label`,
 * with no `className`. To swap the whole icon set without touching every call
 * site, we wrap each ADS glyph in a span that:
 *   - keeps the familiar `className` API (so `h-4 w-4`, `text-*` still work),
 *   - uses the glyph's `shouldScale` mode so the SVG fills our sized span
 *     (any pixel size, not just 12/16),
 *   - inherits text colour via `color="currentColor"`,
 *   - is decorative by default (`label=""`); pass `aria-label` for meaning.
 *
 * Each export keeps its original lucide name so imports only change their
 * source path: `from "lucide-react"` → `from "@/components/icons"`.
 */
import type { ComponentType } from "react";
import { cn } from "@/lib/utils";

import AddIcon from "@atlaskit/icon/core/add";
import AiAgentIcon from "@atlaskit/icon/core/ai-agent";
import AiSparkleIcon from "@atlaskit/icon/core/ai-sparkle";
import AlignTextLeftIcon from "@atlaskit/icon/core/align-text-left";
import AngleBracketsIcon from "@atlaskit/icon/core/angle-brackets";
import ArchiveBoxIcon from "@atlaskit/icon/core/archive-box";
import ArrowDownIcon from "@atlaskit/icon/core/arrow-down";
import ArrowLeftIcon from "@atlaskit/icon/core/arrow-left";
import ArrowRightIcon from "@atlaskit/icon/core/arrow-right";
import ArrowUpIcon from "@atlaskit/icon/core/arrow-up";
import AttachmentIcon from "@atlaskit/icon/core/attachment";
import AutomationIcon from "@atlaskit/icon/core/automation";
import BranchIcon from "@atlaskit/icon/core/branch";
import CalendarIcon from "@atlaskit/icon/core/calendar";
import CashIcon from "@atlaskit/icon/core/cash";
import ChangesIcon from "@atlaskit/icon/core/changes";
import ChartBarIcon from "@atlaskit/icon/core/chart-bar";
import CheckCircleIcon from "@atlaskit/icon/core/check-circle";
import CheckMarkIcon from "@atlaskit/icon/core/check-mark";
import CheckboxUncheckedIcon from "@atlaskit/icon/core/checkbox-unchecked";
import ChevronDownIcon from "@atlaskit/icon/core/chevron-down";
import ChevronRightIcon from "@atlaskit/icon/core/chevron-right";
import ClockIcon from "@atlaskit/icon/core/clock";
import CommentIcon from "@atlaskit/icon/core/comment";
import CommentAddIcon from "@atlaskit/icon/core/comment-add";
import CrossIcon from "@atlaskit/icon/core/cross";
import CrossCircleIcon from "@atlaskit/icon/core/cross-circle";
import DashboardIcon from "@atlaskit/icon/core/dashboard";
import DeleteIcon from "@atlaskit/icon/core/delete";
import DownloadIcon from "@atlaskit/icon/core/download";
import EditIcon from "@atlaskit/icon/core/edit";
import ExpandVerticalIcon from "@atlaskit/icon/core/expand-vertical";
import EyeOpenIcon from "@atlaskit/icon/core/eye-open";
import EyeOpenStrikethroughIcon from "@atlaskit/icon/core/eye-open-strikethrough";
import FileIcon from "@atlaskit/icon/core/file";
import FolderClosedIcon from "@atlaskit/icon/core/folder-closed";
import GridIcon from "@atlaskit/icon/core/grid";
import HashtagIcon from "@atlaskit/icon/core/hashtag";
import ImageIcon from "@atlaskit/icon/core/image";
import InformationCircleIcon from "@atlaskit/icon/core/information-circle";
import LayoutThreeColumnsIcon from "@atlaskit/icon/core/layout-three-columns";
import LightbulbIcon from "@atlaskit/icon/core/lightbulb";
import LinkIcon from "@atlaskit/icon/core/link";
import LinkExternalIcon from "@atlaskit/icon/core/link-external";
import ListBulletedIcon from "@atlaskit/icon/core/list-bulleted";
import ListChecklistIcon from "@atlaskit/icon/core/list-checklist";
import ListNumberedIcon from "@atlaskit/icon/core/list-numbered";
import MinimizeIcon from "@atlaskit/icon/core/minimize";
import PanelRightIcon from "@atlaskit/icon/core/panel-right";
import PeopleGroupIcon from "@atlaskit/icon/core/people-group";
import PriorityHighIcon from "@atlaskit/icon/core/priority-high";
import PriorityHighestIcon from "@atlaskit/icon/core/priority-highest";
import PriorityLowIcon from "@atlaskit/icon/core/priority-low";
import PriorityLowestIcon from "@atlaskit/icon/core/priority-lowest";
import PriorityMediumIcon from "@atlaskit/icon/core/priority-medium";
import PersonIcon from "@atlaskit/icon/core/person";
import PersonAddIcon from "@atlaskit/icon/core/person-add";
import PulseIcon from "@atlaskit/icon/core/pulse";
import QuotationBlockIcon from "@atlaskit/icon/core/quotation-block";
import RadioCheckedIcon from "@atlaskit/icon/core/radio-checked";
import RadioUncheckedIcon from "@atlaskit/icon/core/radio-unchecked";
import RedoIcon from "@atlaskit/icon/core/redo";
import RefreshIcon from "@atlaskit/icon/core/refresh";
import RetryIcon from "@atlaskit/icon/core/retry";
import SearchIcon from "@atlaskit/icon/core/search";
import SendIcon from "@atlaskit/icon/core/send";
import SettingsIcon from "@atlaskit/icon/core/settings";
import ShowMoreHorizontalIcon from "@atlaskit/icon/core/show-more-horizontal";
import ShowMoreVerticalIcon from "@atlaskit/icon/core/show-more-vertical";
import SidebarCollapseIcon from "@atlaskit/icon/core/sidebar-collapse";
import SidebarExpandIcon from "@atlaskit/icon/core/sidebar-expand";
import SnippetIcon from "@atlaskit/icon/core/snippet";
import TagIcon from "@atlaskit/icon/core/tag";
import TextBoldIcon from "@atlaskit/icon/core/text-bold";
import TextItalicIcon from "@atlaskit/icon/core/text-italic";
import TextStrikethroughIcon from "@atlaskit/icon/core/text-strikethrough";
import ThemeIcon from "@atlaskit/icon/core/theme";
import UndoIcon from "@atlaskit/icon/core/undo";
import VideoPlayIcon from "@atlaskit/icon/core/video-play";
import WarningIcon from "@atlaskit/icon/core/warning";

export interface IconProps {
  className?: string;
  "aria-label"?: string;
}

// ADS core glyphs expose `shouldScale` to fill their container; it isn't in the
// public prop types and `color` is a narrow token union, so we accept the glyph
// loosely and pass the runtime props it actually supports.
function makeIcon(Glyph: ComponentType<any>) {
  function AdsIcon({ className, "aria-label": ariaLabel }: IconProps) {
    return (
      <span
        className={cn(
          "inline-flex h-4 w-4 shrink-0 items-center justify-center [&>span]:contents",
          className,
        )}
        aria-hidden={ariaLabel ? undefined : true}
      >
        <Glyph label={ariaLabel ?? ""} color="currentColor" shouldScale />
      </span>
    );
  }
  return AdsIcon;
}

/* lucide name → closest ADS core glyph. A handful are approximations where
   ADS has no 1:1 match (noted): Brain/Sparkles→ai-sparkle, Cpu→automation,
   Save→download, Scissors→minimize, Sun→lightbulb, Moon→theme,
   Gauge→dashboard, Play→video-play, ChevronsUpDown→expand-vertical. */
export const Activity = makeIcon(PulseIcon);
export const AlertTriangle = makeIcon(WarningIcon);
export const AlignLeft = makeIcon(AlignTextLeftIcon);
export const Archive = makeIcon(ArchiveBoxIcon);
export const ArrowDown = makeIcon(ArrowDownIcon);
export const ArrowLeft = makeIcon(ArrowLeftIcon);
export const ArrowRight = makeIcon(ArrowRightIcon);
export const ArrowUp = makeIcon(ArrowUpIcon);
export const BarChart3 = makeIcon(ChartBarIcon);
export const Bold = makeIcon(TextBoldIcon);
export const Bot = makeIcon(AiAgentIcon);
export const Brain = makeIcon(AiSparkleIcon);
export const Calendar = makeIcon(CalendarIcon);
export const CalendarClock = makeIcon(CalendarIcon);
export const Check = makeIcon(CheckMarkIcon);
export const CheckCircle2 = makeIcon(CheckCircleIcon);
export const ChevronDown = makeIcon(ChevronDownIcon);
export const ChevronRight = makeIcon(ChevronRightIcon);
export const ChevronsUpDown = makeIcon(ExpandVerticalIcon);
export const Circle = makeIcon(RadioUncheckedIcon);
export const CircleDot = makeIcon(RadioCheckedIcon);
export const CircleSlash = makeIcon(CrossCircleIcon);
export const Clock = makeIcon(ClockIcon);
export const Code = makeIcon(AngleBracketsIcon);
export const Code2 = makeIcon(SnippetIcon);
export const Coins = makeIcon(CashIcon);
export const Columns3 = makeIcon(LayoutThreeColumnsIcon);
export const Cpu = makeIcon(AutomationIcon);
export const Download = makeIcon(DownloadIcon);
export const ExternalLink = makeIcon(LinkExternalIcon);
export const Eye = makeIcon(EyeOpenIcon);
export const EyeOff = makeIcon(EyeOpenStrikethroughIcon);
export const File = makeIcon(FileIcon);
export const FileDiff = makeIcon(ChangesIcon);
export const FileImage = makeIcon(ImageIcon);
export const FileText = makeIcon(FileIcon);
export const Folder = makeIcon(FolderClosedIcon);
export const FolderGit2 = makeIcon(BranchIcon);
export const Gauge = makeIcon(DashboardIcon);
export const GitBranch = makeIcon(BranchIcon);
export const Hash = makeIcon(HashtagIcon);
export const History = makeIcon(ClockIcon);
export const Info = makeIcon(InformationCircleIcon);
export const Italic = makeIcon(TextItalicIcon);
export const LayoutGrid = makeIcon(GridIcon);
export const Link = makeIcon(LinkIcon);
export const List = makeIcon(ListBulletedIcon);
export const ListChecks = makeIcon(ListChecklistIcon);
export const ListOrdered = makeIcon(ListNumberedIcon);
export const Loader2 = makeIcon(RefreshIcon);
export const MessageSquarePlus = makeIcon(CommentAddIcon);
export const MessagesSquare = makeIcon(CommentIcon);
export const Moon = makeIcon(ThemeIcon);
export const MoreHorizontal = makeIcon(ShowMoreHorizontalIcon);
export const MoreVertical = makeIcon(ShowMoreVerticalIcon);
export const PanelLeftClose = makeIcon(SidebarCollapseIcon);
export const PanelLeftOpen = makeIcon(SidebarExpandIcon);
export const PanelRightClose = makeIcon(PanelRightIcon);
export const PanelRightOpen = makeIcon(PanelRightIcon);
export const Paperclip = makeIcon(AttachmentIcon);
export const Pencil = makeIcon(EditIcon);
export const Play = makeIcon(VideoPlayIcon);
export const Plus = makeIcon(AddIcon);
export const PriorityHighest = makeIcon(PriorityHighestIcon);
export const PriorityHigh = makeIcon(PriorityHighIcon);
export const PriorityMedium = makeIcon(PriorityMediumIcon);
export const PriorityLow = makeIcon(PriorityLowIcon);
export const PriorityLowest = makeIcon(PriorityLowestIcon);
export const Quote = makeIcon(QuotationBlockIcon);
export const Redo2 = makeIcon(RedoIcon);
export const RefreshCw = makeIcon(RefreshIcon);
export const RotateCcw = makeIcon(RetryIcon);
export const Save = makeIcon(DownloadIcon);
export const Scissors = makeIcon(MinimizeIcon);
export const Search = makeIcon(SearchIcon);
export const Send = makeIcon(SendIcon);
export const Settings = makeIcon(SettingsIcon);
export const Sparkles = makeIcon(AiSparkleIcon);
export const Square = makeIcon(CheckboxUncheckedIcon);
export const SquareCode = makeIcon(SnippetIcon);
export const Strikethrough = makeIcon(TextStrikethroughIcon);
export const Sun = makeIcon(LightbulbIcon);
export const Tag = makeIcon(TagIcon);
export const TerminalSquare = makeIcon(AngleBracketsIcon);
export const Trash2 = makeIcon(DeleteIcon);
export const Undo2 = makeIcon(UndoIcon);
export const UserPlus = makeIcon(PersonAddIcon);
export const UserRound = makeIcon(PersonIcon);
export const Users = makeIcon(PeopleGroupIcon);
export const X = makeIcon(CrossIcon);
export const XCircle = makeIcon(CrossCircleIcon);
