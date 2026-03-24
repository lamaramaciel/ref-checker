import re
import requests
import time
import json

JINA_BASE = "https://r.jina.ai/"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.5-flash:generateContent"

BLOCKED_DOMAINS = ["linkedin.com", "facebook.com", "instagram.com", "twitter.com", "x.com"]


def extract_urls(raw: str) -> list[str]:
    """Extract all URLs from a cell (separated by |, comma, or space)."""
    pattern = r'https?://[^\s|,\]>)"\']+'
    urls = re.findall(pattern, raw)
    return [u.rstrip(".,;)") for u in urls]


def is_blocked_domain(url: str) -> str:
    """Check if URL is from a known blocked domain. Returns domain name or empty string."""
    for domain in BLOCKED_DOMAINS:
        if domain in url:
            return domain
    return ""


def fetch_page_text(url: str, timeout: int = 20) -> tuple[str, str]:
    """Fetch the text content of a URL via Jina Reader. Returns (text, error_message)."""
    blocked = is_blocked_domain(url)
    if blocked:
        return "", f"Blocked domain ({blocked}) — requires login"

    try:
        jina_url = JINA_BASE + url
        headers = {"Accept": "text/plain", "X-No-Cache": "true", "X-Return-Format": "text"}
        resp = requests.get(jina_url, headers=headers, timeout=timeout)
        if resp.status_code == 200 and len(resp.text.strip()) > 50:
            return resp.text[:6000], ""
        elif resp.status_code == 200:
            return "", "Page loaded but content is empty"
        else:
            return "", f"HTTP {resp.status_code}"
    except requests.exceptions.Timeout:
        return "", "Timeout — page too slow"
    except requests.exceptions.ConnectionError:
        return "", "Connection error"
    except Exception as e:
        return "", f"Fetch error: {str(e)[:60]}"


def call_gemini(prompt: str, api_key: str, retries: int = 2) -> tuple[str, str]:
    """Call Gemini API with retry. Returns (response_text, error_message)."""
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {"temperature": 0.1, "maxOutputTokens": 2048}
    }

    for attempt in range(retries):
        try:
            resp = requests.post(f"{GEMINI_URL}?key={api_key}", json=payload, timeout=30)
            if resp.status_code == 200:
                data = resp.json()
                return data["candidates"][0]["content"]["parts"][0]["text"].strip(), ""
            elif resp.status_code == 429:
                time.sleep(5 * (attempt + 1))
                continue
            else:
                try:
                    err = resp.json().get("error", {}).get("message", "")
                except Exception:
                    err = resp.text[:100]
                return "", f"Gemini HTTP {resp.status_code}: {err[:80]}"
        except requests.exceptions.Timeout:
            if attempt < retries - 1:
                time.sleep(2)
                continue
            return "", "Gemini timeout"
        except Exception as e:
            return "", f"Gemini error: {str(e)[:80]}"

    return "", "Gemini failed after retries"


def judge_with_gemini(declared_value, column_name, page_text, url, api_key, instruction=""):
    """Use Gemini to compare declared value with page content."""
    instruction_block = f"\nAdditional instruction: {instruction.strip()}\n" if instruction and instruction.strip() else ""

    prompt = f"""You are a data auditor. Verify whether the declared information is confirmed by the source.

Column: {column_name}
Declared value: {declared_value}
Source URL: {url}
{instruction_block}
Source content (first 6000 chars):
---
{page_text}
---

Classify with EXACTLY one status:
- CONFIRMED: value is clearly present and correct in the source
- PARTIAL: source mentions something related but not exactly the declared value
- INCORRECT: source contradicts the declared value
- NOT_FOUND: source loaded but specific information is absent

Respond ONLY with a single-line JSON object (no markdown, no extra text):
{{"status": "CONFIRMED|PARTIAL|INCORRECT|NOT_FOUND", "verdict": "explanation max 100 chars", "excerpt": "source excerpt max 150 chars or empty"}}"""

    raw_text, error = call_gemini(prompt, api_key)
    if error:
        return {"status": "🔒 Inaccessible", "verdict": error, "excerpt": ""}

    clean = re.sub(r"```json\s*|\s*```", "", raw_text).strip()
    match = re.search(r'\{.*\}', clean, re.DOTALL)
    if not match:
        return {"status": "🔒 Inaccessible", "verdict": f"Could not parse: {clean[:60]}", "excerpt": ""}

    try:
        parsed = json.loads(match.group())
    except json.JSONDecodeError as e:
        return {"status": "🔒 Inaccessible", "verdict": f"JSON error: {str(e)[:60]}", "excerpt": ""}

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


def verify_cell(declared_value, column_name, references_raw, gemini_api_key, instruction=""):
    """Main: extract URLs, fetch via Jina, verify with Gemini."""
    urls = extract_urls(references_raw)
    if not urls:
        return {"status": "❓ No Reference", "verdict": "No valid URL found", "excerpt": ""}

    best_result = None
    priority = {"⚠️ Partial": 4, "❌ Incorrect": 3, "❓ Not Found": 2, "🔒 Inaccessible": 1, "❓ No Reference": 0}

    for url in urls[:3]:
        page_text, jina_error = fetch_page_text(url)
        if not page_text:
            result = {"status": "🔒 Inaccessible", "verdict": jina_error or "Could not access page", "excerpt": ""}
        else:
            result = judge_with_gemini(declared_value, column_name, page_text, url, gemini_api_key, instruction)

        if result["status"] == "✅ Confirmed":
            return result

        if best_result is None or priority.get(result["status"], 0) > priority.get(best_result["status"], 0):
            best_result = result

        time.sleep(0.3)

    return best_result or {"status": "🔒 Inaccessible", "verdict": "All URLs failed", "excerpt": ""}
