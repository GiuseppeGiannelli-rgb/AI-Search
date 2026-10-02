# 🐕 Info Segugio

Selfwork **"AI Search"** del corso Aulab *Agentic AI & Python*.

Una chat web in cui fai una domanda su un argomento di attualità: l'app cerca sul web con
[Tavily](https://tavily.com) e un LLM (Ollama in locale oppure OpenAI) risponde **in italiano**,
riassumendo i risultati e citando le **fonti** con link cliccabili.

## Come funziona

1. Scrivi una domanda nella chat (Chainlit).
2. L'LLM la riformula in una **query di ricerca** breve, fatta di parole chiave.
3. Tavily esegue la ricerca (`search_depth="advanced"`, `max_results=5`, `include_answer=True`).
4. Con titolo, contenuto e URL dei risultati si costruisce il **contesto** (limitato a ~6000 token con `tiktoken`).
5. L'LLM genera la risposta **in streaming**, chiudendo con la sezione **Fonti**.
6. Se qualcosa va storto (nessun risultato, chiave Tavily mancante o non valida, timeout,
   Ollama spento, modello non scaricato…) compare un messaggio chiaro in chat.

Nel riquadro **"Ricerca sul web"** sopra ogni risposta puoi vedere la query usata e i link trovati.

## Struttura

```
info-segugio/
├── pyproject.toml, poetry.lock, poetry.toml
├── .env.example          # modello del file .env (da copiare)
├── chainlit.md           # pagina "Leggimi" della chat
├── .chainlit/config.toml # nome dell'app e lingua dell'interfaccia
├── public/               # logo e favicon
└── info_segugio/
    ├── __init__.py       # app Chainlit (on_chat_start, on_message)
    ├── config.py         # lettura del .env e scelta del provider
    ├── search.py         # ricerca Tavily + costruzione del contesto
    └── llm.py            # riformulazione della query + risposta in streaming
```

## Requisiti

- Python **3.13** (Chainlit non supporta ancora la 3.14)
- [Poetry](https://python-poetry.org/) 2.x
- Una chiave **Tavily** gratuita: https://app.tavily.com
- [Ollama](https://ollama.com) con il modello `llama3.2` (`ollama pull llama3.2`) **oppure** una chiave OpenAI

## Installazione

```bash
git clone <url-del-repository> info-segugio
cd info-segugio
poetry config virtualenvs.in-project true
poetry install
```

L'ambiente virtuale viene creato nella cartella `.venv` del progetto
(utile in VS Code: *Python: Select Interpreter* → `.venv`).

## Configurazione delle chiavi

Copia il file di esempio e compilalo:

```bash
cp .env.example .env
```

| Variabile | Descrizione |
|---|---|
| `TAVILY_API_KEY` | chiave della Tavily Search API (obbligatoria) |
| `PROVIDER` | `ollama` (locale, default) oppure `openai`. Se vuoto: `openai` quando c'è `OPENAI_API_KEY`, altrimenti `ollama` |
| `OPENAI_API_KEY` | chiave OpenAI (solo con `PROVIDER=openai`) |
| `OLLAMA_LLM_MODEL` | modello Ollama, default `llama3.2` |
| `OPENAI_LLM_MODEL` | modello OpenAI, default `gpt-4o-mini` |
| `TAVILY_MAX_RESULTS` | numero di risultati della ricerca, default `5` |
| `TAVILY_SEARCH_DEPTH` | `basic` oppure `advanced` (default) |

Il file `.env` è nel `.gitignore`: **non va mai committato**. Nel codice non c'è nessuna chiave.

Per passare da Ollama a OpenAI basta cambiare `PROVIDER` nel `.env` e aprire una **nuova chat**
(il `.env` viene riletto a ogni nuova conversazione).

## Avvio

Se usi Ollama, assicurati che sia in esecuzione (`ollama serve` oppure l'app di Ollama), poi:

```bash
poetry run chainlit run info_segugio/__init__.py -w --port 8002
```

e apri http://localhost:8002.

## Esempi di domande

- Che impatto ha avuto l'elezione di Trump 2024 su Bitcoin?
- Quali sono le ultime novità sui modelli di intelligenza artificiale?
- Come sta andando la missione Artemis della NASA?
- Come funziona la fusione nucleare e a che punto è la ricerca?

Puoi anche fare domande di approfondimento ("e in Europa?"): la query di ricerca tiene conto
degli ultimi messaggi della conversazione.
