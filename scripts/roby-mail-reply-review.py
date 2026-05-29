#!/usr/bin/env python3
from __future__ import annotations

import argparse
import email.utils
import json
import os
import re
import subprocess
import sys
import tempfile
import time
import urllib.request
from pathlib import Path
from typing import Any, Dict, List, Optional, Tuple

OPENCLAW_SCRIPTS_DIR = Path(__file__).resolve().parent
if str(OPENCLAW_SCRIPTS_DIR) not in sys.path:
    sys.path.insert(0, str(OPENCLAW_SCRIPTS_DIR))

from roby_local_first import run_ollama_json

ENV_PATH = Path.home() / ".openclaw" / ".env"
STATE_PATH = Path.home() / ".openclaw" / "roby" / "gmail_reply_reviews.json"
LOG_PATH = Path.home() / ".openclaw" / "roby" / "gmail_reply_reviews.jsonl"

TOKIWAGI_SIGNATURE = """_/_/_/_/_/_/_/_/_/_/_/_/_/_/_/_/

【株式会社TOKIWAGI】
　テクニカルアーキテクト
・新後周平-SHUHEI NIGO
・TEL：080-4117-2153
・MAIL：s.nigo@tokiwa-gi.com

・〒150-0031  　
　billage SHIBUYA：東京都渋谷区桜丘町18-4 二宮ビル 1F 116
・TEL：03-4400-1169
・WEB：https://www.tokiwa-gi.com

_/_/_/_/_/_/_/_/_/_/_/_/_/_/_/_/"""
TAKATA_EMAIL = "a.takata@tokiwa-gi.com"


def load_env_file(path: Path = ENV_PATH) -> None:
    if not path.exists():
        return
    for line in path.read_text(encoding="utf-8").splitlines():
        s = line.strip()
        if not s or s.startswith("#") or "=" not in s:
            continue
        k, v = s.split("=", 1)
        key = k.strip()
        val = v.strip()
        if (val.startswith('"') and val.endswith('"')) or (val.startswith("'") and val.endswith("'")):
            val = val[1:-1]
        os.environ.setdefault(key, val)


def load_state() -> Dict[str, Any]:
    if not STATE_PATH.exists():
        return {"reviews": {}, "by_thread": {}, "by_message": {}}
    try:
        data = json.loads(STATE_PATH.read_text(encoding="utf-8"))
        if isinstance(data, dict):
            data.setdefault("reviews", {})
            data.setdefault("by_thread", {})
            data.setdefault("by_message", {})
            return data
    except Exception:
        pass
    return {"reviews": {}, "by_thread": {}, "by_message": {}}


def save_state(state: Dict[str, Any]) -> None:
    STATE_PATH.parent.mkdir(parents=True, exist_ok=True)
    tmp = STATE_PATH.with_suffix(".tmp")
    tmp.write_text(json.dumps(state, ensure_ascii=False, indent=2), encoding="utf-8")
    tmp.replace(STATE_PATH)


def append_log(payload: Dict[str, Any]) -> None:
    LOG_PATH.parent.mkdir(parents=True, exist_ok=True)
    with LOG_PATH.open("a", encoding="utf-8") as f:
        f.write(json.dumps({"ts": int(time.time()), **payload}, ensure_ascii=False) + "\n")


def slack_api_post_json(token: str, method: str, payload: Dict[str, Any]) -> Dict[str, Any]:
    req = urllib.request.Request(
        f"https://slack.com/api/{method}",
        data=json.dumps(payload).encode("utf-8"),
        headers={"Authorization": f"Bearer {token}", "Content-Type": "application/json; charset=utf-8"},
    )
    with urllib.request.urlopen(req, timeout=20) as resp:
        raw = resp.read().decode("utf-8", "replace")
    try:
        return json.loads(raw)
    except Exception:
        return {"ok": False, "error": "invalid_json", "raw": raw}


def post_message(token: str, channel: str, text: str, thread_ts: str = "") -> Tuple[bool, str]:
    payload = {"channel": channel, "text": text, "unfurl_links": False, "unfurl_media": False}
    if thread_ts:
        payload["thread_ts"] = thread_ts
    resp = slack_api_post_json(token, "chat.postMessage", payload)
    if not resp.get("ok"):
        return False, str(resp.get("error") or "slack_post_failed")
    return True, str(resp.get("ts") or thread_ts or "")


def parse_address_list(value: str) -> List[str]:
    addresses = []
    for _, addr in email.utils.getaddresses([value or ""]):
        clean = addr.strip()
        if clean:
            addresses.append(clean)
    seen = set()
    out: List[str] = []
    for addr in addresses:
        key = addr.lower()
        if key in seen:
            continue
        seen.add(key)
        out.append(addr)
    return out


