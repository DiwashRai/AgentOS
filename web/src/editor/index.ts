// The editor kit: what every editable page shares (fields, the draft and its leave warning, the
// versioned document hook and its page, the save bar, save problems, the list editor, page styles).
// It imports ui/, domain/, api/client types, TanStack Query and react-router; never features/,
// app/ or api/queries.ts (pages pass their queries in).
export {
  DocumentPage,
  DocumentSection,
  type DocumentPageProps,
  type DocumentSectionProps,
  type Loadable,
} from "./DocumentPage";
export { Field, fieldStyles, Input, Select, Textarea, Toggle } from "./fields";
export {
  countWithin,
  errorAt,
  issueCount,
  issuesOf,
  messagesAt,
  NO_ISSUES,
  pathOf,
  placeIssues,
  type FieldPath,
  type Issues,
  type PlaceOptions,
  type SentKeys,
} from "./issues";
export { keyed, keysOf, moveRow, newKey, unkeyed, type Keyed } from "./list";
export {
  ListEditor,
  usePreview,
  type ListEditorProps,
  type ListRowContext,
  type Preview,
} from "./ListEditor";
export { default as pageStyles } from "./pages.module.css";
export { SaveBar, type SaveBarProps } from "./SaveBar";
export {
  describeDocumentError,
  describeSaveError,
  fixFirst,
  isForbidden,
  savedLine,
  type DocumentMeta,
  type SaveProblem,
} from "./save";
export {
  useDocument,
  VALIDATE_DELAY_MS,
  type DocumentMutation,
  type DocumentOptions,
  type DocumentQuery,
  type DocumentRead,
  type DocumentSave,
  type DocumentState,
  type FormConversion,
} from "./useDocument";
export { useDraft, useDraftState, useUnsavedWarning, type Draft } from "./useDraft";
