// Runs tab · "How each stage was called": the selected run's manifest, exactly what the worker
// received. Closed by default; the manifest (which carries every context file) is fetched only once
// the section is opened. Logic lives in `manifest.ts`.
import { useEffect, useRef, useState, type ReactNode } from "react";

import { useManifest } from "../../api/queries";
import { ChevronIcon, cx, Tag } from "../../ui";
import {
  filePaths,
  manifestErrorText,
  promptLabel,
  stageCall,
  timeoutText,
  type Manifest,
  type StageCall,
  type TokenKind,
} from "./manifest";
import s from "./RunsTab.module.css";
import type { RunStatus } from "./runFormat";

const TOKEN_CLASS: Record<TokenKind, string | undefined> = {
  program: s.tProgram,
  flag: s.tFlag,
  arg: s.tArg,
  prompt: s.tPrompt,
};

function Summary({ children }: { children: ReactNode }) {
  return (
    <summary className={s.foldHead}>
      <span className={s.chev}>
        <ChevronIcon />
      </span>
      {children}
    </summary>
  );
}

function CallItem({ call }: { call: StageCall }) {
  return (
    <li className={s.call}>
      <div className={s.callHead}>
        <span className={s.callName}>{call.name}</span>
        <span className={s.mono}>
          {call.kind === "shell" ? "shell · " : ""}
          {timeoutText(call.timeoutS)} limit
        </span>
      </div>
      {call.chips.length > 0 ? (
        <div className={s.chips}>
          {call.chips.map((c) => (
            <Tag key={c.label} quiet>
              <span className={s.chipK}>{c.label}</span>
              <span className={s.chipV}>{c.value}</span>
            </Tag>
          ))}
        </div>
      ) : null}
      {call.tokens ? (
        <code className={s.cmd} aria-label={`${call.name} command`}>
          {call.tokens.map((t, i) => (
            <span key={i}>
              {i > 0 ? " " : ""}
              <span className={TOKEN_CLASS[t.kind]}>{t.text}</span>
            </span>
          ))}
        </code>
      ) : (
        <code className={s.cmd} aria-label={`${call.name} command`}>
          {call.command}
        </code>
      )}
      {call.prompt ? (
        <details className={s.fold}>
          <Summary>{promptLabel(call.prompt.via)}</Summary>
          <pre className={s.pre}>{call.prompt.text}</pre>
        </details>
      ) : null}
    </li>
  );
}

/** The manifest's stages and file list. Pure: props in, markup out. */
export function ManifestBody({ manifest }: { manifest: Manifest }) {
  const paths = filePaths(manifest);
  return (
    <div className={s.calls}>
      <ol className={s.callList} aria-label="Stage calls">
        {manifest.stages.map((st) => (
          <CallItem key={st.id} call={stageCall(st)} />
        ))}
      </ol>
      {paths.length > 0 ? (
        <details className={s.fold}>
          <Summary>Files given to the agent ({paths.length})</Summary>
          <ul className={s.paths}>
            {paths.map((p) => (
              <li key={p}>{p}</li>
            ))}
          </ul>
        </details>
      ) : (
        <p className={s.quiet}>No files given to the agent</p>
      )}
    </div>
  );
}

function ManifestLoader({ runId, status }: { runId: number; status: RunStatus }) {
  const q = useManifest(runId);
  // A queued run has no manifest yet (404): look again once the run moves on.
  const { refetch, isError } = q;
  const seen = useRef(status);
  useEffect(() => {
    if (seen.current === status) return;
    seen.current = status;
    if (isError) void refetch();
  }, [status, isError, refetch]);

  if (q.isPending) return <p className={s.quiet}>Loading</p>;
  if (q.isError) {
    const e = manifestErrorText(q.error);
    return (
      <p className={s.quiet} data-error={e.quiet ? undefined : ""}>
        {e.text}
      </p>
    );
  }
  return <ManifestBody manifest={q.data} />;
}

/** The collapsible section for one run (its panel is keyed by run, so it starts closed). */
export function StageCalls({ runId, status }: { runId: number; status: RunStatus }) {
  const [open, setOpen] = useState(false);
  return (
    <details
      className={cx(s.fold, s.section)}
      onToggle={(e) => {
        setOpen(e.currentTarget.open);
      }}
    >
      <Summary>How each stage was called</Summary>
      {open ? <ManifestLoader runId={runId} status={status} /> : null}
    </details>
  );
}
