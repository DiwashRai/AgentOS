// Ticket page · Context tab (SPEC §13 Context, §6 versioned files): the bundle's files, and the
// selected one (`?file=<path>`, default TASKS.md) in a plain monospace editor. This is where the
// human edits the plan between a gate and Approve. Markdown preview, the version list and diffs
// come later.
import { useCallback, useEffect, useMemo, useRef, useState, type SyntheticEvent } from "react";
import { Link as RouterLink, useBlocker, useSearchParams } from "react-router";

import { ApiError } from "../../api/client";
import { useDeleteFile, useFile, useFiles, useMe, useSaveFile } from "../../api/queries";
import { age, formatSize } from "../../domain/format";
import { Button, Chip, cx, Kbd, LinkButton, linkClass, Tag } from "../../ui";
import { useNow } from "../../ui/useNow";
import { ticketPath } from "../ticket/model";
import {
  AGENT_NAME_RULE,
  agentExists,
  agentNameFromPath,
  agentNameProblem,
  agentPath,
  agentStarter,
} from "./agents";
import s from "./ContextTab.module.css";
import { LOCKED_TEXT, writeProblem, type WriteProblem } from "./errors";
import { authorLabel, isTasksFile, sortFiles, splitPath, type FileInfo } from "./files";

/** An edit in progress: the version and text it started from, and the text now. */
interface Draft {
  baseVersion: number;
  baseContent: string;
  text: string;
}

type DraftUpdate = (prev: Draft | undefined) => Draft | undefined;

function isDirty(d: Draft | undefined): boolean {
  return d !== undefined && d.text !== d.baseContent;
}

const BLOCK_TITLE = "Block mode: appended as a marked block to the repo's file at this path";

// ------------------------------------------------------------------ file list

function FileRow({
  file,
  selected,
  dirty,
  now,
  onSelect,
}: {
  file: FileInfo;
  selected: boolean;
  dirty: boolean;
  now: number;
  onSelect: () => void;
}) {
  const { dir, name } = splitPath(file.path);
  const updated = age(file.updated_at, now);
  return (
    <li>
      <button
        type="button"
        className={cx(s.file, selected && s.selected)}
        aria-current={selected}
        title={file.path}
        onClick={onSelect}
      >
        <span className={s.fileTop}>
          <span className={s.fileName}>{name}</span>
          {file.mode === "block" ? (
            <Tag className={s.tag} title={BLOCK_TITLE}>
              Block
            </Tag>
          ) : null}
          {dirty ? <span className={s.unsaved}>Unsaved</span> : null}
        </span>
        {dir ? <span className={s.fileDir}>{dir}/</span> : null}
        <span className={s.fileMeta}>
          v{file.version} · {formatSize(file.size)} · {authorLabel(file)} ·{" "}
          {updated === "now" ? "just now" : updated}
        </span>
      </button>
    </li>
  );
}

/** Which add form is open under the file list, if any. */
type Adding = "file" | "agent" | null;

function AddFile({
  ticketKey,
  runActive,
  onClose,
  onCreated,
}: {
  ticketKey: string;
  runActive: boolean;
  onClose: () => void;
  onCreated: (p: string) => void;
}) {
  const create = useSaveFile(ticketKey);
  const [path, setPath] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);
  // The form opens on an explicit click, so focus moves into it.
  useEffect(() => {
    input.current?.focus();
  }, []);

  const close = () => {
    create.reset();
    onClose();
  };

  const submit = (e: SyntheticEvent) => {
    e.preventDefault();
    if (runActive) return;
    const p = path.trim();
    if (!p) {
      setProblem("Give the file a path, like notes/approach.md.");
      return;
    }
    setProblem(null);
    create.mutate(
      { path: p, content: "", mode: "replace", base_version: 0 },
      {
        onSuccess: () => {
          close();
          onCreated(p);
        },
        onError: (err) => {
          setProblem(writeProblem(err, "create").text);
        },
      },
    );
  };

  return (
    <form className={s.addForm} onSubmit={submit}>
      <label className={s.k} htmlFor="context-new-path">
        New file path
      </label>
      <input
        ref={input}
        id="context-new-path"
        className={s.input}
        value={path}
        placeholder="notes/approach.md"
        spellCheck={false}
        autoComplete="off"
        onChange={(e) => {
          setPath(e.target.value);
        }}
        onKeyDown={(e) => {
          if (e.key === "Escape") close();
        }}
      />
      {problem ? (
        <p className={s.problem} role="alert">
          {problem}
        </p>
      ) : null}
      {runActive ? (
        <p className={s.note} role="status">
          {LOCKED_TEXT}
        </p>
      ) : null}
      <span className={s.row}>
        <Button type="submit" variant="ink" size="sm" disabled={runActive || create.isPending}>
          {create.isPending ? "Creating…" : "Create"}
        </Button>
        <Button variant="quiet" size="sm" onClick={close}>
          Cancel
        </Button>
      </span>
    </form>
  );
}

