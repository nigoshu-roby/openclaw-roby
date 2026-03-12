#!/usr/bin/env python3
"""Build a local-first registry from TOKIWAGI_MASTER minutes corpus."""

from __future__ import annotations

import argparse
import importlib.util
import json
import re
import sys
import time
from collections import Counter
from datetime import datetime, timedelta, timezone
from pathlib import Path
from typing import Any, Dict, Iterable, List, Optional, Tuple

from roby_audit import append_audit_event

JST = timezone(timedelta(hours=9))
STATE_ROOT = Path.home() / ".openclaw" / "roby"
STATE_PATH = STATE_ROOT / "tokiwagi_master_registry_state.json"
RUN_LOG_PATH = STATE_ROOT / "tokiwagi_master_registry_runs.jsonl"
PROGRESS_PATH = STATE_ROOT / "tokiwagi_master_registry_progress.json"
LATEST_REGISTRY_PATH = STATE_ROOT / "tokiwagi_master_registry_latest.json"

DEFAULT_PROJECT = "TOKIWAGI_MASTER"
DEFAULT_INCLUDE_DB_TITLES = {"TOKIWAGIインナー議事録", "基礎情報"}
DEFAULT_EXCLUDE_DB_TITLES = {"フィードバック", "Untitled"}

ACTION_PATTERN_KEYWORDS = {
    "会議調整": ["日程", "調整", "会議", "MTG", "定例", "打ち合わせ"],
    "資料作成": ["資料", "提案", "見積", "見積書", "アジェンダ", "作成", "共有資料"],
    "実装・設定": ["実装", "構築", "設定", "反映", "申請", "登録", "改修"],
    "確認・調査": ["確認", "調査", "精査", "ヒアリング", "洗い出し", "追跡"],
    "連携・共有": ["共有", "連携", "引き継ぎ", "報告", "展開", "送付"],
    "分析・評価": ["分析", "評価", "検証", "比較", "集計", "整理"],
}

OWNER_SUFFIXES = ("さん", "氏", "様", "店長", "本部長")


def load_minutes_module():
    scripts_dir = Path(__file__).resolve().parent
    script_path = scripts_dir / "roby-minutes.py"
    if str(scripts_dir) not in sys.path:
        sys.path.insert(0, str(scripts_dir))
    spec = importlib.util.spec_from_file_location("roby_minutes_registry_module", script_path)
    if spec is None or spec.loader is None:
        raise RuntimeError(f"Failed to load minutes module from {script_path}")
    module = importlib.util.module_from_spec(spec)
    spec.loader.exec_module(module)
    return module


def read_json(path: Path) -> Dict[str, Any]:
    if not path.exists():
        return {}
    try:
        payload = json.loads(path.read_text(encoding="utf-8"))
        if isinstance(payload, dict):
            return payload
    except Exception:
        return {}
    return {}


