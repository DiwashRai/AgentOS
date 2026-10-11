// The ticket's PR (`pr_url`, found by the worker after each repo run), opened in a new tab.
import { prLabel } from "../../domain/format";
import { Link } from "../../ui";

export function PrLink({ url, className }: { url: string; className?: string | undefined }) {
  return (
    <Link href={url} target="_blank" rel="noreferrer" className={className} title={url}>
      {`${prLabel(url)} ↗`}
    </Link>
  );
}
