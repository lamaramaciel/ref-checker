import streamlit as st
import pandas as pd
import requests
import re
import time
import io
from agent import verify_cell

st.set_page_config(
    page_title="Reference Checker",
    page_icon="🔍",
    layout="wide"
)

st.title("🔍 Reference Checker")
st.caption("Verifica se as informações da planilha batem com os links de referência.")

# --- Sidebar: config ---
with st.sidebar:
    st.header("⚙️ Configuração")
    api_key = st.text_input("Gemini API Key", type="password", help="Sua chave da API do Google Gemini")
    st.divider()
    st.markdown("**Como usar:**")
    st.markdown("1. Cole sua Gemini API Key")
    st.markdown("2. Faça upload da planilha `.csv`")
    st.markdown("3. Escolha quais linhas verificar")
    st.markdown("4. Clique em **Verificar**")
    st.markdown("5. Baixe o relatório")
    st.divider()
    st.caption("Detecta automaticamente colunas `[References]` e verifica cada par dado ↔ fonte.")

# --- Upload ---
uploaded = st.file_uploader("📂 Upload da planilha (.csv)", type=["csv"])

if not uploaded:
    st.info("Faça upload de um arquivo CSV para começar.")
    st.stop()

if not api_key:
    st.warning("Insira sua Gemini API Key na barra lateral.")
    st.stop()

# --- Leitura do CSV ---
try:
    df = pd.read_csv(uploaded)
except Exception as e:
    st.error(f"Erro ao ler o CSV: {e}")
    st.stop()

st.success(f"✅ Planilha carregada: **{len(df)} linhas** × **{len(df.columns)} colunas**")

# --- Detectar pares de colunas ---
ref_pairs = []
for col in df.columns:
    if "[References]" in col or "[Reference]" in col:
        data_col = col.replace(" [References]", "").replace(" [Reference]", "").strip()
        # tenta encontrar a coluna de dado correspondente (nome exato ou aproximado)
        if data_col in df.columns:
            ref_pairs.append((data_col, col))
        else:
            # fallback: procura coluna cujo nome está contido no nome da ref
            for c in df.columns:
                if c.strip() == data_col:
                    ref_pairs.append((c, col))
                    break

if not ref_pairs:
    st.error("Nenhuma coluna `[References]` encontrada. Verifique o formato da planilha.")
    st.stop()

st.write(f"**{len(ref_pairs)} pares** coluna ↔ referência detectados:")
cols_preview = st.columns(3)
for i, (data_col, ref_col) in enumerate(ref_pairs):
    cols_preview[i % 3].markdown(f"- `{data_col}`")

st.divider()

# --- Seleção de linhas ---
st.subheader("Selecione as linhas para verificar")

col1, col2 = st.columns(2)
with col1:
    start_row = st.number_input("Linha inicial", min_value=1, max_value=len(df), value=1)
with col2:
    end_row = st.number_input("Linha final", min_value=1, max_value=len(df), value=min(3, len(df)))

selected_df = df.iloc[int(start_row)-1 : int(end_row)]
st.caption(f"{len(selected_df)} linha(s) selecionada(s) · {len(ref_pairs)} colunas a verificar = **{len(selected_df) * len(ref_pairs)} verificações**")

# --- Preview da seleção ---
with st.expander("👁️ Preview das linhas selecionadas"):
    st.dataframe(selected_df, use_container_width=True)

st.divider()

# --- Botão verificar ---
if st.button("🚀 Verificar referências", type="primary", use_container_width=True):

    results = []
    total = len(selected_df) * len(ref_pairs)
    progress = st.progress(0, text="Iniciando verificação...")
    status_box = st.empty()
    count = 0

    for row_idx, row in selected_df.iterrows():
        # tenta pegar o nome da organização para exibição
        org_name = str(row.get("Name", row_idx + 1))

        for data_col, ref_col in ref_pairs:
            count += 1
            declared_value = str(row.get(data_col, "")).strip()
            references_raw = str(row.get(ref_col, "")).strip()

            # limpa o sufixo [Ref] do valor declarado
            declared_clean = re.sub(r'\s*\[Ref\d*\]', '', declared_value).strip()

            progress.progress(count / total, text=f"Verificando {count}/{total}: **{org_name}** · `{data_col}`")
            status_box.info(f"🔎 `{org_name}` — `{data_col}`: `{declared_clean[:80]}`")

            # pula células vazias ou sem referência
            if not declared_clean or declared_clean in ("nan", "N/A", "/", ""):
                results.append({
                    "Organização": org_name,
                    "Coluna": data_col,
                    "Valor declarado": declared_clean,
                    "Referência": references_raw[:100],
                    "Status": "⏭️ Pulado",
                    "Veredicto": "Valor vazio ou N/A",
                    "Trecho da fonte": ""
                })
                continue

            if not references_raw or references_raw in ("nan", "N/A", "/", ""):
                results.append({
                    "Organização": org_name,
                    "Coluna": data_col,
                    "Valor declarado": declared_clean,
                    "Referência": "",
                    "Status": "❓ Sem referência",
                    "Veredicto": "Nenhuma referência fornecida",
                    "Trecho da fonte": ""
                })
                continue

            # verifica via Jina + Gemini
            result = verify_cell(
                declared_value=declared_clean,
                column_name=data_col,
                references_raw=references_raw,
                gemini_api_key=api_key
            )

            results.append({
                "Organização": org_name,
                "Coluna": data_col,
                "Valor declarado": declared_clean,
                "Referência": references_raw[:120],
                "Status": result["status"],
                "Veredicto": result["verdict"],
                "Trecho da fonte": result["excerpt"]
            })

            time.sleep(0.3)  # evita rate limit

    progress.progress(1.0, text="✅ Verificação concluída!")
    status_box.empty()

    # --- Resultados ---
    st.divider()
    st.subheader("📊 Resultados")

    results_df = pd.DataFrame(results)

    # métricas
    m1, m2, m3, m4, m5 = st.columns(5)
    m1.metric("Total", len(results_df))
    m2.metric("✅ Confirmado", len(results_df[results_df["Status"] == "✅ Confirmado"]))
    m3.metric("❌ Incorreto", len(results_df[results_df["Status"] == "❌ Incorreto"]))
    m4.metric("⚠️ Parcial", len(results_df[results_df["Status"] == "⚠️ Parcial"]))
    m5.metric("🔒 Inacessível", len(results_df[results_df["Status"].isin(["🔒 Inacessível", "❓ Não encontrado", "❓ Sem referência"])]))

    # filtro
    filter_status = st.multiselect(
        "Filtrar por status",
        options=results_df["Status"].unique().tolist(),
        default=results_df["Status"].unique().tolist()
    )
    filtered = results_df[results_df["Status"].isin(filter_status)]
    st.dataframe(filtered, use_container_width=True, height=400)

    # export
    csv_out = results_df.to_csv(index=False).encode("utf-8")
    st.download_button(
        label="⬇️ Baixar relatório completo (.csv)",
        data=csv_out,
        file_name="relatorio_referencias.csv",
        mime="text/csv",
        use_container_width=True
    )
