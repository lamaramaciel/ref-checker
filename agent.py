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
    """Return domain name if URL is from a known blocked domain, else empty string."""
    for domain in BLOCKED_DOMAINS:
        if domain in url.lower():
            return domain
    return ""


def fetch_page_text(url: str, timeout: int = 20) -> tuple[str, str]:
    """
    Fetch page text via Jina Reader.
    Returns (text, error_message). On success, error_message is empty.
    """
    # Skip known blocked domains immediately
    blocked = is_blocked_domain(url)
    if blocked:
        return "", f"Blocked domain ({blocked}) — requires login, cannot verify"

    try:
        jina_url = JINA_BASE + url
        headers = {
            "Accept": "text/plain",
            "X-No-Cache": "true",
            "X-Return-Format": "text"
        }
        resp = requests.get(jina_url, headers=headers, timeout=timeout)

        if resp.status_code == 200:
            text = resp.text.strip()
            # Detect if Jina returned a login/auth wall
            if len(text) < 100:
                return "", "Page too short — likely blocked or empty"
            if any(phrase in text.lower() for phrase in ["sign in", "log in", "login", "401 unauthorized", "access denied"]):
                return "", "Page requires authentication — cannot access content"
            return text[:6000], ""
        else:
            return "", f"HTTP {resp.status_code}"

    except requests.exceptions.Timeout:
        return "", "Timeout — page too slow"
    except requests.exceptions.ConnectionError:
        return "", "Connection error — could not reach page"
    except Exception as e:
        return "", f"Fetch error: {str(e)[:60]}"


def call_gemini(prompt: str, api_key: str, retries: int = 2) -> tuple[str, str]:
    """
    Call Gemini API with retry logic.
    Returns (response_text, error_message).
    """
    payload = {
        "contents": [{"parts": [{"text": prompt}]}],
        "generationConfig": {
            "temperature": 0.0,
            "maxOutputTokens": 2048,
            "responseMimeType": "application/json"
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


def judge_with_gemini(
    declared_value: str,
    column_name: str,
    page_text: str,
    url: str,
    api_key: str,
    instruction: str = ""
) -> dict:
    """Use Gemini to compare declared value with page content."""

    instruction_block = ""
    if instruction and instruction.strip():
        instruction_block = f"\nAdditional instruction for this column: {instruction.strip()}\n"

    # Truncate declared value if very long to keep prompt tight
    declared_short = declared_value[:500] if len(declared_value) > 500 else declared_value

    prompt = f"""You are a data auditor. Verify if the declared value is confirmed by the source content.

Column: {column_name}
Declared value: {declared_short}
Source URL: {url}
{instruction_block}
Source content:
---
{page_text}
---

Classify with one of: CONFIRMED, PARTIAL, INCORRECT, NOT_FOUND

Rules:
- CONFIRMED: declared value is clearly present and correct in the source
- PARTIAL: source mentions something related but does not exactly confirm the value
- INCORRECT: source contradicts the declared value
- NOT_FOUND: source loaded but specific information is absent

Return a JSON object with exactly these keys:
- status: one of CONFIRMED, PARTIAL, INCORRECT, NOT_FOUND
- verdict: string, max 100 characters
- excerpt: string, most relevant source excerpt max 150 characters, or empty string"""

    raw_text, error = call_gemini(prompt, api_key)

    if error:
        return {"status": "🔒 Inaccessible", "verdict": error, "excerpt": ""}

    # Try direct JSON parse first (responseMimeType forces valid JSON)
    try:
        parsed = json.loads(raw_text)
    except json.JSONDecodeError:
        # Fallback: extract JSON from text
        clean = re.sub(r"```json\s*|\s*```", "", raw_text).strip()
        match = re.search(r'\{.*?\}', clean, re.DOTALL)
        if not match:
            return {"status": "🔒 Inaccessible", "verdict": f"Could not parse: {clean[:80]}", "excerpt": ""}
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
        "status": status_map.get(str(parsed.get("status", "")).upper(), "❓ Not Found"),
        "verdict": str(parsed.get("verdict", ""))[:120],
        "excerpt": str(parsed.get("excerpt", ""))[:200]
    }


def verify_cell(
    declared_value: str,
    column_name: str,
    references_raw: str,
    gemini_api_key: str,
    instruction: str = ""
) -> dict:
    """Main: extract URLs, fetch via Jina, verify with Gemini."""
    urls = extract_urls(references_raw)

    if not urls:
        return {"status": "❓ No Reference", "verdict": "No valid URL found in reference cell", "excerpt": ""}

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

    return best_result or {"status": "🔒 Inaccessible", "verdict": "All URLs failed", "excerpt": ""}
