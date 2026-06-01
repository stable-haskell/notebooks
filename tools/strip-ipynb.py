#!/usr/bin/env python3
"""Strip Jupyter cell outputs + run metadata so notebooks commit clean.

Two modes:
  * git clean filter:  reads a notebook on stdin, writes the stripped notebook
                       to stdout (configured via .gitattributes — see Makefile
                       target `install-hooks`).
  * in place:          `strip-ipynb.py NB.ipynb [NB.ipynb ...]` rewrites files.

Stripping removes everything produced by *running* a notebook — cell outputs,
execution counts, and transient cell/notebook metadata (execution timing,
widget state, …) — while preserving the cell sources, ids, intentional tags,
and the kernel binding. The result matches the canonical, never-run form, so a
freshly authored notebook and a run-then-stripped one serialize identically and
git sees no churn.
"""
import json
import sys


def strip_cell(cell):
    ctype = cell.get("cell_type")
    cid = cell.get("id")
    src = cell.get("source", [])
    if ctype == "code":
        tags = cell.get("metadata", {}).get("tags", [])
        return {
            "cell_type": "code",
            "id": cid,
            "execution_count": None,
            "metadata": {"tags": tags},
            "outputs": [],
            "source": src,
        }
    out = {"cell_type": ctype, "id": cid, "metadata": {}, "source": src}
    tags = cell.get("metadata", {}).get("tags")
    if tags:
        out["metadata"] = {"tags": tags}
    if cell.get("attachments"):  # keep embedded images in markdown cells
        out["attachments"] = cell["attachments"]
    return out


def strip(nb):
    md = nb.get("metadata", {})
    return {
        "cells": [strip_cell(c) for c in nb.get("cells", [])],
        "metadata": {k: md[k] for k in ("kernelspec", "language_info") if k in md},
        "nbformat": nb.get("nbformat", 4),
        "nbformat_minor": nb.get("nbformat_minor", 5),
    }


def dump(nb):
    return json.dumps(nb, indent=1, ensure_ascii=False) + "\n"


def main():
    paths = sys.argv[1:]
    if paths:
        for p in paths:
            with open(p) as f:
                nb = json.load(f)
            with open(p, "w") as f:
                f.write(dump(strip(nb)))
        return
    data = sys.stdin.read()
    if not data.strip():
        return  # empty input — leave it be
    sys.stdout.write(dump(strip(json.loads(data))))


if __name__ == "__main__":
    main()