def fetch_original_message_metadata(message_id: str, account: str = "") -> Dict[str, Any]:
    cmd = ["gog", "gmail", "get", message_id, "--format=metadata", "--headers=From,To,Cc,Subject"]
    if account:
        cmd += ["--account", account]
    proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
    if proc.returncode != 0:
        raise RuntimeError((proc.stderr or proc.stdout or f"exit={proc.returncode}")[:1200])
    raw = (proc.stdout or "").strip()
    if not raw:
        return {}
    try:
        data = json.loads(raw)
    except Exception:
        return {}
    headers = data.get("headers") if isinstance(data, dict) else {}
    if not isinstance(headers, dict):
        headers = {}
    return {
        "from": str(headers.get("from") or "").strip(),
        "to": str(headers.get("to") or "").strip(),
        "cc": str(headers.get("cc") or "").strip(),
        "subject": str(headers.get("subject") or "").strip(),
    }


def sender_display_name(sender: str) -> str:
    name, addr = email.utils.parseaddr(sender or "")
    value = (name or addr.split("@", 1)[0] if addr else sender or "").strip().strip('"')
    return re.sub(r"\s+", " ", value) or "ご担当者"


def strip_quoted_reply(text: str) -> str:
    """Keep only the newly written part of a Gmail-style reply."""
    lines = (text or "").replace("\r\n", "\n").replace("\r", "\n").split("\n")
    kept: List[str] = []
    quote_start_patterns = (
        r"^\s*>",
        r"^\s*20\d{2}年\d{1,2}月\d{1,2}日.*<[^>]+>[:：]\s*$",
        r"^\s*On .+ wrote:\s*$",
    )
    for line in lines:
        if any(re.search(pat, line) for pat in quote_start_patterns):
            break
        kept.append(line)
    return "\n".join(kept).strip()


def intro_identity(text: str) -> Tuple[str, str]:
    source = strip_quoted_reply(text)
    patterns = (
        r"([^\s　\r\n。]{2,40})の([^\s　\r\n。]{1,20})(?:と申します|です)",
        r"([^\s　\r\n。]{2,40})[ 　]+([^\s　\r\n。]{1,20})(?:と申します|です)",
    )
    for pat in patterns:
        m = re.search(pat, source)
        if not m:
            continue
        company = m.group(1).strip("、。:：")
        name = m.group(2).strip("、。:：")
        if company and name and "TOKIWAGI" not in company:
            return company, name
    return "", ""


def looks_self_authored(text: str) -> bool:
    source = strip_quoted_reply(text)
    return any(k in source for k in ("TOKIWAGIの新後です", "TOKIWAGI 新後です", "株式会社TOKIWAGI"))


def extract_company_name(text: str, sender: str = "") -> str:
    source = f"{text or ''}\n{sender or ''}"
    patterns = (
        r"(株式会社[^\s　]{2,30})",
        r"([^\s　]{2,30}株式会社)",
        r"(有限会社[^\s　]{2,30})",
        r"([^\s　]{2,30}有限会社)",
        r"(合同会社[^\s　]{2,30})",
        r"([^\s　]{2,30}合同会社)",
    )
    for pat in patterns:
        m = re.search(pat, source)
        if m:
            return m.group(1).strip("、。:：")
    return ""


def recipient_line(sender: str, source_body: str = "") -> str:
    intro_company, intro_name = intro_identity(source_body)
    if intro_company and intro_name:
        return f"{intro_company}の{intro_name}様"
    name = sender_display_name(sender)
    company = extract_company_name(source_body, sender)
    if company and company not in name:
        return f"{company}の{name}様"
    return f"{name}様"


def has_reply_template(body: str) -> bool:
    text = body or ""
    return "TOKIWAGIの新後です。" in text and "_/_/_/_/_/_/_/_/_/_/_/_/_/_/_/_/" in text


def apply_reply_template(body: str, to_line: str) -> str:
    clean = (body or "").strip()
    if has_reply_template(clean):
        return clean
    header = f"{to_line}\n\nお世話になっております。\nTOKIWAGIの新後です。"
    return f"{header}\n\n{clean}\n\n{TOKIWAGI_SIGNATURE}"


def apply_template_to_candidates(candidates: List[Dict[str, str]], to_line: str) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    for cand in candidates:
        c = dict(cand)
        c["body"] = apply_reply_template(str(c.get("body") or ""), to_line)
        out.append(c)
    return out


def merge_cc_values(*values: str) -> str:
    merged: List[str] = []
    seen = set()
    for value in values:
        for addr in parse_address_list(value):
            key = addr.lower()
            if key in seen:
                continue
            seen.add(key)
            merged.append(addr)
    return ", ".join(merged)


def cc_has_takata(cc_value: str) -> bool:
    return TAKATA_EMAIL.lower() in {addr.lower() for addr in parse_address_list(cc_value)}


