// Kit primitives (design pack kit.css + the visual refresh): presentational only, props in, markup
// out, plus `useNow`, the clock that ages and elapsed times tick on.
export { Age } from "./Age";
export { Avatar, AvatarSlot } from "./Avatar";
export { Badge } from "./Badge";
export { Button, IconButton, litRowClass, type ButtonVariant } from "./Button";
export { Checklist } from "./Checklist";
export { Chip } from "./Chip";
export { CountPill } from "./CountPill";
export { cx } from "./cx";
export {
  DataFoot,
  DataHead,
  DataLine,
  DataRow,
  DataRows,
  DataTitle,
  type DataColumn,
  type KeyHint,
} from "./DataList";
export { Dialog, dialogOpen, DialogTitle, reducedMotion } from "./Dialog";
export { EmptyState } from "./EmptyState";
export { Faces } from "./Faces";
export { Fold } from "./Fold";
export { GLYPH_SLOT } from "./glyph";
export { GroupHead } from "./GroupHead";
export { ArrowIcon, ChevronIcon, CloseIcon } from "./icons";
export { HUES, hueOf, keyHue, personHue, projectPrefix } from "./identity";
export { avatarSlot, initials } from "./initials";
export { Kbd } from "./Kbd";
export { KeyChip } from "./KeyChip";
export { Inline, Spacer, Stack } from "./Layout";
export { Link, LinkButton, linkClass } from "./Link";
export { PriorityIcon } from "./PriorityIcon";
export { RunMeter } from "./RunMeter";
export { StageGlyph } from "./StageGlyph";
export { StatusMark, YouPill, type MarkKind } from "./StatusMark";
export { StatusLine } from "./StatusLine";
export { Stepper } from "./Stepper";
export { Tabs, type TabItem } from "./Tabs";
export { Tag } from "./Tag";
/** Text utilities (`heading`, `label`, `meta`, `error`, `ellipsis`) as CSS module classes. */
export { default as text } from "./text.module.css";
export { TickBadge } from "./TickBadge";
export { TypeIcon } from "./TypeIcon";
/** The ticking clock behind ages and elapsed times. */
export { useNow } from "./useNow";
/** One document `keydown` listener (shortcuts, a dialog's outside keys). */
export { useDocumentKey } from "./useDocumentKey";
export { ZoneHead } from "./ZoneHead";