/** "Set a stage's Agent to reviewer in the Pipeline tab to use it." */
function AgentHint({ ticketKey, name }: { ticketKey: string; name: string }) {
  return (
    <p className={s.note}>
      {`Set a stage's Agent to ${name} in the `}
      <RouterLink className={linkClass} to={ticketPath(ticketKey, "pipeline")}>
        Pipeline tab
      </RouterLink>
      {" to use it."}
    </p>
  );
}

/** A shortcut for `.claude/agents/<name>.md`, the file `claude --agent <name>` reads. It asks only
 *  for the name and writes a starter file through the same PUT as "+ Add context". */
function AddAgent({
  ticketKey,
  files,
  runActive,
  onClose,
  onCreated,
  onOpen,
}: {
  ticketKey: string;
  files: readonly FileInfo[];
  runActive: boolean;
  onClose: () => void;
  onCreated: (p: string) => void;
  /** Open a file that's already in the bundle. */
  onOpen: (p: string) => void;
}) {
  const create = useSaveFile(ticketKey);
  const [name, setName] = useState("");
  const [problem, setProblem] = useState<string | null>(null);
  const input = useRef<HTMLInputElement>(null);
  useEffect(() => {
    input.current?.focus();
  }, []);

  const n = name.trim();
  // The rule shows as a hint until something is typed; then an invalid name shows it as the error.
  const invalid = n === "" ? null : agentNameProblem(n);
  const path = invalid === null && n !== "" ? agentPath(n) : null;
  const exists = path !== null && agentExists(files, n);

  const close = () => {
    create.reset();
    onClose();
  };

  const submit = (e: SyntheticEvent) => {
    e.preventDefault();
    if (runActive || exists) return;
    const bad = agentNameProblem(n);
    if (bad !== null) {
      setProblem(bad);
      return;
    }
    setProblem(null);
    const p = agentPath(n);
    create.mutate(
      { path: p, content: agentStarter(n), mode: "replace", base_version: 0 },
      {
        onSuccess: () => {
          close();
          onCreated(p);
        },
        onError: (err) => {
          setProblem(writeProblem(err, "create").text);
        },
      },
    );
  };

  // Typing clears a submit-time problem; the live checks (rule, exists) take over.
  const shown = problem ?? invalid;

  return (
    <form className={s.addForm} onSubmit={submit}>
      <label className={s.k} htmlFor="context-agent-name">
        Agent name
      </label>
      <input
        ref={input}
        id="context-agent-name"
        className={s.input}
        value={name}
        placeholder="reviewer"
        spellCheck={false}
        autoComplete="off"
        aria-describedby="context-agent-rule"
        aria-invalid={invalid !== null || exists}
        onChange={(e) => {
          setName(e.target.value);
          setProblem(null);
        }}
        onKeyDown={(e) => {
          if (e.key === "Escape") close();
        }}
      />
      {shown ? (
        <p className={s.problem} id="context-agent-rule" role="alert">
          {shown}
        </p>
      ) : (
        <p className={s.note} id="context-agent-rule">
          {AGENT_NAME_RULE}
        </p>
      )}
      <p className={s.preview} title={path ?? undefined}>
        {path ?? agentPath("<name>")}
      </p>
      {exists ? (
        <p className={s.problem} role="alert">
          {"This agent is already in the bundle. "}
          <LinkButton
            onClick={() => {
              close();
              onOpen(agentPath(n));
            }}
          >
            Open it
          </LinkButton>
        </p>
      ) : null}
      {runActive ? (
        <p className={s.note} role="status">
          {LOCKED_TEXT}
        </p>
      ) : null}
      <span className={s.row}>
        <Button
          type="submit"
          variant="ink"
          size="sm"
          disabled={runActive || exists || create.isPending}
        >
          {create.isPending ? "Creating…" : "Create"}
        </Button>
        <Button variant="quiet" size="sm" onClick={close}>
          Cancel
        </Button>
      </span>
      <AgentHint ticketKey={ticketKey} name={path !== null ? n : "its name"} />
    </form>
  );
}