def parse_takata_cc_preference(text: str) -> Optional[bool]:
    raw = (text or "").strip().lower()
    if not raw:
        return None
    direct_true = {"true", "1", "yes", "y", "on", "あり", "入れる", "入れて", "ccあり", "cc入れ"}
    direct_false = {"false", "0", "no", "n", "off", "なし", "外す", "外して", "入れない", "ccなし", "cc不要"}
    if raw in direct_true:
        return True
    if raw in direct_false:
        return False
    if re.search(r"(高田さん|高田).*(入れて|入れる|追加|含め|ccあり|ccに入れて|cc入れ)", raw):
        return True
    if re.search(r"(高田さん|高田).*(外して|外す|入れない|不要|ccなし|cc入れない)", raw):
        return False
    if re.search(r"cc\s*[:=]?\s*(true|on|1|あり|入れて)", raw):
        return True
    if re.search(r"cc\s*[:=]?\s*(false|off|0|なし|外して|不要)", raw):
        return False
    return None


def candidate_prompt() -> str:
    return """
あなたは日本語ビジネスメールの返信文を作るアシスタントです。
入力メールは「相手からユーザーに届いたメール」です。
あなたは必ず「受信者であるユーザー側」から「送信者へ返す」返信候補を2案作ってください。
相手の依頼内容を、こちらから相手へ再依頼する文に反転してはいけません。

最重要方針:
- 一発で完璧に回答できない場合は、まず「メールを受け取った」「内容を確認する」「確認後に改めて連絡する」という取り急ぎ返信を作る。
- 相手が「発注書/契約書/見積書を確認してほしい」と言っている場合は、「ご発注/ご送付ありがとうございます。確認します」と返す。
- 相手が「日程候補がほしい」と言っている場合は、「打ち合わせについて承知しました。候補日は確認のうえ追って連絡します」と返す。
- こちらが前回「追って連絡する」と返した後に、相手が「まだいただけていない」「いかがでしょうか」と催促している場合は、必ず「遅くなり申し訳ありません」というお詫びを入れる。
- 相手が急ぎと言っている場合は、放置しない印象になるよう、受領と確認予定を明示する。
- 冒頭で「ありがとうございます」を連続させない。例: 1行目「ご連絡ありがとうございます。」、2行目「ご発注ありがとうございます。」のような重複は避け、どちらか一方にまとめる。

要件:
- 1案目は短い取り急ぎ返信。
- 2案目は少し丁寧な取り急ぎ返信。
- まだ確認していない事実を「確認済み」と書かない。
- 日程候補や具体回答を勝手に作らない。
- 日程候補の催促に対して、候補日が未確定なら「至急確認のうえ追って連絡する」と書く。具体日程を捏造しない。
- 送信本文は読みやすいように、意味の区切りごとに改行する。
- 件名や宛名は本文に含めなくてよい。本文のみ。
- 署名は入れない。
- 返信不要・確認のみのメールに見える場合でも、受領/確認中である旨の短い返信案にする。
出力はJSONのみ: {"summary":"メール要約", "candidates":[{"label":"短い取り急ぎ返信", "body":"本文"},{"label":"丁寧な取り急ぎ返信", "body":"本文"}]}
"""


def extract_json_value(data: Dict[str, Any]) -> str:
    for key in ("summary", "output", "text", "result"):
        val = data.get(key)
        if isinstance(val, str) and val.strip():
            return val
    return ""


def parse_jsonish_text(raw: str) -> Any:
    if not raw:
        return None
    cleaned = raw.strip()
    cleaned = re.sub(r"^```(?:json)?\s*", "", cleaned, flags=re.IGNORECASE)
    cleaned = re.sub(r"\s*```$", "", cleaned)
    try:
        return json.loads(cleaned)
    except Exception:
        m = re.search(r"(\{.*\}|\[.*\])", cleaned, re.DOTALL)
        if not m:
            return None
        try:
            return json.loads(m.group(1))
        except Exception:
            return None


def run_reply_json_prompt(
    *,
    prompt: str,
    source_text: str,
    env: Dict[str, str],
    model: str,
    timeout_sec: int,
    num_predict: int,
    temperature: float,
) -> Tuple[Optional[Any], Dict[str, Any]]:
    normalized = (model or "").strip()
    if not normalized or normalized.lower().startswith("ollama/") or "/" not in normalized:
        return run_ollama_json(
            prompt=prompt,
            source_text=source_text,
            env=env,
            model=normalized,
            timeout_sec=timeout_sec,
            num_predict=num_predict,
            temperature=temperature,
        )

    cmd = [
        "summarize",
        "-",
        "--json",
        "--plain",
        "--metrics",
        "off",
        "--model",
        normalized,
        "--length",
        "short",
        "--force-summary",
        "--prompt",
        prompt,
        "--max-output-tokens",
        str(num_predict),
    ]
    try:
        out = subprocess.check_output(
            cmd,
            input=source_text.encode("utf-8"),
            env=env,
            timeout=timeout_sec,
            stderr=subprocess.STDOUT,
        )
    except subprocess.CalledProcessError as e:
        return None, {
            "ok": False,
            "backend": "summarize",
            "model": normalized,
            "error": "summarize_failed",
            "detail": (e.output or b"").decode("utf-8", "replace")[:1200],
        }
    except Exception as e:
        return None, {"ok": False, "backend": "summarize", "model": normalized, "error": str(e)}

    try:
        data = json.loads(out.decode("utf-8", "replace"))
        raw = extract_json_value(data)
    except Exception:
        raw = out.decode("utf-8", "replace")
    parsed = parse_jsonish_text(raw)
    return parsed, {"ok": parsed is not None, "backend": "summarize", "model": normalized}


