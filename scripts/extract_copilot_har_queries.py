#!/usr/bin/env python3
"""Extract Copilot dashboard DataPrime query templates from a cx498 HAR file."""

from __future__ import annotations

import json
import re
import sys
from pathlib import Path


def _extract_z_block(text: str) -> str | None:
    start = text.find("var z={")
    if start < 0:
        return None
    depth = 0
    i = start + len("var z=")
    while i < len(text):
        if text[i] == "{":
            depth += 1
        elif text[i] == "}":
            depth -= 1
            if depth == 0:
                return text[start : i + 1]
        i += 1
    return None


def _query_for_key(zblock: str, key: str) -> str | None:
    m = re.search(rf"{key}:.*?\`([^`]+)\`", zblock, re.DOTALL)
    if not m:
        return None
    return m.group(1).replace("\\n", "\n").replace('\\"', '"')


def main() -> None:
    if len(sys.argv) != 2:
        print(f"Usage: {sys.argv[0]} <path-to.har>", file=sys.stderr)
        raise SystemExit(2)

    har_path = Path(sys.argv[1])
    data = json.loads(har_path.read_text())

    dpv = ""
    repo_chunk = ""
    shared_chunk = ""
    for entry in data["log"]["entries"]:
        url = entry["request"]["url"]
        text = entry["response"]["content"].get("text", "") or ""
        if "chunk-DPVBNZ5E.js" in url:
            dpv = text
        if "sessionRepoUserInfo" in text:
            repo_chunk = text
        if "var p=`source spans" in text and "invoke_agent" in text:
            shared_chunk = text

    print(f"# Copilot HAR query extract: {har_path.name}\n")

    if shared_chunk:
        for name, pat in [
            ("base", r"var p=`([^`]+)`"),
            ("invoke", r"var h=`([^`]+)`"),
            ("chat", r"var ye=`([^`]+)`"),
            ("user_email", r'P="\$d\.process\.tags\[\'user\.email\'\]"'),
        ]:
            m = re.search(pat, shared_chunk)
            if m:
                val = m.group(1) if name != "user_email" else m.group(0)
                print(f"## {name}\n{val.replace(chr(92)+'n', chr(10))}\n")

    z = _extract_z_block(dpv) if dpv else None
    if z:
        keys = re.findall(r"(\w+):(?:e=>|i=>|\([^)]*\)=>)", z)
        print(f"## DPVBNZ5E queries ({len(keys)})\n")
        for key in keys:
            q = _query_for_key(z, key)
            if q:
                print(f"### {key}\n{q}\n")
    else:
        print("(no chunk-DPVBNZ5E.js — main dashboard queries not found)\n")

    if repo_chunk:
        for key in ("sessionRepoUserInfo", "sessionMessages", "sessionsWithMessages"):
            m = re.search(rf"{key}:.*?`([^`]+)`", repo_chunk, re.DOTALL)
            if m:
                print(f"### {key}\n{m.group(1).replace(chr(92)+'n', chr(10))}\n")


if __name__ == "__main__":
    main()
