// Runs tab · "How each stage was called": the selected run's job package, exactly what the worker
// received. Closed by default; the package (which carries every context file) is fetched only once
// the section is opened. Logic lives in `manifest.ts`.
import { useEffect, useRef, useState } from "react";

import { useManifest } from "../../api/queries";
import { cx, EmptyState, Fold, Inline, Stack, StatusLine, Tag, text } from "../../ui";
import {
  laterLabel,
  manifestErrorText,
  packageFiles,
  promptLabel,
  stageCall,
  timeoutText,
  type LaterCall,
  type Manifest,
  type StageCall,
  type CommandToken,
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

/** A command line, each token in its kind's style; it wraps, never scrolls sideways. */
function Command({
  tokens,
  label,
}: {
  tokens: readonly CommandToken[];
  label?: string | undefined;
}) {
  return (
    <code className={s.cmd} aria-label={label}>
      {tokens.map((t, i) => (
        <span key={i}>
          {i > 0 ? " " : ""}
          <span className={TOKEN_CLASS[t.kind]}>{t.text}</span>
        </span>
      ))}
    </code>
  );
}

function CallItem({ call }: { call: StageCall }) {
  return (
    <Stack as="li" gap={4} className={s.call}>
      <Inline gap={6} align="baseline" justify="between">
        <span className={cx(s.callName, text.ellipsis)}>{call.name}</span>
        <span className={s.mono}>
          {[
            call.kind === "shell" ? "shell" : "",
            ...call.check,
            `${timeoutText(call.timeoutS)} limit`,
          ]
            .filter(Boolean)
            .join(" · ")}
        </span>
      </Inline>
      {call.chips.length > 0 ? (
        <Inline gap={3} wrap>
          {call.chips.map((c) => (
            <Tag key={c.label} quiet>
              <span className={s.chipK}>{c.label}</span>
              <span className={s.chipV}>{c.value}</span>
            </Tag>
          ))}
        </Inline>
      ) : null}
      {call.tokens ? (
        <Command tokens={call.tokens} label={`${call.name} command`} />
      ) : (
        <code className={s.cmd} aria-label={`${call.name} command`}>
          {call.command}
        </code>
      )}
      {call.prompt ? (
        <Fold summary={promptLabel(call.prompt.via)}>
          <pre className={s.pre}>{call.prompt.text}</pre>
        </Fold>
      ) : null}
      {call.later.length > 0 ? <LaterCalls calls={call.later} /> : null}
    </Stack>
  );
}

/** A loop stage's later-round and repair calls, folded: each its command and prompt. */
function LaterCalls({ calls }: { calls: LaterCall[] }) {
  return (
    <Fold summary={laterLabel(calls)}>
      <Stack as="ol" gap={5} className={s.later}>
        {calls.map((c) => (
          <Stack as="li" gap={2} key={c.key}>
            <span className={text.label}>
              {c.kind === "round"
                ? `Round ${String(c.round)}`
                : `Repair in round ${String(c.round)}`}
            </span>
            <Command tokens={c.tokens} />
            <pre className={cx(s.pre, s.laterPre)}>{c.prompt}</pre>
          </Stack>
        ))}
      </Stack>
    </Fold>
  );
}

/** The package's stages and file list. Pure: props in, markup out. */
export function ManifestBody({ manifest }: { manifest: Manifest }) {
  const files = packageFiles(manifest);
  return (
    <Stack gap={6}>
      {(manifest.warnings ?? []).map((w, i) => (
        <p key={i} className={s.callWarning} role="note">
          {w}
        </p>
      ))}
      <ol className={s.ruled} aria-label="Stage calls">
        {manifest.stages.map((st) => (
          <CallItem key={st.id} call={stageCall(st)} />
        ))}
      </ol>
      {files.length > 0 ? (
        <Fold summary={`Files given to the agent (${String(files.length)})`}>
          <ul className={cx(s.pre, s.paths)} aria-label="Files given to the agent">
            {files.map((f) => (
              <li key={f.path}>
                {f.path} <span className={s.fileKind}>· {f.kind}</span>
              </li>
            ))}
          </ul>
        </Fold>
      ) : (
        <EmptyState form="note">No files given to the agent</EmptyState>
      )}
    </Stack>
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

  if (q.isPending) return <StatusLine>Loading</StatusLine>;
  if (q.isError) {
    const e = manifestErrorText(q.error);
    return <StatusLine error={!e.quiet}>{e.text}</StatusLine>;
  }
  return <ManifestBody manifest={q.data} />;
}

/** The collapsible section for one run (its panel is keyed by run, so it starts closed). */
export function StageCalls({ runId, status }: { runId: number; status: RunStatus }) {
  const [open, setOpen] = useState(false);
  return (
    <Fold summary="How each stage was called" size="section" onToggle={setOpen}>
      {open ? (
        <div className={s.calls}>
          <ManifestLoader runId={runId} status={status} />
        </div>
      ) : null}
    </Fold>
  );
}