def revise_prompt() -> str:
    return """
あなたは日本語ビジネスメールの返信文を修正するアシスタントです。
元の返信案を、ユーザーの修正指示に沿って書き直してください。
要件:
- 宛名、冒頭挨拶、署名はシステム側で付与するため、中心本文のみを返す。
- 修正指示にない事実は足さない。
出力はJSONのみ: {"body":"修正版本文"}
"""


def normalize_candidates(parsed: Any) -> Tuple[str, List[Dict[str, str]]]:
    if not isinstance(parsed, dict):
        return "", []
    summary = str(parsed.get("summary") or "").strip()
    candidates: List[Dict[str, str]] = []
    for row in parsed.get("candidates") or []:
        if not isinstance(row, dict):
            continue
        label = str(row.get("label") or f"案{len(candidates)+1}").strip()
        body = str(row.get("body") or "").strip()
        if body:
            candidates.append({"label": label[:80], "body": body})
        if len(candidates) >= 2:
            break
    return summary, candidates


def looks_reversed_reply(body: str) -> bool:
    text = (body or "").strip()
    bad_patterns = (
        r"発注書.*確認できました(?:でしょう)?か",
        r"発注書.*ご確認いただけますか",
        r"日程候補.*いただけますか",
        r"候補日.*いただけますか",
        r"打ち合わせ.*日程候補.*ください",
    )
    return any(re.search(pat, text) for pat in bad_patterns)


def polish_reply_body(body: str) -> str:
    lines = [line.rstrip() for line in (body or "").strip().splitlines()]
    lines = [line for line in lines if line.strip()]
    if len(lines) >= 2 and "ありがとうございます" in lines[0] and "ありがとうございます" in lines[1]:
        # 「ご連絡ありがとうございます。ご発注ありがとうございます。」のような連続感謝を避ける。
        if re.search(r"(発注|注文|送付|契約書|見積書|資料).{0,12}ありがとうございます", lines[1]):
            lines.pop(0)
        else:
            lines[1] = re.sub(r"^(ご連絡|ご確認|ご送付|ご発注)[^。]*ありがとうございます。?", "", lines[1]).strip() or lines[1]
    return "\n".join(lines)


def polish_candidates(candidates: List[Dict[str, str]]) -> List[Dict[str, str]]:
    out: List[Dict[str, str]] = []
    for cand in candidates:
        c = dict(cand)
        c["body"] = polish_reply_body(str(c.get("body") or ""))
        out.append(c)
    return out