def write_json(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    path.write_text(json.dumps(payload, ensure_ascii=False, indent=2), encoding="utf-8")


def append_jsonl(path: Path, payload: Dict[str, Any]) -> None:
    path.parent.mkdir(parents=True, exist_ok=True)
    with path.open("a", encoding="utf-8") as handle:
        handle.write(json.dumps(payload, ensure_ascii=False) + "\n")


def write_progress(payload: Dict[str, Any]) -> None:
    write_json(PROGRESS_PATH, payload)


def normalize_owner_name(raw: str) -> str:
    text = (raw or "").strip()
    for suffix in OWNER_SUFFIXES:
        if text.endswith(suffix):
            text = text[: -len(suffix)]
            break
    return text.strip()


def extract_owner_mentions(text: str) -> List[str]:
    owners: List[str] = []
    for suffix in OWNER_SUFFIXES:
        pattern = rf"([一-龥ぁ-んァ-ヶA-Za-z0-9]{{1,12}}){suffix}"
        for match in re.finditer(pattern, text):
            candidate = normalize_owner_name(match.group(1))
            if not candidate:
                continue
            if len(candidate) == 1 and not candidate.isascii():
                continue
            owners.append(candidate)
    return owners


def classify_action_patterns(line: str) -> List[str]:
    matched: List[str] = []
    for label, keywords in ACTION_PATTERN_KEYWORDS.items():
        if any(keyword in line for keyword in keywords):
            matched.append(label)
    return matched


def _increment_counter_map(target: Dict[str, int], values: Iterable[str]) -> None:
    for value in values:
        key = (value or "").strip()
        if not key:
            continue
        target[key] = int(target.get(key, 0) or 0) + 1


def extract_project_sections(
    text: str,
    *,
    default_project: str,
    known_projects: List[str],
    source_title: str,
    mod: Any,
) -> Dict[str, Dict[str, Any]]:
    inferred_primary = mod.infer_primary_project(
        text=text,
        known_projects=known_projects,
        source_title=source_title,
        fallback_project=default_project,
    )
    current_project = mod._canonical_project_display_name(inferred_primary or default_project)
    sections: Dict[str, Dict[str, Any]] = {}

    def ensure_section(project_name: str) -> Dict[str, Any]:
        canonical = mod._canonical_project_display_name(project_name or default_project or "TOKIWAGI_MASTER")
        if canonical not in sections:
            sections[canonical] = {
                "project": canonical,
                "line_count": 0,
                "action_count": 0,
                "sample_lines": [],
                "owners": {},
                "action_patterns": {},
            }
        return sections[canonical]

    ensure_section(current_project)

    for raw in text.splitlines():
        line = mod._clean_line(raw)
        if not line:
            continue
        heading = mod._line_looks_like_project_heading(raw, known_projects)
        if heading:
            matched = mod._match_known_project_name(heading, known_projects)
            if matched:
                current_project = mod._canonical_project_display_name(matched)
                ensure_section(current_project)
                continue
        elif "：" in raw or ":" in raw:
            leading = raw.split("：", 1)[0].split(":", 1)[0]
            matched = mod._match_known_project_name(leading, known_projects)
            if matched:
                current_project = mod._canonical_project_display_name(matched)
                ensure_section(current_project)

        matched_inline = mod._infer_project_from_text(line, known_projects)
        project_name = mod._canonical_project_display_name(matched_inline or current_project or default_project)
        section = ensure_section(project_name)
        section["line_count"] += 1
        action_patterns = classify_action_patterns(line)
        if mod._line_looks_actionable(line) or action_patterns or re.search(r"(する|した|して|ます|予定|必要|依頼|確認)$", line):
            section["action_count"] += 1
        if len(section["sample_lines"]) < 8 and line not in section["sample_lines"]:
            section["sample_lines"].append(line[:180])
        _increment_counter_map(section["owners"], extract_owner_mentions(line))
        _increment_counter_map(section["action_patterns"], action_patterns)

    return sections


def _top_counter(counter_map: Dict[str, int], limit: int = 5) -> List[Dict[str, Any]]:
    rows = sorted(counter_map.items(), key=lambda item: (-int(item[1] or 0), item[0]))
    return [{"value": key, "count": int(val or 0)} for key, val in rows[:limit]]


def _unique_keep_order(values: Iterable[str]) -> List[str]:
    seen = set()
    out: List[str] = []
    for value in values:
        key = (value or "").strip()
        if not key or key in seen:
            continue
        seen.add(key)
        out.append(key)
    return out


def _extract_tokiwagi_master_databases(structure: Dict[str, Any]) -> List[Dict[str, Any]]:
    for project in structure.get("projects", []):
        if (project.get("project") or "").strip() == DEFAULT_PROJECT:
            return [
                db
                for db in project.get("databases", [])
                if (db.get("title") or "").strip()
            ]
    return []


def _select_databases(databases: List[Dict[str, Any]]) -> List[Dict[str, Any]]:
    selected: List[Dict[str, Any]] = []
    for db in databases:
        title = (db.get("title") or "").strip()
        if not title:
            continue
        if title in DEFAULT_EXCLUDE_DB_TITLES:
            continue
        if title in DEFAULT_INCLUDE_DB_TITLES:
            selected.append(db)
    return selected


def _load_cached_pages() -> Dict[str, Any]:
    payload = read_json(STATE_PATH)
    pages = payload.get("pages")
    if isinstance(pages, dict):
        return pages
    return {}


def _summarize_project_with_ollama(mod: Any, env: Dict[str, str], project: Dict[str, Any]) -> Dict[str, Any]:
    prompt = (
        "You are building a Japanese project registry from internal meeting minutes. "
        "Return JSON with keys: aliases, owner_hints, action_patterns, summary. "
        "aliases must be an array of short project aliases. "
        "owner_hints must be an array of likely owner names. "
        "action_patterns must be an array of short work pattern labels. "
        "summary must be one short Japanese sentence describing the project context."
    )
    source_text = "\n".join(
        [
            f"Project: {project.get('project')}",
            "Doc titles:",
            *[f"- {title}" for title in project.get("sample_doc_titles", [])[:6]],
            "Sample lines:",
            *[f"- {line}" for line in project.get("sample_lines", [])[:12]],
        ]
    )
    parsed, meta = mod.run_ollama_json(
        prompt=prompt,
        source_text=source_text[:6000],
        env=env,
        model=(env.get("ROBY_TOKIWAGI_REGISTRY_MODEL") or "ollama/qwen2.5:7b"),
        timeout_sec=int(env.get("ROBY_TOKIWAGI_REGISTRY_TIMEOUT_SEC", "60") or "60"),
        num_predict=int(env.get("ROBY_TOKIWAGI_REGISTRY_NUM_PREDICT", "1200") or "1200"),
        temperature=float(env.get("ROBY_TOKIWAGI_REGISTRY_TEMPERATURE", "0.1") or "0.1"),
        top_p=float(env.get("ROBY_TOKIWAGI_REGISTRY_TOP_P", "0.9") or "0.9"),
        repeat_penalty=float(env.get("ROBY_TOKIWAGI_REGISTRY_REPEAT_PENALTY", "1.05") or "1.05"),
    )
    if not isinstance(parsed, dict):
        return {"ok": False, "meta": meta}
    return {
        "ok": True,
        "aliases": _unique_keep_order(parsed.get("aliases", []) or []),
        "owner_hints": _unique_keep_order(parsed.get("owner_hints", []) or []),
        "action_patterns": _unique_keep_order(parsed.get("action_patterns", []) or []),
        "summary": str(parsed.get("summary") or "").strip(),
        "meta": meta,
    }


def build_registry(
    *,
    env: Dict[str, str],
    mod: Any,
    structure: Dict[str, Any],
    refresh: bool,
    max_pages_per_db: int,
    use_local_llm: bool,
) -> Dict[str, Any]:
    token = mod.load_notion_key(env)
    if not token:
        raise RuntimeError("Notion token not found.")

    selected_databases = _select_databases(_extract_tokiwagi_master_databases(structure))
    if not selected_databases:
        raise RuntimeError("TOKIWAGI_MASTER target databases not found in cached structure.")

    known_projects = [db.get("project") for db in structure.get("databases", [])]
    version = env.get("NOTION_VERSION", "2025-09-03")
    page_cache = _load_cached_pages()
    docs: List[Dict[str, Any]] = []
    changed_count = 0
    llm_used_projects = 0
    db_pages: List[Tuple[Dict[str, Any], List[Dict[str, Any]]]] = []
    total_pages = 0

    for db in selected_databases:
        db_id = db.get("id") or ""
        pages = mod.list_database_pages(db_id, token, version, None, max_pages_per_db)
        db_pages.append((db, pages))
        total_pages += len(pages)

    processed_pages = 0
    write_progress(
        {
            "updated_at": datetime.now(JST).isoformat(),
            "stage": "reading_pages",
            "database_titles": [db.get("title") for db in selected_databases],
            "total_pages": total_pages,
            "processed_pages": processed_pages,
            "changed_documents": changed_count,
            "llm_used_projects": llm_used_projects,
        }
    )

    for db, pages in db_pages:
        db_title = db.get("title") or DEFAULT_PROJECT
        for page in pages:
            page_id = page.get("id") or ""
            last_edited = page.get("last_edited_time") or ""
            cached = page_cache.get(page_id) if not refresh else None
            if cached and cached.get("last_edited") == last_edited:
                docs.append(cached)
                processed_pages += 1
                continue

            text = mod.fetch_page_text(page_id, token, version)
            primary_project = mod._canonical_project_display_name(
                mod.infer_primary_project(
                    text=text,
                    known_projects=known_projects,
                    source_title=mod.extract_page_title(page),
                    fallback_project=DEFAULT_PROJECT,
                )
            )
            sections = extract_project_sections(
                text,
                default_project=primary_project or DEFAULT_PROJECT,
                known_projects=known_projects,
                source_title=mod.extract_page_title(page),
                mod=mod,
            )
            owners_global = Counter(extract_owner_mentions(text))
            patterns_global = Counter()
            for raw in text.splitlines():
                line = mod._clean_line(raw)
                if not line:
                    continue
                patterns_global.update(classify_action_patterns(line))

            entry = {
                "page_id": page_id,
                "title": mod.extract_page_title(page),
                "db_title": db_title,
                "project": DEFAULT_PROJECT,
                "url": page.get("url", ""),
                "last_edited": last_edited,
                "text_length": len(text),
                "primary_project": primary_project or DEFAULT_PROJECT,
                "sections": sections,
                "owner_mentions": dict(owners_global),
                "action_patterns": dict(patterns_global),
                "sample_excerpt": "\n".join(text.splitlines()[:30])[:1200],
            }
            page_cache[page_id] = entry
            docs.append(entry)
            changed_count += 1
            processed_pages += 1
            if processed_pages == total_pages or processed_pages % 10 == 0:
                write_progress(
                    {
                        "updated_at": datetime.now(JST).isoformat(),
                        "stage": "reading_pages",
                        "database_titles": [db.get("title") for db in selected_databases],
                        "total_pages": total_pages,
                        "processed_pages": processed_pages,
                        "changed_documents": changed_count,
                        "llm_used_projects": llm_used_projects,
                    }
                )

    project_registry_map: Dict[str, Dict[str, Any]] = {}
    owner_registry = Counter()
    action_registry = Counter()

    for doc in docs:
        for section_name, section in (doc.get("sections") or {}).items():
            project_name = mod._canonical_project_display_name(section_name or DEFAULT_PROJECT)
            entry = project_registry_map.setdefault(
                project_name,
                {
                    "project": project_name,
                    "doc_count": 0,
                    "db_titles": set(),
                    "sample_doc_titles": [],
                    "sample_lines": [],
                    "owner_counts": Counter(),
                    "action_pattern_counts": Counter(),
                    "aliases": mod._project_aliases(project_name),
                },
            )
            entry["doc_count"] += 1
            entry["db_titles"].add(doc.get("db_title") or "")
            if len(entry["sample_doc_titles"]) < 8:
                entry["sample_doc_titles"].append(doc.get("title") or "")
            for line in section.get("sample_lines", [])[:8]:
                if line not in entry["sample_lines"] and len(entry["sample_lines"]) < 16:
                    entry["sample_lines"].append(line)
            entry["owner_counts"].update(section.get("owners") or {})
            entry["action_pattern_counts"].update(section.get("action_patterns") or {})
            owner_registry.update(section.get("owners") or {})
            action_registry.update(section.get("action_patterns") or {})

    project_registry: List[Dict[str, Any]] = []
    for project_name, entry in sorted(
        project_registry_map.items(),
        key=lambda item: (-int(item[1]["doc_count"]), item[0]),
    ):
        row = {
            "project": project_name,
            "doc_count": int(entry["doc_count"]),
            "db_titles": sorted([x for x in entry["db_titles"] if x]),
            "aliases": _unique_keep_order(entry["aliases"]),
            "sample_doc_titles": _unique_keep_order(entry["sample_doc_titles"])[:8],
            "sample_lines": entry["sample_lines"][:16],
            "top_owners": _top_counter(dict(entry["owner_counts"]), limit=5),
            "top_action_patterns": _top_counter(dict(entry["action_pattern_counts"]), limit=5),
        }
        if use_local_llm:
            write_progress(
                {
                    "updated_at": datetime.now(JST).isoformat(),
                    "stage": "ollama_enrichment",
                    "database_titles": [db.get("title") for db in selected_databases],
                    "total_pages": total_pages,
                    "processed_pages": processed_pages,
                    "changed_documents": changed_count,
                    "llm_used_projects": llm_used_projects,
                    "current_project": project_name,
                    "projects_total": len(project_registry_map),
                }
            )
            enriched = _summarize_project_with_ollama(mod, env, row)
            row["local_llm"] = enriched
            if enriched.get("ok"):
                llm_used_projects += 1
        project_registry.append(row)

    registry = {
        "generated_at": datetime.now(JST).isoformat(),
        "scope": DEFAULT_PROJECT,
        "database_titles": [db.get("title") for db in selected_databases],
        "counts": {
            "databases": len(selected_databases),
            "documents": len(docs),
            "changed_documents": changed_count,
            "projects": len(project_registry),
            "owners": len(owner_registry),
            "action_patterns": len(action_registry),
            "llm_used_projects": llm_used_projects,
        },
        "project_registry": project_registry,
        "owner_registry": _top_counter(dict(owner_registry), limit=50),
        "action_pattern_registry": _top_counter(dict(action_registry), limit=20),
        "documents": docs,
    }

    state_payload = {
        "updated_at": registry["generated_at"],
        "scope": DEFAULT_PROJECT,
        "pages": page_cache,
        "latest_registry_counts": registry["counts"],
    }
    write_json(STATE_PATH, state_payload)
    write_json(LATEST_REGISTRY_PATH, registry)
    write_progress(
        {
            "updated_at": registry["generated_at"],
            "stage": "completed",
            "database_titles": [db.get("title") for db in selected_databases],
            "total_pages": total_pages,
            "processed_pages": processed_pages,
            "changed_documents": changed_count,
            "llm_used_projects": llm_used_projects,
            "projects_total": len(project_registry),
        }
    )
    return registry


def main() -> int:
    parser = argparse.ArgumentParser()
    parser.add_argument("--refresh", action="store_true")
    parser.add_argument("--skip-local-llm", action="store_true")
    parser.add_argument("--max-pages-per-db", type=int, default=5000)
    parser.add_argument("--json", action="store_true")
    args = parser.parse_args()

    start = time.time()
    mod = load_minutes_module()
    env = mod.load_env()
    notion_root = env.get("TOKIWAGI_ROOT_ID") or env.get("NOTION_TOKIWAGI_ID") or ""
    structure = mod.load_cached_structure(notion_root)
    if not structure or args.refresh:
        token = mod.load_notion_key(env)
        if not token:
            raise SystemExit("ERROR: Notion token missing.")
        structure = mod.build_notion_structure(
            notion_root,
            token,
            env.get("NOTION_VERSION", "2025-09-03"),
            int(args.max_pages_per_db or 5000),
        )
        mod.save_cached_structure(structure)

    registry = build_registry(
        env=env,
        mod=mod,
        structure=structure,
        refresh=args.refresh,
        max_pages_per_db=int(args.max_pages_per_db or 5000),
        use_local_llm=not args.skip_local_llm,
    )
    elapsed_ms = int((time.time() - start) * 1000)
    summary = {
        "generated_at": registry["generated_at"],
        "scope": DEFAULT_PROJECT,
        "counts": registry["counts"],
        "database_titles": registry["database_titles"],
        "elapsed_ms": elapsed_ms,
    }
    append_jsonl(RUN_LOG_PATH, summary)
    append_audit_event(
        "precision.registry_build",
        "info",
        "TOKIWAGI_MASTER registry build completed",
        counts=registry["counts"],
        elapsed_ms=elapsed_ms,
    )
    if args.json:
        print(json.dumps(summary, ensure_ascii=False))
    else:
        print(json.dumps(summary, ensure_ascii=False, indent=2))
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
