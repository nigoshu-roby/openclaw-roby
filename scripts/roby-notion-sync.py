#!/usr/bin/env python3
import argparse
import json
import os
import re
import subprocess
import sys
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, List, Optional
from urllib import error, request
from roby_audit import append_audit_event

ENV_PATH = Path.home() / ".openclaw" / ".env"
STATE_DIR = Path.home() / ".openclaw" / "roby"
STATE_PATH = STATE_DIR / "notion_sync_state.json"
NOTION_KEY_PATH = Path.home() / ".config" / "notion" / "api_key"
JST = timezone(timedelta(hours=9))


def load_env() -> Dict[str, str]:
    env = dict(os.environ)
    if ENV_PATH.exists():
        for line in ENV_PATH.read_text(encoding="utf-8").splitlines():
            line = line.strip()
            if not line or line.startswith("#") or "=" not in line:
                continue
            k, v = line.split("=", 1)
            key = k.strip()
            val = v.strip()
            if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
                val = val[1:-1]
            env[key] = val
    return env


def load_notion_key(env: Dict[str, str]) -> Optional[str]:
    for k in ("NOTION_API_KEY", "NOTION_TOKEN", "NOTION_KEY"):
        if env.get(k):
            return env[k].strip()
    if NOTION_KEY_PATH.exists():
        return NOTION_KEY_PATH.read_text(encoding="utf-8").strip()
    return None


def notion_request(method: str, url: str, token: str, payload: Optional[Dict[str, Any]] = None) -> Dict[str, Any]:
    data = None
    if payload is not None:
        data = json.dumps(payload, ensure_ascii=False).encode("utf-8")
    req = request.Request(
        url,
        data=data,
        method=method.upper(),
        headers={
            "Authorization": f"Bearer {token}",
            "Notion-Version": "2022-06-28",
            "Content-Type": "application/json",
        },
    )
    try:
        with request.urlopen(req, timeout=30) as resp:
            raw = resp.read().decode("utf-8", "ignore")
            if not raw:
                return {}
            return json.loads(raw)
    except error.HTTPError as e:
        body = e.read().decode("utf-8", "ignore") if hasattr(e, "read") else ""
        raise RuntimeError(f"Notion API error {e.code}: {body}") from e


def gh_project_items(owner: str, number: int, limit: int = 500) -> List[Dict[str, Any]]:
    cmd = [
        "gh",
        "project",
        "item-list",
        str(number),
        "--owner",
        owner,
        "--limit",
        str(limit),
        "--format",
        "json",
    ]
    out = subprocess.check_output(cmd, timeout=30)
    data = json.loads(out)
    return data.get("items", [])


def normalize_item(it: Dict[str, Any]) -> Dict[str, Any]:
    content = it.get("content") or {}
    labels = it.get("labels") or []
    phase = (it.get("phase") or "").strip()
    if not phase:
        for lb in labels:
            m = re.match(r"(?i)^phase[:\-/ ]*([Pp]?[0-5])$", str(lb).strip())
            if m:
                raw = m.group(1).upper()
                phase = raw if raw.startswith("P") else f"P{raw}"
                break
    priority = (it.get("priority") or "").strip()
    return {
        "title": content.get("title", "(no title)"),
        "url": content.get("url", ""),
        "status": (it.get("status") or "").strip(),
        "phase": phase,
        "priority": priority,
        "number": content.get("number"),
    }


def split_views(items: List[Dict[str, Any]]) -> Dict[str, List[Dict[str, Any]]]:
    norm = [normalize_item(x) for x in items if (x.get("content") or {}).get("url")]
    weekly = [x for x in norm if x["status"] in {"This Week", "In Progress", "Blocked"}]
    done = [x for x in norm if x["status"] == "Done"]
    return {"weekly_focus": weekly, "done": done}


def truncate(items: List[Dict[str, Any]], max_items: int) -> List[Dict[str, Any]]:
    return items[:max_items] if max_items > 0 else items