def deterministic_ack_candidates(subject: str, source_body: str) -> Tuple[str, List[Dict[str, str]]] | None:
    text = f"{subject}\n{strip_quoted_reply(source_body)}"
    has_order_doc = any(k in text for k in ("発注書", "注文書", "契約書", "見積書"))
    has_schedule = any(k in text for k in ("日程候補", "候補日", "打ち合わせ", "ミーティング"))
    is_followup_delay = has_schedule and (
        any(k in text for k in ("まだいただけていない", "まだ頂けていない", "まだいただいていない", "未着", "未確認", "未送付", "急ぎで候補日"))
        or ("その後" in text and "いかがでしょうか" in text)
    )
    if not (has_order_doc or has_schedule):
        return None

    if is_followup_delay:
        return (
            "打ち合わせ候補日の催促があり、こちらからの連絡遅れに対するお詫びと、至急確認して追って連絡する旨を返す必要があります。",
            [
                {
                    "label": "お詫びを入れた取り急ぎ返信",
                    "body": "\n".join([
                        "ご連絡ありがとうございます。",
                        "打ち合わせ候補日のご連絡が遅くなり、大変申し訳ありません。",
                        "至急、候補日を確認のうえ、追ってご連絡いたします。",
                        "取り急ぎのお返事となり恐縮ですが、よろしくお願いいたします。",
                    ]),
                },
                {
                    "label": "少し丁寧なお詫び返信",
                    "body": "\n".join([
                        "ご連絡いただきありがとうございます。",
                        "日程候補のご連絡が遅くなっており、申し訳ございません。",
                        "急ぎの件として受け止め、候補日を確認のうえ、整理でき次第ご連絡いたします。",
                        "引き続きよろしくお願いいたします。",
                    ]),
                },
            ],
        )

    parts1: List[str] = []
    parts2: List[str] = []
    if has_order_doc:
        if "発注書" in text or "注文書" in text:
            parts1.append("発注書をご送付いただき、ありがとうございます。内容はこのあと確認させていただきます。")
            parts2.append("発注書のご送付ありがとうございます。内容を確認のうえ、改めてご連絡いたします。")
        elif "契約書" in text:
            parts1.append("契約書をご送付いただき、ありがとうございます。内容はこのあと確認させていただきます。")
            parts2.append("契約書のご送付ありがとうございます。内容を確認のうえ、必要事項を整理して改めてご連絡いたします。")
        else:
            parts1.append("資料をご送付いただき、ありがとうございます。内容はこのあと確認させていただきます。")
            parts2.append("資料のご送付ありがとうございます。内容を確認のうえ、改めてご連絡いたします。")
    else:
        parts1.append("ご連絡ありがとうございます。")
        parts2.append("ご連絡いただきありがとうございます。")
    if has_schedule:
        parts1.append("打ち合わせについても承知しました。候補日については確認のうえ、追ってお知らせいたします。")
        parts2.append("今後のお打ち合わせについても承知いたしました。社内の予定を確認し、候補日を整理して追ってご連絡いたします。")
    parts1.append("取り急ぎ、受領のご連絡まで失礼いたします。")
    parts2.append("まずは受領のご連絡まで失礼いたします。")
    return (
        "発注書等の確認依頼と、打ち合わせ日程候補の依頼が含まれています。",
        [
            {"label": "短い取り急ぎ返信", "body": "\n".join(parts1)},
            {"label": "丁寧な取り急ぎ返信", "body": "\n".join(parts2)},
        ],
    )


def generate_candidates(env: Dict[str, str], subject: str, sender: str, body: str) -> Tuple[str, List[Dict[str, str]], Dict[str, Any]]:
    model = (env.get("GMAIL_REPLY_REVIEW_MODEL") or env.get("ROBY_ORCH_GMAIL_LLM_FAST_MODEL") or "openai/gpt-5.4").strip()
    clean_body = strip_quoted_reply(body)
    source = f"Subject: {subject}\nFrom: {sender}\n\n{clean_body[:6000]}"
    parsed, meta = run_reply_json_prompt(
        prompt=candidate_prompt(),
        source_text=source,
        env=env,
        model=model,
        timeout_sec=int(env.get("GMAIL_REPLY_REVIEW_TIMEOUT_SEC", "45") or "45"),
        num_predict=int(env.get("GMAIL_REPLY_REVIEW_NUM_PREDICT", "900") or "900"),
        temperature=0.25,
    )
    summary, candidates = normalize_candidates(parsed)
    candidates = polish_candidates(candidates)
    deterministic = deterministic_ack_candidates(subject, clean_body)
    to_line = recipient_line(sender, clean_body)
    if deterministic and "催促" in deterministic[0]:
        summary2, candidates2 = deterministic
        return summary2, apply_template_to_candidates(polish_candidates(candidates2), to_line), {**meta, "deterministic_ack": True, "deterministic_reason": "followup_delay", "recipient_line": to_line}
    if deterministic and (len(candidates) < 2 or any(looks_reversed_reply(c.get("body", "")) for c in candidates)):
        summary2, candidates2 = deterministic
        return summary or summary2, apply_template_to_candidates(polish_candidates(candidates2), to_line), {**meta, "deterministic_ack": True, "recipient_line": to_line}
    if len(candidates) >= 2:
        return summary, apply_template_to_candidates(candidates, to_line), {**meta, "recipient_line": to_line}
    if deterministic:
        summary2, candidates2 = deterministic
        return summary or summary2, apply_template_to_candidates(polish_candidates(candidates2), to_line), {**meta, "deterministic_ack": True, "recipient_line": to_line}
    fallback = [
        {"label": "簡潔に確認", "body": "ご連絡ありがとうございます。\n内容を確認のうえ、改めてご連絡いたします。"},
        {"label": "丁寧に確認", "body": "ご連絡ありがとうございます。\nいただいた内容を確認いたします。確認でき次第、必要事項を整理して改めてご連絡いたします。"},
    ]
    return (
        summary or "返信案を自動生成できなかったため、確認中の返信案を作成しました。",
        apply_template_to_candidates(fallback, to_line),
        {**meta, "recipient_line": to_line},
    )