// ------------------------------------------------------------------ editor

function FileEditor({
  ticketKey,
  path,
  listed,
  draft,
  runActive,
  onDraft,
  onDeleted,
}: {
  ticketKey: string;
  path: string;
  /** The file's row in the (polled) list; undefined if the list doesn't have it. */
  listed: FileInfo | undefined;
  draft: Draft | undefined;
  runActive: boolean;
  onDraft: (path: string, update: DraftUpdate) => void;
  onDeleted: () => void;
}) {
  const file = useFile(ticketKey, path);
  const save = useSaveFile(ticketKey);
  const del = useDeleteFile(ticketKey);
  const [problem, setProblem] = useState<WriteProblem | null>(null);
  const [confirmDelete, setConfirmDelete] = useState(false);

  // The list polls; the file doesn't. When a run (or a teammate) writes a newer version, reload.
  const { refetch, isFetching } = file;
  const loadedVersion = file.data?.version;
  const listedVersion = listed?.version;
  useEffect(() => {
    if (listedVersion === undefined || loadedVersion === undefined || isFetching) return;
    if (listedVersion !== loadedVersion) void refetch();
  }, [listedVersion, loadedVersion, isFetching, refetch]);

  if (file.isPending) return <p className={s.quiet}>{`Loading ${splitPath(path).name}`}</p>;
  if (file.isError) {
    return (
      <p className={s.quiet} data-error="" role="alert">
        {file.error instanceof ApiError && file.error.code === "file_not_found"
          ? "No file at this path. Pick one from the list."
          : `Can't load this file · ${file.error.message}`}
      </p>
    );
  }

  const server = file.data;
  const dirty = isDirty(draft);
  // A clean draft is kept just after a save, until the refetch catches up with it; once the
  // server moves past it (a run wrote the file), the server's text wins.
  const live = draft && (dirty || draft.baseVersion >= server.version) ? draft : undefined;
  const value = live ? live.text : server.content;
  const editable = !runActive;

  const onChange = (text: string) => {
    onDraft(path, (prev) => {
      const keep = prev && (isDirty(prev) || prev.baseVersion >= server.version);
      const base = keep
        ? prev
        : { baseVersion: server.version, baseContent: server.content, text: server.content };
      return { ...base, text };
    });
  };

  const doSave = () => {
    if (!live || !dirty || !editable || save.isPending) return;
    const sent = live.text;
    setProblem(null);
    save.mutate(
      { path, content: sent, mode: server.mode, base_version: live.baseVersion },
      {
        onSuccess: (info) => {
          // Keep typing that happened during the save; it is now based on the new version.
          onDraft(path, (prev) => ({
            baseVersion: info.version,
            baseContent: sent,
            text: prev?.text ?? sent,
          }));
        },
        onError: (err) => {
          setProblem(writeProblem(err, "save"));
        },
      },
    );
  };

  const revert = () => {
    onDraft(path, () => undefined);
    setProblem(null);
  };

  const reload = () => {
    revert();
    void refetch();
  };

  const doDelete = () => {
    setProblem(null);
    del.mutate(
      { path, baseVersion: server.version },
      {
        onSuccess: () => {
          onDraft(path, () => undefined);
          setConfirmDelete(false);
          onDeleted();
        },
        onError: (err) => {
          setConfirmDelete(false);
          setProblem(writeProblem(err, "delete"));
        },
      },
    );
  };

  const { dir, name } = splitPath(path);
  const agent = agentNameFromPath(path);

  return (
    <div className={s.editor}>
      <div className={s.editorHead}>
        <div className={s.editorTitle}>
          <span className={s.editorName} title={path}>
            {name}
          </span>
          <span className={s.k}>v{server.version}</span>
          {server.mode === "block" ? <Tag title={BLOCK_TITLE}>Block</Tag> : null}
          {dirty ? <span className={s.unsaved}>Unsaved</span> : null}
        </div>
        <div className={s.row}>
          {editable && !confirmDelete ? (
            <Button
              variant="quiet"
              size="sm"
              onClick={() => {
                setConfirmDelete(true);
              }}
            >
              Delete
            </Button>
          ) : null}
          <Button size="sm" disabled={!dirty || save.isPending} onClick={revert}>
            Revert
          </Button>
          <Button
            variant="ink"
            size="sm"
            disabled={!dirty || !editable || save.isPending}
            onClick={doSave}
          >
            {save.isPending ? "Saving…" : "Save"}
          </Button>
        </div>
      </div>

      {confirmDelete ? (
        <div className={s.banner} role="alert">
          <span>{`Delete ${name}? The agent won't see it on the next run.`}</span>
          <span className={s.row}>
            <Button size="sm" disabled={del.isPending} onClick={doDelete}>
              {del.isPending ? "Deleting…" : "Delete file"}
            </Button>
            <Button
              variant="quiet"
              size="sm"
              onClick={() => {
                setConfirmDelete(false);
              }}
            >
              Keep
            </Button>
          </span>
        </div>
      ) : null}

      {runActive ? (
        <p className={s.banner} role="status">
          {dirty ? `${LOCKED_TEXT} Your unsaved edits stay here until then.` : LOCKED_TEXT}
        </p>
      ) : null}

      {problem ? (
        <div className={cx(s.banner, s.bad)} role="alert">
          <span>{problem.text}</span>
          {problem.reload ? (
            <Button size="sm" onClick={reload}>
              Reload
            </Button>
          ) : null}
        </div>
      ) : null}

      {agent !== null ? <AgentHint ticketKey={ticketKey} name={agent} /> : null}

      <textarea
        className={s.textarea}
        aria-label={`Contents of ${path}`}
        value={value}
        readOnly={!editable}
        spellCheck={false}
        onChange={(e) => {
          onChange(e.target.value);
        }}
        onKeyDown={(e) => {
          if ((e.ctrlKey || e.metaKey) && e.key.toLowerCase() === "s") {
            e.preventDefault();
            doSave();
          }
        }}
      />
      <p className={s.foot}>
        <span className={s.footPath}>
          {`${dir ? `${dir}/` : ""}${name} · ${formatSize(server.size)} · last written by ${authorLabel(server)}`}
        </span>
        {editable ? (
          <span className={s.footHint}>
            <Kbd>Ctrl S</Kbd> saves
          </span>
        ) : null}
      </p>
    </div>
  );
}

