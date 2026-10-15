"""The mailbox workflow template (`deploy/github/agentos-mailbox.yml`) keeps its security shape.

PyYAML isn't a dependency, so these are indentation-based text checks. They rely on the template's
block style (one key per line, `run: |` scalars), which the first test pins.
"""

from __future__ import annotations

import re
from pathlib import Path

from agentos.launchers.mailbox import MAX_OUTBOX_BYTES, RUN_FOLDER_PATTERN, WORK_JOB

TEMPLATE = Path(__file__).resolve().parents[2] / "deploy" / "github" / "agentos-mailbox.yml"


def _lines() -> list[str]:
    return TEMPLATE.read_text(encoding="utf-8").splitlines()


def _indent(line: str) -> int:
    return len(line) - len(line.lstrip(" "))


def _significant(line: str) -> bool:
    stripped = line.strip()
    return bool(stripped) and not stripped.startswith("#")


def _section(lines: list[str], *keys: str) -> list[str]:
    """The lines nested under the block-style mapping path `keys` (comments included)."""
    body, parent = lines, -1
    for key in keys:
        pattern = re.compile(rf"^( *){re.escape(key)}:(\s|$)")
        for i, line in enumerate(body):
            match = pattern.match(line)
            if match and _indent(line) > parent and _significant(line):
                parent = len(match.group(1))
                end = i + 1
                while end < len(body) and (
                    not _significant(body[end]) or _indent(body[end]) > parent
                ):
                    end += 1
                body = body[i + 1 : end]
                break
        else:
            raise AssertionError(f"no {'.'.join(keys)} in {TEMPLATE.name}")
    return body


def _child_keys(section: list[str]) -> list[str]:
    significant = [line for line in section if _significant(line)]
    if not significant:
        return []
    level = min(_indent(line) for line in significant)
    return [
        line.strip().split(":", 1)[0]
        for line in significant
        if _indent(line) == level and not line.strip().startswith("- ")
    ]


def _code(section: list[str]) -> str:
    """The non-comment lines (a trailing `# ...` comment is kept: it never holds `${{`)."""
    return "\n".join(line for line in section if _significant(line))


def _reads_secrets(code: str) -> bool:
    """Whether any `${{ }}` expression reads `secrets`, or a `secrets: inherit` hands them on."""
    expressions = re.findall(r"\$\{\{(.*?)\}\}", code, re.DOTALL)
    return any(re.search(r"\bsecrets\b", e) for e in expressions) or bool(
        re.search(r"^\s*secrets:", code, re.MULTILINE)
    )


def _run_blocks(lines: list[str]) -> list[list[str]]:
    blocks: list[list[str]] = []
    for i, line in enumerate(lines):
        match = re.match(r"^( *)(?:- )?run:(.*)$", line)
        if not match or not _significant(line):
            continue
        block = [match.group(2)]
        column = _indent(line)
        for nxt in lines[i + 1 :]:
            if nxt.strip() and _indent(nxt) <= column:
                break
            block.append(nxt)
        blocks.append(block)
    return blocks


def test_template_exists_in_block_style() -> None:
    text = TEMPLATE.read_text(encoding="utf-8")
    assert "\r" not in text
    assert "\t" not in text
    # Flow mappings would hide keys from these checks (`permissions: {}` is the one exception).
    flow = [line for line in _lines() if _significant(line) and re.search(r":\s*\{\s*[^\s}]", line)]
    assert flow == []


def test_no_expressions_inside_run_blocks() -> None:
    blocks = _run_blocks(_lines())
    assert len(blocks) >= 5  # fetch download, work check/run/cleanup, publish upload
    for block in blocks:
        assert not any("${{" in line for line in block), block


def test_only_workflow_dispatch_with_exactly_three_inputs() -> None:
    lines = _lines()
    assert _child_keys(_section(lines, "on")) == ["workflow_dispatch"]
    inputs = _section(lines, "on", "workflow_dispatch", "inputs")
    assert sorted(_child_keys(inputs)) == ["manifest_sha256", "run", "worker_sha256"]
    # The Artifactory URL is a repo variable, never an input; no server or token inputs.
    assert "${{ vars.AGENTOS_ARTIFACTORY_URL }}" in _code(_section(lines, "env"))


def test_top_level_permissions_empty_and_top_level_env_holds_no_secret() -> None:
    lines = _lines()
    assert "permissions: {}" in lines
    assert not _reads_secrets(_code(_section(lines, "env")))