def build_reply_context(review: Dict[str, Any]) -> Dict[str, str]:
    ctx = review.get("original_headers")
    if isinstance(ctx, dict):
        return {
            "from": str(ctx.get("from") or "").strip(),
            "to": str(ctx.get("to") or "").strip(),
            "cc": str(ctx.get("cc") or "").strip(),
            "subject": str(ctx.get("subject") or "").strip(),
        }
    try:
        fetched = fetch_original_message_metadata(str(review.get("message_id") or ""), str(review.get("account") or ""))
    except Exception:
        fetched = {}
    return {
        "from": str(fetched.get("from") or review.get("sender") or "").strip(),
        "to": str(fetched.get("to") or "").strip(),
        "cc": str(fetched.get("cc") or "").strip(),
        "subject": str(fetched.get("subject") or review.get("subject") or "").strip(),
    }


def format_review_message(review: Dict[str, Any]) -> str:
    subject = review.get("subject") or "(件名なし)"
    sender = review.get("sender") or "-"
    summary = review.get("summary") or "-"
    cc_value = str(review.get("original_cc") or "").strip()
    cc_note = "あり" if bool(review.get("original_cc_has_takata")) else "なし"
    takata_note = "あり" if bool(review.get("include_takata_cc", True)) else "なし"
    cc_suffix = f" / 元CC: {cc_value}" if cc_value else ""
    candidates = review.get("candidates") or []
    lines = [
        "📩 *重要メールの返信案を作成しました*",
        f"*件名*: {subject}",
        f"*送信元*: {sender}",
        f"*要約*: {summary}",
        f"*CC判定*: 高田さん{cc_note} / 送信時追加{takata_note}{cc_suffix}",
        "",
    ]
    for i, c in enumerate(candidates[:2], start=1):
        lines.append(f"*{i}. {c.get('label') or f'案{i}'}*")
        lines.append(f"```{str(c.get('body') or '').strip()[:2500]}```")
    lines.extend([
        "このスレッドで `1` または `2` と送ると、その案で返信します。",
        "本文をそのまま送りたい場合は、返信文を直接入力してください。",
        "修正する場合は `1をもう少し丁寧に` のように送ってください。",
        "取り消す場合は `キャンセル` と送ってください。",
    ])
    return "\n".join(lines)


def configured_channel(env: Dict[str, str]) -> str:
    for key in ("GMAIL_REPLY_REVIEW_CHANNEL", "ROBY_GMAIL_REPLY_REVIEW_CHANNEL"):
        value = (env.get(key) or "").strip()
        if value:
            return value
    for key in ("ROBY_ALLOWED_SLACK_CHANNELS", "ROBY_SLACK_BACKFILL_CHANNELS"):
        raw = (env.get(key) or "").strip()
        if raw:
            return raw.split(",")[0].strip()
    return ""


def propose(args: argparse.Namespace) -> int:
    load_env_file()
    env = os.environ.copy()
    token = (env.get("SLACK_BOT_TOKEN") or "").strip()
    channel = (args.channel or configured_channel(env)).strip()
    if not token or not channel:
        print(json.dumps({"ok": False, "skipped": True, "reason": "missing_slack_token_or_channel"}, ensure_ascii=False))
        return 0
    state = load_state()
    if args.message_id in state.get("by_message", {}):
        print(json.dumps({"ok": True, "skipped": True, "reason": "already_proposed"}, ensure_ascii=False))
        return 0
    body = Path(args.body_file).read_text(encoding="utf-8") if args.body_file else args.body
    if looks_self_authored(body):
        print(json.dumps({"ok": True, "skipped": True, "reason": "self_authored_body"}, ensure_ascii=False))
        return 0
    summary, candidates, meta = generate_candidates(env, args.subject, args.sender, body)
    try:
        original_headers = fetch_original_message_metadata(args.message_id, args.account)
    except Exception:
        original_headers = {}
    cc_value = str(original_headers.get("cc") or "").strip()
    review_id = f"mailreply:{args.message_id}:{int(time.time())}"
    review = {
        "id": review_id,
        "status": "pending",
        "account": args.account,
        "message_id": args.message_id,
        "thread_id": args.thread_id,
        "subject": args.subject,
        "sender": args.sender,
        "recipient_line": meta.get("recipient_line") or recipient_line(args.sender, body),
        "original_headers": original_headers,
        "original_cc": cc_value,
        "original_cc_has_takata": cc_has_takata(cc_value),
        "include_takata_cc": True,
        "summary": summary,
        "candidates": candidates,
        "channel": channel,
        "created_at": int(time.time()),
        "llm_meta": meta,
    }
    ok, ts_or_err = post_message(token, channel, format_review_message(review))
    if not ok:
        print(json.dumps({"ok": False, "error": ts_or_err}, ensure_ascii=False))
        return 0
    review["slack_thread_ts"] = ts_or_err
    state["reviews"][review_id] = review
    state["by_thread"][f"{channel}:{ts_or_err}"] = review_id
    state["by_message"][args.message_id] = review_id
    save_state(state)
    append_log({"event": "proposed", "review_id": review_id, "message_id": args.message_id, "channel": channel, "thread_ts": ts_or_err})
    print(json.dumps({"ok": True, "review_id": review_id, "thread_ts": ts_or_err}, ensure_ascii=False))
    return 0


