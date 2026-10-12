// Talk (DESIGN §11, SPEC §13 "Chat pane"): the people-only chat on a ticket. Agents never read
// or post here. No typing indicator, presence, reactions, attach or @ (cut; see web/AGENTS.md).
import {
  useId,
  useLayoutEffect,
  useMemo,
  useRef,
  useState,
  type KeyboardEvent,
  type ReactNode,
} from "react";

import type { Schemas } from "../../api/client";
import { useChat, useMe, usePostChat } from "../../api/queries";
import { Avatar, Button, Faces, cx } from "../../ui";
import { linkify } from "./linkify";
import s from "./Talk.module.css";
import { clockTime, fullTime, groupThread, placeholder, speakers, type BlockItem } from "./thread";

export interface TalkProps {
  ticketKey: string;
  /** Peek only: show the last `limit` messages (default 8). */
  limit?: number;
  variant: "ticket" | "peek";
}

const NOTE = {
  ticket: "Just people here. Agents don't read this.",
  peek: "Just people here",
} as const;

/** Server limit (`ChatPost.body` maxLength). */
const MAX_CHARS = 4000;
/** Within this many px of the end counts as "at the bottom". */
const BOTTOM_SLACK = 24;
/** The composer grows to about 6 lines (20px line-height + 10px padding). */
const COMPOSER_MAX_PX = 6 * 20 + 10;

function Body({ text }: { text: string }) {
  return (
    <>
      {linkify(text).map((seg, i) =>
        seg.kind === "link" ? (
          <a key={i} className={s.link} href={seg.href} target="_blank" rel="noreferrer">
            {seg.text}
          </a>
        ) : (
          seg.text
        ),
      )}
    </>
  );
}

function Block({ block, newAfter }: { block: BlockItem; newAfter: number }) {
  const at = new Date(block.at);
  const time = (
    <time className={s.k} dateTime={block.at} title={fullTime(at)}>
      {clockTime(at)}
    </time>
  );
  const [first] = block.messages;
  const bubbles = block.messages.map((m) => (
    <span key={m.id} className={cx(s.bub, m !== first && m.id > newAfter && s.isNew)}>
      <Body text={m.body} />
    </span>
  ));
  const isNew = first !== undefined && first.id > newAfter;
  if (block.mine) {
    return (
      <div className={cx(s.msg, s.mine, isNew && s.isNew)}>
        <div>
          <div className={s.who}>
            {time}
            You
          </div>
          <div className={s.bs}>{bubbles}</div>
        </div>
      </div>
    );
  }
  return (
    <div className={cx(s.msg, isNew && s.isNew)}>
      <Avatar name={block.author.display_name} id={block.author.id} size={26} />
      <div>
        <div className={s.who}>
          {block.author.display_name}
          {time}
        </div>
        <div className={s.bs}>{bubbles}</div>
      </div>
    </div>
  );
}

function Composer({ ticketKey, people }: { ticketKey: string; people: Schemas["Person"][] }) {
  const id = useId();
  const post = usePostChat(ticketKey);
  const [text, setText] = useState("");
  const ref = useRef<HTMLTextAreaElement>(null);
  const body = text.trim();
  const canSend = body.length > 0 && !post.isPending;

  // Grow with the text up to ~6 lines, then scroll inside.
  useLayoutEffect(() => {
    const el = ref.current;
    if (!el) return;
    el.style.height = "auto";
    el.style.height = `${String(Math.min(el.scrollHeight, COMPOSER_MAX_PX))}px`;
  }, [text]);

  const send = () => {
    if (!canSend) return;
    const sent = text;
    post.mutate(body, {
      // Keep anything typed while the post was in flight.
      onSuccess: () => {
        setText((t) => (t === sent ? "" : t));
      },
    });
  };

  const onKeyDown = (e: KeyboardEvent<HTMLTextAreaElement>) => {
    if (e.key !== "Enter" || e.shiftKey || e.nativeEvent.isComposing) return;
    e.preventDefault();
    send();
  };

  return (
    <div className={s.composeWrap}>
      <form
        className={s.compose}
        onSubmit={(e) => {
          e.preventDefault();
          send();
        }}
      >
        <label htmlFor={id} className={s.sr}>
          Message
        </label>
        <textarea
          id={id}
          ref={ref}
          rows={1}
          maxLength={MAX_CHARS}
          placeholder={placeholder(people)}
          value={text}
          onChange={(e) => {
            setText(e.target.value);
            if (post.isError) post.reset();
          }}
          onKeyDown={onKeyDown}
        />
        <Button type="submit" variant="ink" size="sm" disabled={!canSend}>
          Send
        </Button>
      </form>
      {post.isError && (
        <p className={s.error} aria-live="polite">
          {`Not sent · ${post.error.message}`}
        </p>
      )}
    </div>
  );
}

