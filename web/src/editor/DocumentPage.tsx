// An admin page over one or more versioned documents.
// - DocumentPage draws the page: the loading, admins-only (a non-admin viewer, or a 403 from any
//   read) and error states, then an optional header (title, meta) and description, then the page.
//   It holds the page's one leave warning, over every source's `dirty` plus its own `dirty` prop
//   (a plain draft on the same page): the router allows one blocker at a time.
// - DocumentSection draws one document: its save bar (saved-by line, unsaved marker, Discard / Save,
//   the save problem with Reload, form-level issues) above its fields. A page with two documents
//   stacks two sections, each saved on its own; a page with no document (Users) uses no section.
import type { ReactNode } from "react";

import { StatusLine, useNow } from "../ui";
import { issueCount } from "./issues";
import s from "./pages.module.css";
import { SaveBar } from "./SaveBar";
import { fixFirst, isForbidden, savedLine } from "./save";
import type { DocumentRead, DocumentState } from "./useDocument";
import { useUnsavedWarning } from "./useDraft";

/** Anything the page waits for: a document, or a plain query result. */
export interface Loadable {
  data: unknown;
  error: unknown;
  /** Unsaved changes (a document's draft): leaving the page asks first. */
  dirty?: boolean | undefined;
}

export interface DocumentPageProps {
  /** The signed-in person; undefined while loading. Only admins see the page. */
  viewer: { role: string } | null | undefined;
  /** What the page shows a non-admin (or on a 403). */
  adminsOnly: string;
  /** What is loading: "Loading {noun}", "Can't load {noun} · …". */
  noun: string;
  /** The documents and queries the page needs before it renders. */
  sources: readonly Loadable[];
  /** A page header; a page whose sections carry their own titles leaves it out. */
  title?: string | undefined;
  /** Beside the header title (e.g. a count). */
  meta?: string | undefined;
  /** A note under the header. */
  description?: ReactNode;
  /** Unsaved changes besides the sources' (a `useDraftState` draft on the same page). */
  dirty?: boolean | undefined;
  /** The page, rendered once every source has loaded. */
  children: ReactNode | (() => ReactNode);
}

export function DocumentPage(props: DocumentPageProps) {
  const { viewer, sources } = props;
  useUnsavedWarning(props.dirty === true || sources.some((x) => x.dirty === true));

  if (viewer === undefined) return <StatusLine>{`Loading ${props.noun}`}</StatusLine>;
  if (viewer?.role !== "admin" || sources.some((x) => isForbidden(x.error))) {
    return <StatusLine>{props.adminsOnly}</StatusLine>;
  }
  const failed = sources.find(
    (x) => x.data === undefined && x.error !== null && x.error !== undefined,
  );
  if (failed) {
    const why = failed.error instanceof Error ? failed.error.message : "";
    return <StatusLine error>{`Can't load ${props.noun} · ${why}`}</StatusLine>;
  }
  if (sources.some((x) => x.data === undefined)) {
    return <StatusLine>{`Loading ${props.noun}`}</StatusLine>;
  }

  return (
    <div className={s.page}>
      {props.title === undefined ? null : (
        <header className={s.bar}>
          <h2 className={s.title}>{props.title}</h2>
          {props.meta === undefined ? null : <span className={s.meta}>{props.meta}</span>}
        </header>
      )}
      {props.description === undefined ? null : (
        <p className={s.banner} role="note">
          {props.description}
        </p>
      )}
      {typeof props.children === "function" ? props.children() : props.children}
    </div>
  );
}

export interface DocumentSectionProps<R extends DocumentRead, F> {
  /** The save bar's title, and the section's accessible name. */
  title: string;
  document: DocumentState<R, F>;
  /** A note under the save bar. */
  description?: ReactNode;
  /** Controls before Discard / Save. */
  extra?: ReactNode;
  /** The document's fields. */
  children: (form: F, read: R) => ReactNode;
}

export function DocumentSection<R extends DocumentRead, F>(props: DocumentSectionProps<R, F>) {
  const doc = props.document;
  const now = useNow(60_000);
  if (doc.data === undefined || doc.form === undefined) return null;

  return (
    <section className={s.section} aria-label={props.title}>
      <SaveBar
        title={props.title}
        meta={savedLine(doc.data, now)}
        dirty={doc.dirty}
        readOnly={false}
        saveDisabledReason={fixFirst(issueCount(doc.issues))}
        saving={doc.saving}
        problem={doc.problem}
        issues={doc.issues.form}
        onSave={doc.save}
        onDiscard={doc.discard}
        onReload={doc.reload}
        extra={props.extra}
      />
      {props.description}
      {props.children(doc.form, doc.data)}
    </section>
  );
}