def send_reply(review: Dict[str, Any], body: str) -> Tuple[bool, str]:
    to_line = str(review.get("recipient_line") or recipient_line(str(review.get("sender") or ""))).strip()
    body = apply_reply_template(body, to_line)
    reply_ctx = build_reply_context(review)
    cc_value = str(review.get("original_cc") or reply_ctx.get("cc") or "").strip()
    cc_recipients = parse_address_list(cc_value)
    include_takata = bool(review.get("include_takata_cc", True))
    cc_arg = TAKATA_EMAIL if include_takata and TAKATA_EMAIL.lower() not in {addr.lower() for addr in cc_recipients} else ""
    with tempfile.NamedTemporaryFile("w", encoding="utf-8", delete=False) as f:
        f.write(body.strip() + "\n")
        body_path = f.name
    cmd = [
        "gog", "gmail", "send",
        "--reply-to-message-id", review["message_id"],
        "--reply-all",
        "--quote",
        "--subject", _reply_subject(review.get("subject") or ""),
        "--body-file", body_path,
        "--no-input", "--force",
    ]
    if cc_arg:
        cmd += ["--cc", cc_arg]
    if review.get("account"):
        cmd += ["--account", str(review.get("account"))]
    try:
        proc = subprocess.run(cmd, capture_output=True, text=True, timeout=60)
        if proc.returncode != 0:
            return False, (proc.stderr or proc.stdout or f"exit={proc.returncode}")[:1200]
        return True, (proc.stdout or "sent").strip()[:1200]
    finally:
        try:
            Path(body_path).unlink()
        except Exception:
            pass


def _reply_subject(subject: str) -> str:
    s = subject.strip() or "(件名なし)"
    if re.match(r"^(re|返信):", s, flags=re.I):
        return s
    return f"Re: {s}"


def revise_candidate(env: Dict[str, str], review: Dict[str, Any], idx: int, instruction: str) -> Tuple[str, Dict[str, Any]]:
    candidates = review.get("candidates") or []
    base = str(candidates[idx].get("body") or "") if idx < len(candidates) else ""
    source = f"元の返信案:\n{base}\n\n修正指示:\n{instruction}\n\n元メール件名:\n{review.get('subject','')}\n元メール要約:\n{review.get('summary','')}"
    model = (env.get("GMAIL_REPLY_REVIEW_MODEL") or env.get("ROBY_ORCH_GMAIL_LLM_FAST_MODEL") or "openai/gpt-5.4").strip()
    parsed, meta = run_reply_json_prompt(
        prompt=revise_prompt(),
        source_text=source,
        env=env,
        model=model,
        timeout_sec=45,
        num_predict=700,
        temperature=0.25,
    )
    if isinstance(parsed, dict) and str(parsed.get("body") or "").strip():
        to_line = str(review.get("recipient_line") or recipient_line(str(review.get("sender") or ""))).strip()
        return apply_reply_template(str(parsed.get("body") or "").strip(), to_line), meta
    # Fallback: keep instruction visible but do not invent too much.
    to_line = str(review.get("recipient_line") or recipient_line(str(review.get("sender") or ""))).strip()
    return apply_reply_template(base + "\n\n" + instruction.strip(), to_line), meta