def test_jobs_and_their_runners() -> None:
    lines = _lines()
    assert _child_keys(_section(lines, "jobs")) == ["fetch", "work", "publish"]
    store_runner = (
        "runs-on: ${{ fromJSON(vars.AGENTOS_STORE_RUNS_ON || vars.AGENTOS_RUNS_ON"
        " || '\"ubuntu-latest\"') }}"
    )
    for job in ("fetch", "publish"):
        code = _code(_section(lines, "jobs", job))
        assert store_runner in code
        assert re.search(r"^ +environment: agentos-store$", code, re.MULTILINE)
    # The reaper finds the work job by name in GitHub's jobs list: its key, with no `name:`.
    assert WORK_JOB == "work"
    assert "name" not in _child_keys(_section(lines, "jobs", WORK_JOB))
    work = _code(_section(lines, "jobs", "work"))
    assert "runs-on: ${{ fromJSON(vars.AGENTOS_RUNS_ON || '\"ubuntu-latest\"') }}" in work
    assert "environment:" not in work
    publish = _code(_section(lines, "jobs", "publish"))
    assert "needs: [fetch, work]" in publish
    assert "if: always() && needs.fetch.result == 'success'" in publish


def test_work_job_never_sees_a_secret() -> None:
    work = _section(_lines(), "jobs", "work")
    assert "${{" in _code(work)
    assert not _reads_secrets(_code(work))
    assert not any("ARTIFACTORY" in line for line in work)  # not even in a comment
    permissions = _section(_lines(), "jobs", "work", "permissions")
    assert sorted(line.strip() for line in permissions if _significant(line)) == [
        "contents: write",
        "pull-requests: write",
    ]


def test_work_job_runs_the_worker_in_mailbox_mode() -> None:
    work = _code(_section(_lines(), "jobs", "work"))
    assert "AOS_PYTHON: python3" in work
    stop = work.index("::stop-commands::")
    exec_at = work.index('exec "$AOS_PYTHON" "$RUNNER_TEMP/inbox/worker.py"')
    assert stop < exec_at
    for flag in ("--manifest", '--outbox "$RUNNER_TEMP/outbox/outbox.jsonl"', "--workdir"):
        assert flag in work[exec_at:]
    assert "--server" not in work
    assert "--token" not in work
    # gh's credential helper is keyed by the job's GitHub host (github.com or GHES), via env.
    assert "GIT_CONFIG_KEY_0: credential.${{ github.server_url }}.helper" in work
    assert "GH_TOKEN: ${{ github.token }}" in work


def test_artifacts_are_short_lived_and_the_outbox_always_uploads() -> None:
    lines = _lines()
    fetch = _code(_section(lines, "jobs", "fetch"))
    work = _code(_section(lines, "jobs", "work"))
    assert "name: agentos-inbox" in fetch
    outbox_step = work[work.index("name: Hand the outbox") :]
    assert "if: always()" in outbox_step
    assert "name: agentos-outbox" in outbox_step
    for code in (fetch, outbox_step):
        assert "retention-days: 1" in code
        assert "if-no-files-found: error" in code


def test_fetch_validates_inputs_and_checksums() -> None:
    fetch = _code(_section(_lines(), "jobs", "fetch"))
    # The same folder pattern the server uses for `run`.
    assert f'[[ "$AOS_RUN" =~ {RUN_FOLDER_PATTERN} ]]' in fetch
    assert '=~ ^[0-9a-f]{64}$ && "$AOS_MANIFEST_SHA" =~ ^[0-9a-f]{64}$' in fetch
    assert "sha256sum --check --strict" in fetch
    assert '"${AOS_STORE%/}/runs/$AOS_RUN/$1"' in fetch


def test_publish_caps_the_outbox_and_sends_bytes_as_is() -> None:
    publish = _code(_section(_lines(), "jobs", "publish"))
    assert f'[[ "$AOS_RUN" =~ {RUN_FOLDER_PATTERN} ]]' in publish
    assert '! -L "$f"' in publish
    assert f"<= {MAX_OUTBOX_BYTES} ))" in publish
    assert 'X-Checksum-Sha256: $sha"' in publish
    assert '--upload-file "$f" "${AOS_STORE%/}/runs/$AOS_RUN/outbox.jsonl"' in publish
    for forbidden in ("python", "jq", 'bash "$f"', "source", '. "$f"'):
        assert forbidden not in publish


def test_credentials_never_reach_curl_argv() -> None:
    code = _code(_lines())
    assert not re.search(r"(^|\s)(--user|-u|--proxy-user|-U)(\s|=|$)", code, re.MULTILINE)
    # Every curl invocation (not `command -v curl`) reads its credentials from stdin.
    curls = [line for line in code.splitlines() if re.search(r"(^|[|;&]\s*)curl\s", line.strip())]
    assert len(curls) == 2  # fetch's get(), publish's upload
    for line in curls:
        assert "AOS_USER" not in line
        assert "AOS_PASSWORD" not in line
        assert "--config -" in line
