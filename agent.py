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


def fetch_page_text(url: str, timeout: int = 20) -> tuple[str, str]:
    """
    Fetch the text content of a URL via Jina Reader.
    Returns (text, error_message). If successful, error_message is empty.
    """
    try:
        jina_url = JINA_BASE + url
        headers = {
            "Accept": "text/plain",
            "X-No-Cache": "true",
            "X-Return-Format": "text"
        }
        resp = requests.get(jina_url, headers=headers, timeout=timeout)
        if resp.status_code == 200 and len(resp.text.strip()) > 50:
            return resp.text[:6000], ""
        elif resp.status_code == 200:
            return "", "Page loaded but content is empty or too short"
        else:
            return "", f"Jina returned HTTP {resp.status_code}"
    except requests.exceptions.Timeout:
        return "", "Jina timeout — page took too long to load"
    except requests.exceptions.ConnectionError:
        return "", "Jina connection error — could not reach page"
    except Exception as e:
        return "", f"Jina error: {str(e)[:60]}"


def call_gemini(prompt: str, api_key: str, retries: int = 2) -> tuple[str, str]:
    """
    Call Gemini API with retry logic.
    Returns (response_text, error_message).
    """
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.1,
            "maxOutputTokens": 400
        }
    }

    for attempt in range(retries):
        try:
            resp = requests.post(
                f"{GEMINI_URL}?key={api_key}",
                json=payload,
                timeout=30
            )

            if resp.status_code == 200:
                data = resp.json()
                text = data["candidates"][0]["content"]["parts"][0]["text"].strip()
                return text, ""

            elif resp.status_code == 429:
                wait = 5 * (attempt + 1)
                time.sleep(wait)
                continue

            else:
                # Try to get error detail from response body
                try:
                    err_detail = resp.json().get("error", {}).get("message", "")
                except Exception:
                    err_detail = resp.text[:100]
                return "", f"Gemini HTTP {resp.status_code}: {err_detail[:80]}"

        except requests.exceptions.Timeout:
            if attempt < retries - 1:
                time.sleep(2)
                continue
            return "", "Gemini timeout"
        except Exception as e:
            return "", f"Gemini error: {str(e)[:80]}"

    return "", "Gemini failed after retries"


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

    raw_text, error = call_gemini(prompt, api_key)

    if error:
        return {
            "status": "🔒 Inaccessible",
            "verdict": error,
            "excerpt": ""
        }

    # Clean possible markdown fences
    clean = re.sub(r"```json\s*|\s*```", "", raw_text).strip()

    # Extract JSON even if there's surrounding text
    match = re.search(r'\{.*\}', clean, re.DOTALL)
    if not match:
        return {
            "status": "🔒 Inaccessible",
            "verdict": f"Could not parse Gemini response: {clean[:60]}",
            "excerpt": ""
        }

    try:
        parsed = json.loads(match.group())
    except json.JSONDecodeError as e:
        return {
            "status": "🔒 Inaccessible",
            "verdict": f"JSON parse error: {str(e)[:60]}",
            "excerpt": ""
        }

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

    for url in urls[:3]:
        page_text, jina_error = fetch_page_text(url)

        if not page_text:
            result = {
                "status": "🔒 Inaccessible",
                "verdict": jina_error or "Could not access page content",
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

        if result["status"] == "✅ Confirmed":
            return result

        if best_result is None or priority.get(result["status"], 0) > priority.get(best_result["status"], 0):
            best_result = result

        time.sleep(0.3)

    return best_result or {
        "status": "🔒 Inaccessible",
        "verdict": "All URLs failed",
        "excerpt": ""
    }
