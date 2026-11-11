#!/usr/bin/env python3
"""Verify an executed Jupyter notebook and render a visual walkthrough to Markdown.

Unlike grepping raw .ipynb JSON (which matches strings inside cell 'source' or
error tracebacks), this script:
  1. Verifies that every code cell executed (`execution_count` is set) and that
     ZERO cells produced `output_type == "error"`.
  2. Verifies that every required `--marker` string appears in the actual
     executed `stdout`/`stderr` stream outputs (never in cell source code).
  3. Renders a complete visual walkthrough (status table, verification highlights,
     rendered markdown prose, bash code cells, and executed outputs) to
     `--summary` (e.g. `$GITHUB_STEP_SUMMARY`).
"""
import argparse
import json
import os
import re
import sys
from pathlib import Path

ANSI_RE = re.compile(r"\x1b\[[0-9;]*[A-Za-z]|\r")


def clean_ansi(text: str) -> str:
    return ANSI_RE.sub("", text)


def extract_cell_streams(cell: dict) -> tuple[str, str, list[str]]:
    stdout_parts: list[str] = []
    stderr_parts: list[str] = []
    errors: list[str] = []
    for out in cell.get("outputs", []):
        otype = out.get("output_type")
        if otype == "stream":
            txt = clean_ansi("".join(out.get("text", [])))
            if out.get("name") == "stderr":
                stderr_parts.append(txt)
            else:
                stdout_parts.append(txt)
        elif otype in ("execute_result", "display_data"):
            data = out.get("data", {})
            if "text/plain" in data:
                stdout_parts.append(clean_ansi("".join(data["text/plain"])))
        elif otype == "error":
            ename = out.get("ename", "Error")
            evalue = out.get("evalue", "")
            errors.append(clean_ansi(f"{ename}: {evalue}"))
    return "".join(stdout_parts), "".join(stderr_parts), errors


def truncate_lines(text: str, max_lines: int = 60, head: int = 20, tail: int = 35) -> tuple[str, bool]:
    lines = text.splitlines()
    if len(lines) <= max_lines:
        return text.rstrip(), False
    omitted = len(lines) - head - tail
    snippet = "\n".join(
        lines[:head]
        + [f"... ({omitted} lines omitted in summary; see executed.html / executed.ipynb artifact) ..."]
        + lines[-tail:]
    )
    return snippet, True


