// Kit primitives (design pack kit.css + the visual refresh): presentational only, props in, markup
// out, plus `useNow`, the clock that ages and elapsed times tick on.
export { Age, type AgeProps } from "./Age";
export {
  Avatar,
  AvatarSlot,
  type AvatarPalette,
  type AvatarProps,
  type AvatarSize,
  type AvatarSlotProps,
} from "./Avatar";
export {
  Button,
  IconButton,
  litRowClass,
  type ButtonProps,
  type ButtonVariant,
  type IconButtonProps,
} from "./Button";
export { Chip, type ChipProps } from "./Chip";
export { CountPill, type CountPillProps } from "./CountPill";
export { cx } from "./cx";
export { EmptyRow, type EmptyRowProps } from "./EmptyRow";
export { Faces, type FacesProps } from "./Faces";
export { GLYPH_SLOT, type StepperModel, type StepperStage } from "./glyph";
export { GroupHead, type GroupHeadProps } from "./GroupHead";
export { ArrowIcon, ChevronIcon, CloseIcon } from "./icons";
export { HUES, hueOf, keyHue, personHue, projectPrefix, type Hue } from "./identity";
export { avatarSlot, initials } from "./initials";
export { Kbd } from "./Kbd";
export { KeyChip, type KeyChipProps } from "./KeyChip";
export { Link, LinkButton, linkClass } from "./Link";
export type { MeterState } from "./meter";
export { PriorityIcon, type Priority, type PriorityIconProps } from "./PriorityIcon";
export { RunMeter, type RunMeterProps } from "./RunMeter";
export { StageGlyph, type StageGlyphProps } from "./StageGlyph";
export { MARK_WORD, StatusMark, YouPill, type MarkKind, type StatusMarkProps } from "./StatusMark";
export { StatusLine, type StatusLineProps } from "./StatusLine";
export { Stepper, type StepperProps } from "./Stepper";
export { Tabs, type TabItem, type TabsProps } from "./Tabs";
export { Tag, type TagProps } from "./Tag";
/** Text utilities (`label`, `meta`, `error`, `ellipsis`) as CSS module classes. */
export { default as text } from "./text.module.css";
export { TickBadge, type TickBadgeProps } from "./TickBadge";
export { TypeIcon, type TicketType, type TypeIconProps } from "./TypeIcon";
/** The ticking clock behind ages and elapsed times. */
export { useNow } from "./useNow";
export { ZoneHead, type ZoneHeadProps } from "./ZoneHead";