def paragraph(text: str) -> Dict[str, Any]:
    return {
        "object": "block",
        "type": "paragraph",
        "paragraph": {"rich_text": [{"type": "text", "text": {"content": text}}]},
    }


def heading(text: str, level: int = 2) -> Dict[str, Any]:
    t = f"heading_{level}"
    return {
        "object": "block",
        "type": t,
        t: {"rich_text": [{"type": "text", "text": {"content": text}}]},
    }


def bullet(title: str, url: str, status: str, phase: str, priority: str) -> Dict[str, Any]:
    prefix_parts: List[str] = [status or "-"]
    if phase:
        prefix_parts.append(phase)
    if priority:
        prefix_parts.append(priority)
    prefix = " / ".join(prefix_parts)
    return {
        "object": "block",
        "type": "bulleted_list_item",
        "bulleted_list_item": {
            "rich_text": [
                {"type": "text", "text": {"content": f"[{prefix}] "}},
                {"type": "text", "text": {"content": title, "link": {"url": url}}},
            ]
        },
    }


def read_state() -> Dict[str, Any]:
    if not STATE_PATH.exists():
        return {"page_id": None, "block_ids": []}
    try:
        return json.loads(STATE_PATH.read_text(encoding="utf-8"))
    except Exception:
        return {"page_id": None, "block_ids": []}


def write_state(state: Dict[str, Any]) -> None:
    STATE_DIR.mkdir(parents=True, exist_ok=True)
    STATE_PATH.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")


def delete_old_blocks(token: str, block_ids: List[str]) -> None:
    for bid in block_ids:
        try:
            notion_request("DELETE", f"https://api.notion.com/v1/blocks/{bid}", token)
        except Exception:
            continue


def append_blocks(token: str, page_id: str, children: List[Dict[str, Any]]) -> List[str]:
    resp = notion_request(
        "PATCH",
        f"https://api.notion.com/v1/blocks/{page_id}/children",
        token,
        {"children": children},
    )
    block_ids = []
    for r in resp.get("results", []):
        if r.get("id"):
            block_ids.append(r["id"])
    return block_ids


