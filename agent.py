import re
import requests
import time

JINA_BASE = "https://r.jina.ai/"
GEMINI_URL = "https://generativelanguage.googleapis.com/v1beta/models/gemini-2.0-flash:generateContent"


def extract_urls(raw: str) -> list[str]:
    """Extrai todas as URLs de uma célula (separadas por |, vírgula ou espaço)."""
    pattern = r'https?://[^\s|,\]>)"\']+'
    urls = re.findall(pattern, raw)
    return [u.rstrip(".,;)") for u in urls]


def fetch_page_text(url: str, timeout: int = 15) -> str:
    """Busca o conteúdo textual de uma URL via Jina Reader."""
    try:
        jina_url = JINA_BASE + url
        headers = {
            "Accept": "text/plain",
            "X-No-Cache": "true"
        }
        resp = requests.get(jina_url, headers=headers, timeout=timeout)
        if resp.status_code == 200:
            # limita o texto a 6000 chars para não explodir o contexto do Gemini
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
    Usa o Gemini para comparar o valor declarado com o conteúdo da página.
    Retorna dict com status, verdict e excerpt.
    """
    instruction_block = ""
    if instruction and instruction.strip():
        instruction_block = f"\nInstrução adicional para esta coluna: {instruction.strip()}\n"

    prompt = f"""Você é um auditor de dados. Sua tarefa é verificar se a informação declarada é confirmada pela fonte fornecida.

Coluna analisada: {column_name}
Valor declarado: {declared_value}
URL da fonte: {url}
{instruction_block}
Conteúdo extraído da fonte (primeiros 6000 caracteres):
---
{page_text}
---

Com base no conteúdo acima, classifique com EXATAMENTE um destes status:
- CONFIRMADO: o valor declarado está claramente presente e correto na fonte
- PARCIAL: a fonte menciona algo relacionado mas não confirma exatamente o valor
- INCORRETO: a fonte contradiz o valor declarado
- NAO_ENCONTRADO: a fonte carregou mas não contém a informação específica

Responda SOMENTE neste formato JSON (sem markdown, sem explicações fora do JSON):
{{
  "status": "CONFIRMADO|PARCIAL|INCORRETO|NAO_ENCONTRADO",
  "verdict": "explicação curta em português (máx 120 caracteres)",
  "excerpt": "trecho relevante da fonte que embasou a decisão (máx 200 caracteres, em branco se não encontrado)"
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
                "status": "🔒 Inacessível",
                "verdict": f"Erro na API Gemini: {resp.status_code}",
                "excerpt": ""
            }

        data = resp.json()
        text = data["candidates"][0]["content"]["parts"][0]["text"].strip()

        # remove possíveis blocos markdown ```json ... ```
        text = re.sub(r"```json\s*|\s*```", "", text).strip()

        import json
        parsed = json.loads(text)

        status_map = {
            "CONFIRMADO": "✅ Confirmado",
            "PARCIAL": "⚠️ Parcial",
            "INCORRETO": "❌ Incorreto",
            "NAO_ENCONTRADO": "❓ Não encontrado"
        }

        return {
            "status": status_map.get(parsed.get("status", ""), "❓ Não encontrado"),
            "verdict": parsed.get("verdict", ""),
            "excerpt": parsed.get("excerpt", "")
        }

    except Exception as e:
        return {
            "status": "🔒 Inacessível",
            "verdict": f"Erro ao processar resposta: {str(e)[:80]}",
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
    Função principal: extrai URLs, busca conteúdo via Jina,
    e pede ao Gemini para verificar o valor declarado.
    Tenta cada URL até encontrar uma que confirme ou esgote as opções.
    """
    urls = extract_urls(references_raw)

    if not urls:
        return {
            "status": "❓ Sem referência",
            "verdict": "Nenhuma URL válida encontrada na célula de referência",
            "excerpt": ""
        }

    best_result = None

    for url in urls[:3]:  # tenta no máximo 3 URLs por célula
        page_text = fetch_page_text(url)

        if not page_text:
            result = {
                "status": "🔒 Inacessível",
                "verdict": "Não foi possível acessar o conteúdo da página",
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

        # se confirmou, retorna imediatamente
        if result["status"] == "✅ Confirmado":
            return result

        # guarda o melhor resultado parcial
        priority = {
            "⚠️ Parcial": 4,
            "❌ Incorreto": 3,
            "❓ Não encontrado": 2,
            "🔒 Inacessível": 1,
            "❓ Sem referência": 0
        }
        if best_result is None or priority.get(result["status"], 0) > priority.get(best_result["status"], 0):
            best_result = result

        time.sleep(0.2)  # pequeno delay entre URLs

    return best_result or {
        "status": "🔒 Inacessível",
        "verdict": "Todas as URLs falharam",
        "excerpt": ""
    }