def main() -> int:
    parser = argparse.ArgumentParser(description=__doc__)
    parser.add_argument("notebook", help="Path to executed .ipynb file")
    parser.add_argument("--title", default="Notebook Walkthrough", help="Heading title for the summary")
    parser.add_argument("--marker", action="append", default=[], help="Substring required in cell stdout/stderr")
    parser.add_argument("--summary", default=os.environ.get("GITHUB_STEP_SUMMARY", ""), help="Output markdown path")
    args = parser.parse_args()

    nb_path = Path(args.notebook)
    if not nb_path.is_file():
        print(f"::error::Executed notebook not found: {nb_path}", file=sys.stderr)
        return 1

    nb = json.load(nb_path.open(encoding="utf-8"))
    cells = nb.get("cells", [])

    code_rows = []
    all_stream_output = []
    failed_cells = []

    code_idx = 0
    for cell in cells:
        if cell.get("cell_type") != "code":
            continue
        code_idx += 1
        cid = cell.get("id") or f"cell-{code_idx}"
        exec_count = cell.get("execution_count")
        stdout, stderr, errors = extract_cell_streams(cell)
        all_stream_output.append(stdout)
        all_stream_output.append(stderr)

        unexecuted = exec_count is None and not stdout and not stderr and not errors
        failed = bool(errors) or unexecuted
        if failed:
            reason = errors[0] if errors else "Cell did not execute"
            combined_cell_out = (stdout.strip() + "\n" + stderr.strip()).strip()
            stderr_tail = "\n".join(combined_cell_out.splitlines()[-30:]) if combined_cell_out else reason
            failed_cells.append((cid, stderr_tail))
            print(f"=== FAILED CELL {cid} OUTPUT TAIL ===\n{stderr_tail}\n=== END {cid} ===", file=sys.stderr)
            gh_msg = stderr_tail.replace("%", "%25").replace("\r", "%0D").replace("\n", "%0A")
            print(f"::error title=Cell {cid} failed::{gh_msg}", file=sys.stderr)

        last_line = ""
        for candidate in (stdout.strip().splitlines() or stderr.strip().splitlines()):
            if candidate.strip():
                last_line = candidate.strip()
        if len(last_line) > 90:
            last_line = last_line[:87] + "..."

        status = "❌ FAIL" if failed else "✅ OK"
        code_rows.append((code_idx, cid, status, len(stdout.splitlines()), last_line))

    combined_streams = "\n".join(all_stream_output)
    missing_markers = []
    for marker in args.marker:
        if marker not in combined_streams:
            missing_markers.append(marker)
            print(f"::error title=Missing output marker::Marker not found in executed cell outputs: {marker}", file=sys.stderr)

    overall_ok = not failed_cells and not missing_markers
    badge = "✅" if overall_ok else "❌"

    # Build Markdown summary
    md_lines: list[str] = []
    md_lines.append(f"## {badge} {args.title}")
    md_lines.append("")
    md_lines.append(
        f"**Code cells passed:** `{len(code_rows) - len(failed_cells)}/{len(code_rows)}` "
        f"| **Output markers verified:** `{len(args.marker) - len(missing_markers)}/{len(args.marker)}` "
        f"| **Artifacts:** `executed.html` & `executed.ipynb`"
    )
    md_lines.append("")

    if args.marker:
        md_lines.append("### Verification Markers (from executed `stdout`)")
        md_lines.append("")
        md_lines.append("| Marker | Status | Matched Line |")
        md_lines.append("|---|---|---|")
        for marker in args.marker:
            matched_line = next(
                (line.strip() for line in combined_streams.splitlines() if marker in line),
                "",
            )
            m_status = "✅ Present" if marker not in missing_markers else "❌ Missing"
            safe_line = matched_line.replace("|", "\\|")[:100]
            md_lines.append(f"| `{marker}` | {m_status} | `{safe_line}` |")
        md_lines.append("")

    md_lines.append("<details>")
    md_lines.append(f"<summary><strong>📊 Cell Execution Summary ({len(code_rows)} code cells)</strong></summary>")
    md_lines.append("")
    md_lines.append("| # | Cell ID | Status | stdout lines | Last Output Line |")
    md_lines.append("|---:|---|---|---:|---|")
    for idx, cid, status, nlines, last_line in code_rows:
        safe_last = last_line.replace("|", "\\|").replace("`", "'")
        md_lines.append(f"| {idx} | `{cid}` | {status} | {nlines} | `{safe_last}` |")
    md_lines.append("")
    md_lines.append("</details>")
    md_lines.append("")

    if failed_cells:
        md_lines.append("### ❌ Failed Cells")
        md_lines.append("")
        for cid, err_tail in failed_cells:
            md_lines.append(f"#### Cell `{cid}`")
            md_lines.append("```text")
            md_lines.append(err_tail)
            md_lines.append("```")
            md_lines.append("")

    md_lines.append("<details open>")
    md_lines.append("<summary><strong>📖 Full Executed Notebook Walkthrough (click to collapse)</strong></summary>")
    md_lines.append("")

    step_num = 0
    for cell in cells:
        ctype = cell.get("cell_type")
        cid = cell.get("id") or "cell"
        src = "".join(cell.get("source", [])).strip()
        if ctype == "markdown":
            md_lines.append(src)
            md_lines.append("")
        elif ctype == "code":
            step_num += 1
            stdout, stderr, errors = extract_cell_streams(cell)
            cell_ok = not errors and (cell.get("execution_count") is not None or stdout or stderr)
            c_badge = "✅" if cell_ok else "❌"
            clean_src = src
            if clean_src.startswith("%%bash\n"):
                clean_src = clean_src[len("%%bash\n"):]

            md_lines.append("<details>")
            md_lines.append(f"<summary>{c_badge} <code>In [{step_num}] ({cid})</code> — Bash Cell Source</summary>")
            md_lines.append("")
            md_lines.append("```bash")
            md_lines.append(clean_src)
            md_lines.append("```")
            md_lines.append("")
            md_lines.append("</details>")
            md_lines.append("")

            if stdout.strip():
                snippet, was_trunc = truncate_lines(stdout.strip(), max_lines=60, head=20, tail=35)
                if len(stdout.strip().splitlines()) <= 25:
                    md_lines.append(f"**Output (`{cid}`):**")
                    md_lines.append("```text")
                    md_lines.append(snippet)
                    md_lines.append("```")
                else:
                    md_lines.append("<details open>")
                    md_lines.append(
                        f"<summary><strong>Output (<code>{cid}</code> — {len(stdout.strip().splitlines())} lines)</strong></summary>"
                    )
                    md_lines.append("")
                    md_lines.append("```text")
                    md_lines.append(snippet)
                    md_lines.append("```")
                    md_lines.append("</details>")
                md_lines.append("")

            if not cell_ok:
                err_text = stderr.strip() or "\n".join(errors) or "Cell did not execute"
                err_snippet, _ = truncate_lines(err_text, max_lines=50, head=15, tail=30)
                md_lines.append(f"**Error (`{cid}`):**")
                md_lines.append("```text")
                md_lines.append(err_snippet)
                md_lines.append("```")
                md_lines.append("")

    md_lines.append("</details>")
    md_lines.append("")

    summary_text = "\n".join(md_lines)
    # Guard against GitHub's 1 MiB step summary limit
    if len(summary_text.encode("utf-8")) > 950_000:
        summary_text = summary_text[:900_000] + "\n\n... (walkthrough truncated to fit GitHub Summary limit; see `executed.html` artifact) ...\n</details>\n"

    if args.summary:
        summary_path = Path(args.summary)
        with summary_path.open("a", encoding="utf-8") as f:
            f.write(summary_text)
        print(f"Wrote {len(summary_text)} chars to step summary {summary_path}")

    if not overall_ok:
        return 1
    print(f"✓ {args.title}: all {len(code_rows)} code cells succeeded and all {len(args.marker)} markers verified in stdout")
    return 0


if __name__ == "__main__":
    sys.exit(main())
