import re
import requests
import time
import json

JINA_BASE = "https://r.jina.ai/"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash-001:generateContent"


def extract_urls(raw: str) -> list[str]:
    """Extract all URLs from a cell (separated by |, comma, or space)."""
    pattern = r'https?://[^\s|,\]>)"\']+'
    urls = re.findall(pattern, raw)
    return [u.rstrip(".,;)") for u in urls]


def fetch_page_text(url: str, timeout: int = 15) -> str:
    """Fetch the text content of a URL via Jina Reader."""
    try:
        jina_url = JINA_BASE + url
        headers = {
            "Accept": "text/plain",
            "X-No-Cache": "true"
        }
        resp = requests.get(jina_url, headers=headers, timeout=timeout)
        if resp.status_code == 200:
            return resp.text[:6000]
        return ""
    except Exception:
        return ""


def judge_with_gemini(
    declared_value: str,
    column_name: str,
    page_text: str,
    url: str,
    api_key: str,
    instruction: str = ""
) -> dict:
    """
    Use Gemini to compare the declared value with the page content.
    Returns a dict with status, verdict and excerpt.
    """
    instruction_block = ""
    if instruction and instruction.strip():
        instruction_block = f"\nAdditional instruction for this column: {instruction.strip()}\n"

    prompt = f"""You are a data auditor. Your task is to verify whether the declared information is confirmed by the provided source.

Column being analyzed: {column_name}
Declared value: {declared_value}
Source URL: {url}
{instruction_block}
Content extracted from the source (first 6000 characters):
---
{page_text}
---

Based on the content above, classify using EXACTLY one of these statuses:
- CONFIRMED: the declared value is clearly present and correct in the source
- PARTIAL: the source mentions something related but does not exactly confirm the value
- INCORRECT: the source contradicts the declared value
- NOT_FOUND: the source loaded but does not contain the specific information

Respond ONLY in this JSON format (no markdown, no explanations outside the JSON):
{{
  "status": "CONFIRMED|PARTIAL|INCORRECT|NOT_FOUND",
  "verdict": "short explanation in English (max 120 characters)",
  "excerpt": "relevant excerpt from the source that supported the decision (max 200 characters, empty if not found)"
}}"""

    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.1,
            "maxOutputTokens": 300
        }
    }

    try:
        resp = requests.post(
            f"{GEMINI_URL}?key={api_key}",
            json=payload,
            timeout=30
        )
        if resp.status_code != 200:
            return {
                "status": "🔒 Inaccessible",
                "verdict": f"Gemini API error: {resp.status_code}",
                "excerpt": ""
            }

        data = resp.json()
        text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
        text = re.sub(r"```json\s*|\s*```", "", text).strip()

        parsed = json.loads(text)

        status_map = {
            "CONFIRMED": "✅ Confirmed",
            "PARTIAL": "⚠️ Partial",
            "INCORRECT": "❌ Incorrect",
            "NOT_FOUND": "❓ Not Found"
        }

        return {
            "status": status_map.get(parsed.get("status", ""), "❓ Not Found"),
            "verdict": parsed.get("verdict", ""),
            "excerpt": parsed.get("excerpt", "")
        }

    except Exception as e:
        return {
            "status": "🔒 Inaccessible",
            "verdict": f"Error processing response: {str(e)[:80]}",
            "excerpt": ""
        }


def verify_cell(
    declared_value: str,
    column_name: str,
    references_raw: str,
    gemini_api_key: str,
    instruction: str = ""
) -> dict:
    """
    Main function: extract URLs, fetch content via Jina Reader,
    and ask Gemini to verify the declared value against the source.
    Tries each URL until one confirms or all are exhausted.
    """
    urls = extract_urls(references_raw)

    if not urls:
        return {
            "status": "❓ No Reference",
            "verdict": "No valid URL found in the reference cell",
            "excerpt": ""
        }

    best_result = None

    priority = {
        "⚠️ Partial": 4,
        "❌ Incorrect": 3,
        "❓ Not Found": 2,
        "🔒 Inaccessible": 1,
        "❓ No Reference": 0
    }

    for url in urls[:3]:  # try at most 3 URLs per cell
        page_text = fetch_page_text(url)

        if not page_text:
            result = {
                "status": "🔒 Inaccessible",
                "verdict": "Could not access page content",
                "excerpt": ""
            }
        else:
            result = judge_with_gemini(
                declared_value=declared_value,
                column_name=column_name,
                page_text=page_text,
                url=url,
                api_key=gemini_api_key,
                instruction=instruction
            )

        # return immediately if confirmed
        if result["status"] == "✅ Confirmed":
            return result

        if best_result is None or priority.get(result["status"], 0) > priority.get(best_result["status"], 0):
            best_result = result

        time.sleep(0.2)

    return best_result or {
        "status": "🔒 Inaccessible",
        "verdict": "All URLs failed",
        "excerpt": ""
    }