def build_snapshot_blocks(weekly: List[Dict[str, Any]], done: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    now = datetime.now(JST).strftime("%Y-%m-%d %H:%M:%S JST")
    phase_counts: Dict[str, int] = {}
    for item in weekly + done:
        ph = (item.get("phase") or "").strip()
        if not ph:
            continue
        phase_counts[ph] = phase_counts.get(ph, 0) + 1

    blocks: List[Dict[str, Any]] = []
    blocks.append(heading("PBS Snapshot", 2))
    blocks.append(paragraph(f"auto-generated at {now}"))
    blocks.append(paragraph(f"Weekly Focus: {len(weekly)} items / Done: {len(done)} items"))
    if phase_counts:
        ordered = sorted(phase_counts.items(), key=lambda kv: kv[0])
        blocks.append(paragraph("Phase counts: " + ", ".join(f"{k}={v}" for k, v in ordered)))
    blocks.append(paragraph("表示形式: [Status / Phase / Priority] タイトル"))
    blocks.append(heading("Weekly Focus", 3))
    if not weekly:
        blocks.append(paragraph("No items."))
    for it in weekly:
        blocks.append(
            bullet(
                it["title"],
                it["url"],
                it["status"],
                (it.get("phase") or ""),
                (it.get("priority") or ""),
            )
        )
    blocks.append(heading("Done", 3))
    if not done:
        blocks.append(paragraph("No items."))
    for it in done:
        blocks.append(
            bullet(
                it["title"],
                it["url"],
                it["status"],
                (it.get("phase") or ""),
                (it.get("priority") or ""),
            )
        )
    return blocks


def build_phase_counts(items: List[Dict[str, Any]]) -> Dict[str, int]:
    out: Dict[str, int] = {}
    for item in items:
        phase = str(item.get("phase") or "").strip()
        if not phase:
            continue
        out[phase] = out.get(phase, 0) + 1
    return dict(sorted(out.items(), key=lambda kv: kv[0]))


def write_audit(summary: Dict[str, Any], run_id: str, severity: str = "info") -> None:
    if os.environ.get("ROBY_IMMUTABLE_AUDIT", "1") != "1":
        return
    try:
        append_audit_event(
            "notion_sync.run",
            summary,
            source="roby-notion-sync",
            run_id=run_id,
            severity=severity,
        )
    except Exception:
        pass


def main() -> int:
    p = argparse.ArgumentParser()
    p.add_argument("--owner", default=os.environ.get("ROBY_GH_OWNER", "nigoshu-roby"))
    p.add_argument("--project-number", type=int, default=int(os.environ.get("ROBY_GH_PROJECT_NUMBER", "1")))
    p.add_argument("--page-id", default=os.environ.get("ROBY_NOTION_SYNC_PAGE_ID", ""))
    p.add_argument("--max-weekly", type=int, default=int(os.environ.get("ROBY_NOTION_SYNC_MAX_WEEKLY", "30")))
    p.add_argument("--max-done", type=int, default=int(os.environ.get("ROBY_NOTION_SYNC_MAX_DONE", "30")))
    p.add_argument("--dry-run", action="store_true")
    args = p.parse_args()

    env = load_env()
    token = load_notion_key(env)
    page_id = (args.page_id or env.get("ROBY_NOTION_SYNC_PAGE_ID", "")).strip()
    run_id = f"roby:notion_sync:{datetime.now(JST).strftime('%Y%m%d%H%M%S')}"

    if not token:
        msg = "missing Notion token (NOTION_API_KEY or ~/.config/notion/api_key)"
        print(f"ERROR: {msg}", file=sys.stderr)
        write_audit({"ok": False, "error": msg, "dry_run": bool(args.dry_run)}, run_id, severity="error")
        return 2

    try:
        items = gh_project_items(args.owner, args.project_number)
        views = split_views(items)
        weekly = truncate(views["weekly_focus"], args.max_weekly)
        done = truncate(views["done"], args.max_done)
        phase_counts = build_phase_counts(weekly + done)

        if args.dry_run:
            payload = {
                "owner": args.owner,
                "project_number": args.project_number,
                "weekly_focus": len(weekly),
                "done": len(done),
                "phase_counts": phase_counts,
                "page_id": page_id or "(not-set)",
                "dry_run": True,
            }
            print(json.dumps(payload, ensure_ascii=False))
            write_audit({"ok": True, **payload}, run_id, severity="info")
            return 0

        if not page_id:
            msg = "missing page id (ROBY_NOTION_SYNC_PAGE_ID or --page-id)"
            print(f"ERROR: {msg}", file=sys.stderr)
            write_audit({"ok": False, "error": msg, "dry_run": False}, run_id, severity="error")
            return 2

        old = read_state()
        if old.get("page_id") == page_id and old.get("block_ids"):
            delete_old_blocks(token, old.get("block_ids", []))

        new_blocks = build_snapshot_blocks(weekly, done)
        new_ids = append_blocks(token, page_id, new_blocks)
        write_state({"page_id": page_id, "block_ids": new_ids, "updated_at": datetime.now(JST).isoformat()})

        payload = {
            "owner": args.owner,
            "project_number": args.project_number,
            "weekly_focus": len(weekly),
            "done": len(done),
            "phase_counts": phase_counts,
            "page_id": page_id,
            "blocks_written": len(new_ids),
        }
        print(json.dumps(payload, ensure_ascii=False))
        write_audit({"ok": True, **payload}, run_id, severity="info")
        return 0
    except Exception as exc:
        msg = str(exc)
        print(f"ERROR: {msg}", file=sys.stderr)
        write_audit(
            {
                "ok": False,
                "error": msg,
                "owner": args.owner,
                "project_number": args.project_number,
                "dry_run": bool(args.dry_run),
            },
            run_id,
            severity="error",
        )
        return 1


if __name__ == "__main__":
    raise SystemExit(main())