/** Keyed by ticket, so scroll position and "new message" state start fresh per ticket. */
export function Talk(props: TalkProps) {
  return <TalkPane key={props.ticketKey} {...props} />;
}

function TalkPane({ ticketKey, limit = 8, variant }: TalkProps) {
  const chat = useChat(ticketKey);
  const me = useMe();
  const myId = me.data?.id ?? null;

  const all = useMemo(() => chat.data ?? [], [chat.data]);
  const shown = variant === "peek" ? all.slice(-limit) : all;
  const items = groupThread(shown, myId, new Date());
  const people = useMemo(() => speakers(all, myId), [all, myId]);
  const last = all.at(-1);
  const lastId = last?.id;

  // Messages after this id arrived while open: they fade and rise in. Set once, on first load.
  const [newAfter, setNewAfter] = useState<number | null>(null);
  if (newAfter === null && chat.isSuccess) setNewAfter(lastId ?? 0);

  const threadRef = useRef<HTMLDivElement>(null);
  const atBottom = useRef(true);
  const shownLastId = useRef<number | undefined>(undefined);
  const [pill, setPill] = useState(false);

  const toBottom = () => {
    const el = threadRef.current;
    if (el) el.scrollTop = el.scrollHeight;
    atBottom.current = true;
    setPill(false);
  };

  // Open at the newest; then follow new messages only when already at the bottom (or when
  // the new message is mine), otherwise offer the pill.
  useLayoutEffect(() => {
    if (lastId === undefined || lastId === shownLastId.current) return;
    const first = shownLastId.current === undefined;
    shownLastId.current = lastId;
    const el = threadRef.current;
    if (!el) return;
    if (first || atBottom.current || (myId !== null && last?.author.id === myId)) {
      el.scrollTop = el.scrollHeight;
      atBottom.current = true;
      setPill(false);
    } else {
      setPill(true);
    }
  }, [lastId, last, myId]);

  const onScroll = () => {
    const el = threadRef.current;
    if (!el) return;
    atBottom.current = el.scrollHeight - el.scrollTop - el.clientHeight <= BOTTOM_SLACK;
    if (atBottom.current) setPill(false);
  };

  let thread: ReactNode;
  if (chat.isPending) thread = <p className={s.quiet}>Loading</p>;
  else if (chat.isError)
    thread = <p className={s.quiet}>{`Can't load Talk · ${chat.error.message}`}</p>;
  else if (items.length === 0)
    thread = <p className={s.empty}>Nobody has said anything yet. Start it off.</p>;
  else
    thread = items.map((item) =>
      item.kind === "day" ? (
        <div key={item.key} className={s.day}>
          {item.label}
        </div>
      ) : (
        <Block key={item.key} block={item} newAfter={newAfter ?? Number.POSITIVE_INFINITY} />
      ),
    );

  return (
    <section className={cx(s.talk, s[variant])} aria-label="Talk">
      <div className={s.sheet}>
        <div className={s.head}>
          <b>Talk</b>
          {people.length > 0 && (
            <Faces
              palette="talk"
              people={people.map((p) => ({ id: p.id, name: p.display_name }))}
            />
          )}
          <span className={s.note}>{NOTE[variant]}</span>
        </div>
        <div className={s.threadWrap}>
          <div
            ref={threadRef}
            className={s.thread}
            role="log"
            aria-live="polite"
            aria-label="Messages"
            onScroll={onScroll}
          >
            {thread}
          </div>
          {pill && (
            <button type="button" className={s.pill} onClick={toBottom}>
              New messages ↓
            </button>
          )}
        </div>
        <Composer ticketKey={ticketKey} people={people} />
      </div>
    </section>
  );
}
