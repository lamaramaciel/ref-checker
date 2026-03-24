# 🔍 Reference Checker

Ferramenta para verificar automaticamente se as informações de uma planilha batem com os links de referência fornecidos.

## Como funciona

1. Você faz upload de um `.csv` com colunas no formato `Nome da Coluna` + `Nome da Coluna [References]`
2. O app detecta automaticamente todos os pares
3. Para cada célula, o Jina Reader acessa o link e extrai o texto da página
4. O Gemini 2.0 Flash compara o valor declarado com o conteúdo da fonte
5. Você recebe um relatório com ✅ Confirmado / ❌ Incorreto / ⚠️ Parcial / 🔒 Inacessível

---

## 🚀 Passo a passo: do zero ao link compartilhável

### 1. Pré-requisitos
- Conta no [GitHub](https://github.com) (gratuito)
- Conta no [Streamlit Cloud](https://streamlit.io/cloud) (gratuito, entre com o GitHub)
- [Python 3.10+](https://python.org) instalado no seu PC
- [Git](https://git-scm.com) instalado no seu PC
- Sua **Gemini API Key** (pegue em [aistudio.google.com/app/apikey](https://aistudio.google.com/app/apikey))

---

### 2. Criar o repositório no GitHub

1. Acesse [github.com](https://github.com) e clique em **New repository**
2. Nome sugerido: `ref-checker`
3. Deixe como **Public** (necessário para o Streamlit Cloud gratuito)
4. Clique em **Create repository**

---

### 3. Subir os arquivos

No seu terminal (ou Git Bash no Windows):

```bash
# Clone o repositório vazio
git clone https://github.com/SEU_USUARIO/ref-checker.git
cd ref-checker

# Copie os arquivos do projeto para esta pasta:
# app.py, agent.py, requirements.txt

# Suba os arquivos
git add .
git commit -m "primeiro commit"
git push origin main
```

---

### 4. Deploy no Streamlit Cloud

1. Acesse [share.streamlit.io](https://share.streamlit.io)
2. Clique em **New app**
3. Selecione:
   - **Repository:** `SEU_USUARIO/ref-checker`
   - **Branch:** `main`
   - **Main file path:** `app.py`
4. Clique em **Deploy!**

Aguarde ~2 minutos. O Streamlit vai instalar as dependências e subir o app.

---

### 5. Configurar a Gemini API Key (seguro, sem expor no código)

1. No painel do Streamlit Cloud, clique no seu app
2. Vá em **Settings → Secrets**
3. Cole o seguinte (substituindo pela sua chave real):

```toml
GEMINI_API_KEY = "sua_chave_aqui"
```

> ⚠️ **Nunca coloque sua API Key diretamente no código ou no GitHub!**

4. Clique em **Save**
5. O app vai reiniciar automaticamente

Depois disso, os colegas podem usar sem precisar inserir a chave — ela já estará configurada.

---

### 6. Compartilhar

Seu app estará disponível em uma URL como:

```
https://seu-usuario-ref-checker-app-xxxxxxxx.streamlit.app
```

Copie e envie para qualquer colega. Eles abrem no navegador, fazem upload do CSV e usam — sem instalar nada.

---

## 📁 Estrutura do projeto

```
ref-checker/
├── app.py            # Interface Streamlit
├── agent.py          # Lógica: Jina Reader + Gemini
├── requirements.txt  # Dependências Python
└── README.md         # Este arquivo
```

---

## 💡 Dicas de uso

- **Linha de instrução:** se sua planilha tiver uma linha abaixo do cabeçalho com instruções por coluna, o app detecta e usa como guia para o Gemini
- **Múltiplos links:** células com vários links separados por `|` são testadas em sequência — o app para no primeiro que confirmar
- **Rate limit:** o app tem um delay entre requisições para não estourar o free tier do Gemini
- **LinkedIn:** links do LinkedIn frequentemente bloqueiam acesso externo — o app vai marcar como 🔒 Inacessível nesses casos

---

## 🔧 Rodar localmente (opcional)

```bash
pip install -r requirements.txt
streamlit run app.py
```

Acesse `http://localhost:8501` no navegador.