def handle_slack(args: argparse.Namespace) -> int:
    load_env_file()
    env = os.environ.copy()
    token = (env.get("SLACK_BOT_TOKEN") or "").strip()
    if not token:
        print(json.dumps({"handled": False, "reason": "missing_slack_token"}, ensure_ascii=False))
        return 0
    state = load_state()
    key = f"{args.channel}:{args.thread}"
    review_id = state.get("by_thread", {}).get(key)
    if not review_id:
        print(json.dumps({"handled": False, "reason": "no_pending_review"}, ensure_ascii=False))
        return 0
    review = state.get("reviews", {}).get(review_id)
    if not isinstance(review, dict) or review.get("status") != "pending":
        print(json.dumps({"handled": False, "reason": "not_pending"}, ensure_ascii=False))
        return 0
    text = (args.text or "").strip()
    low = text.lower()
    if low in {"キャンセル", "cancel", "取り消し", "中止"}:
        review["status"] = "cancelled"
        review["cancelled_at"] = int(time.time())
        save_state(state)
        post_message(token, args.channel, "このメール返信案はキャンセルしました。", args.thread)
        print(json.dumps({"handled": True, "action": "cancelled"}, ensure_ascii=False))
        return 0
    m = re.fullmatch(r"([12])", text)
    if m:
        idx = int(m.group(1)) - 1
        candidates = review.get("candidates") or []
        if idx >= len(candidates):
            post_message(token, args.channel, "その番号の返信案はありません。`1` または `2` を指定してください。", args.thread)
            print(json.dumps({"handled": True, "action": "invalid_choice"}, ensure_ascii=False))
            return 0
        body = str(candidates[idx].get("body") or "").strip()
        ok, detail = send_reply(review, body)
        if ok:
            review["status"] = "sent"
            review["sent_at"] = int(time.time())
            review["sent_candidate"] = idx + 1
            save_state(state)
            post_message(token, args.channel, f"送信しました。選択: {idx + 1}", args.thread)
            append_log({"event": "sent", "review_id": review_id, "candidate": idx + 1})
        else:
            post_message(token, args.channel, f"送信に失敗しました。\n```{detail}```", args.thread)
            append_log({"event": "send_failed", "review_id": review_id, "error": detail})
        print(json.dumps({"handled": True, "action": "sent" if ok else "send_failed"}, ensure_ascii=False))
        return 0
    takata_pref = parse_takata_cc_preference(text)
    if takata_pref is not None:
        review["include_takata_cc"] = takata_pref
        review.setdefault("revisions", []).append({"at": int(time.time()), "kind": "takata_cc_preference", "value": takata_pref, "text": text})
        save_state(state)
        status = "入れます" if takata_pref else "入れません"
        post_message(token, args.channel, f"了解です。高田さんCCは `{status}` に変更しました。", args.thread)
        print(json.dumps({"handled": True, "action": "takata_cc_preference", "value": takata_pref}, ensure_ascii=False))
        return 0
    m = re.match(r"^([12])\s*(?:を|:|：)?\s*(.+)$", text, flags=re.S)
    if m:
        idx = int(m.group(1)) - 1
        instruction = m.group(2).strip()
    elif text.startswith(("修正", "変更")):
        idx = 0
        instruction = re.sub(r"^(修正|変更)[:：]?", "", text).strip() or text
    else:
        direct_body = text.strip()
        if direct_body:
            ok, detail = send_reply(review, direct_body)
            if ok:
                review["status"] = "sent"
                review["sent_at"] = int(time.time())
                review["sent_mode"] = "direct_body"
                save_state(state)
                post_message(token, args.channel, "送信しました。直接入力の本文をそのまま使いました。", args.thread)
                append_log({"event": "sent", "review_id": review_id, "candidate": 0, "mode": "direct_body"})
            else:
                post_message(token, args.channel, f"送信に失敗しました。\n```{detail}```", args.thread)
                append_log({"event": "send_failed", "review_id": review_id, "error": detail, "mode": "direct_body"})
            print(json.dumps({"handled": True, "action": "sent" if ok else "send_failed", "mode": "direct_body"}, ensure_ascii=False))
            return 0
        print(json.dumps({"handled": False, "reason": "not_review_command"}, ensure_ascii=False))
        return 0
    if idx not in {0, 1}:
        post_message(token, args.channel, "修正対象は `1` または `2` で指定してください。", args.thread)
        print(json.dumps({"handled": True, "action": "invalid_revise_target"}, ensure_ascii=False))
        return 0
    body, meta = revise_candidate(env, review, idx, instruction)
    candidates = review.setdefault("candidates", [])
    while len(candidates) <= idx:
        candidates.append({"label": f"案{idx+1}", "body": ""})
    candidates[idx] = {"label": f"修正版案{idx+1}", "body": body}
    review.setdefault("revisions", []).append({"at": int(time.time()), "candidate": idx + 1, "instruction": instruction, "meta": meta})
    save_state(state)
    post_message(token, args.channel, format_review_message(review), args.thread)
    print(json.dumps({"handled": True, "action": "revised", "candidate": idx + 1}, ensure_ascii=False))
    return 0


def main() -> int:
    ap = argparse.ArgumentParser()
    sub = ap.add_subparsers(dest="cmd", required=True)
    p = sub.add_parser("propose")
    p.add_argument("--account", default="")
    p.add_argument("--message-id", required=True)
    p.add_argument("--thread-id", required=True)
    p.add_argument("--subject", required=True)
    p.add_argument("--sender", required=True)
    p.add_argument("--body", default="")
    p.add_argument("--body-file", default="")
    p.add_argument("--channel", default="")
    h = sub.add_parser("handle-slack")
    h.add_argument("--channel", required=True)
    h.add_argument("--thread", required=True)
    h.add_argument("--user", default="")
    h.add_argument("--text", required=True)
    args = ap.parse_args()
    if args.cmd == "propose":
        return propose(args)
    if args.cmd == "handle-slack":
        return handle_slack(args)
    return 2


if __name__ == "__main__":
    raise SystemExit(main())