// ------------------------------------------------------------------ tab

export function ContextTab({ ticketKey, runActive }: { ticketKey: string; runActive: boolean }) {
  const filesQ = useFiles(ticketKey);
  const me = useMe();
  const canWrite = me.data?.role === "writer" || me.data?.role === "admin";
  const [adding, setAdding] = useState<Adding>(null);
  const [params, setParams] = useSearchParams();
  const now = useNow(30_000);
  // Drafts per path, so switching files keeps edits. Reset when the ticket changes.
  const [drafts, setDrafts] = useState<{ ticket: string; byPath: Record<string, Draft> }>({
    ticket: ticketKey,
    byPath: {},
  });
  const byPath = drafts.ticket === ticketKey ? drafts.byPath : {};

  const files = useMemo(() => sortFiles(filesQ.data ?? []), [filesQ.data]);
  const asked = params.get("file");
  const selected = asked ?? files[0]?.path ?? null;
  const dirtyPaths = Object.keys(byPath).filter((p) => isDirty(byPath[p]));
  const anyDirty = dirtyPaths.length > 0;

  const onDraft = useCallback(
    (path: string, update: DraftUpdate) => {
      setDrafts((prev) => {
        const map = prev.ticket === ticketKey ? { ...prev.byPath } : {};
        const next = update(map[path]);
        if (next === undefined) {
          // Removing the key: rebuild without it rather than `delete` on a computed key.
          return {
            ticket: ticketKey,
            byPath: Object.fromEntries(Object.entries(map).filter(([p]) => p !== path)),
          };
        }
        map[path] = next;
        return { ticket: ticketKey, byPath: map };
      });
    },
    [ticketKey],
  );

  const select = useCallback(
    (path: string | null) => {
      setParams(
        (prev) => {
          const next = new URLSearchParams(prev);
          if (path === null) next.delete("file");
          else next.set("file", path);
          return next;
        },
        { replace: true },
      );
    },
    [setParams],
  );

  // Unsaved edits: warn before the page unloads, and before leaving this tab in the app.
  useEffect(() => {
    if (!anyDirty) return;
    const onBeforeUnload = (e: BeforeUnloadEvent) => {
      e.preventDefault();
    };
    window.addEventListener("beforeunload", onBeforeUnload);
    return () => {
      window.removeEventListener("beforeunload", onBeforeUnload);
    };
  }, [anyDirty]);
  const blocker = useBlocker(
    ({ currentLocation, nextLocation }) =>
      anyDirty && currentLocation.pathname !== nextLocation.pathname,
  );
  useEffect(() => {
    if (blocker.state !== "blocked") return;
    const n = dirtyPaths.length;
    const what = n === 1 ? "1 context file" : `${String(n)} context files`;
    if (window.confirm(`You have unsaved edits in ${what}. Leave and lose them?`)) {
      blocker.proceed();
    } else {
      blocker.reset();
    }
  }, [blocker, dirtyPaths.length]);

  if (filesQ.isPending) return <p className={s.quiet}>Loading files</p>;
  if (filesQ.isError) {
    return (
      <p className={s.quiet} data-error="" role="alert">
        {`Can't load files · ${filesQ.error.message}`}
      </p>
    );
  }

  const closeAdd = () => {
    setAdding(null);
  };
  const listed = selected === null ? undefined : files.find((f) => f.path === selected);
  const hasTasks = files.some((f) => isTasksFile(f.path));

  return (
    <div className={s.tab}>
      <div className={s.side}>
        <div className={s.sideHead}>
          <span className={s.k}>The agent reads</span>
          <span className={s.k}>{files.length}</span>
        </div>
        {files.length === 0 ? (
          <p className={s.emptyNote}>No context files yet.</p>
        ) : (
          <ul className={s.files} aria-label="Context files">
            {files.map((f) => (
              <FileRow
                key={f.path}
                file={f}
                now={now}
                selected={f.path === selected}
                dirty={isDirty(byPath[f.path])}
                onSelect={() => {
                  select(f.path);
                }}
              />
            ))}
          </ul>
        )}
        {!hasTasks && files.length > 0 ? (
          <p className={s.emptyNote}>No TASKS.md yet. The plan stage writes it.</p>
        ) : null}
        {!canWrite ? null : adding === "file" ? (
          <AddFile
            ticketKey={ticketKey}
            runActive={runActive}
            onClose={closeAdd}
            onCreated={select}
          />
        ) : adding === "agent" ? (
          <AddAgent
            ticketKey={ticketKey}
            files={files}
            runActive={runActive}
            onClose={closeAdd}
            onCreated={select}
            onOpen={select}
          />
        ) : (
          <div className={s.adds}>
            <Chip
              add
              onClick={() => {
                setAdding("file");
              }}
            >
              + Add context
            </Chip>
            <Chip
              add
              title="A Claude Code agent: .claude/agents/<name>.md"
              onClick={() => {
                setAdding("agent");
              }}
            >
              + Add an agent
            </Chip>
          </div>
        )}
      </div>
      <div className={s.main}>
        {selected === null ? (
          <p className={s.quiet}>Add a file to give the agent context</p>
        ) : (
          <FileEditor
            key={selected}
            ticketKey={ticketKey}
            path={selected}
            listed={listed}
            draft={byPath[selected]}
            runActive={runActive}
            onDraft={onDraft}
            onDeleted={() => {
              select(null);
            }}
          />
        )}
      </div>
    </div>
  );
}
