"""Chiamate all'LLM: query di ricerca, riassunto di ogni giro e risposta finale in streaming.

Usiamo l'SDK di OpenAI sia con OpenAI sia con Ollama, perché Ollama espone
un'API compatibile su http://localhost:11434/v1.
"""

from collections.abc import AsyncIterator
from datetime import date

from openai import AsyncOpenAI

from config import Config

PROMPT_PRIMA_QUERY = """Sei un esperto di motori di ricerca.
Trasforma la domanda dell'utente in una query di ricerca web breve ed efficace:
- solo parole chiave (massimo 10 parole), senza punteggiatura inutile;
- tieni conto della conversazione precedente se la domanda vi fa riferimento;
- se la domanda riguarda novità, notizie o eventi recenti, aggiungi l'anno corrente ({anno});
- rispondi SOLO con la query, senza spiegazioni e senza virgolette.
Oggi è il {oggi}."""

# Aspetto da approfondire in ciascun giro dopo il primo (il ciclo ricomincia se i giri sono di più)
ASPETTI_DA_APPROFONDIRE = [
    ("dati, numeri e conseguenze concrete", "dati conseguenze"),
    ("analisi e opinioni di esperti", "analisi esperti"),
    ("ultime notizie e sviluppi più recenti", "ultime notizie"),
]

PROMPT_QUERY_SUCCESSIVA = """Devi scrivere UNA query per un motore di ricerca web. NON rispondere alla domanda.

Domanda da approfondire: {domanda}
Query già usate: {query_usate}
Aspetto da cercare ora: {aspetto}

Scrivi una nuova query (massimo 10 parole chiave) sullo stesso argomento della domanda,
mirata all'aspetto indicato e diversa dalle query già usate.
Rispondi SOLO con la query, senza virgolette e senza altre parole.
Oggi è il {oggi}."""

PROMPT_RIASSUNTO = """Sei un analista. Leggi i risultati di ricerca e scrivi un riassunto in italiano
di 4-6 frasi con le informazioni utili per rispondere alla domanda.

Regole:
- Usa solo le informazioni dei risultati: sono aggiornati e più recenti delle tue conoscenze.
- Ogni frase deve terminare con il numero della fonte tra parentesi quadre, ad esempio:
  "Il prezzo è salito del 30% [4]."
- Scrivi direttamente il riassunto, senza introduzioni come "Ecco il riassunto".
- Se nessun risultato è pertinente, scrivi solo: Nessuna informazione utile.

Domanda: {domanda}

Risultati:
{contesto}"""

# Testo che il riassunto restituisce quando i risultati non servono
NESSUNA_INFORMAZIONE = "Nessuna informazione utile"

PROMPT_RISPOSTA = """Sei Info Segugio, un assistente che risponde a domande di attualità
basandosi SOLO sugli appunti raccolti durante una ricerca sul web in più giri.

Regole:
- Gli appunti provengono dal web e sono aggiornati a oggi: sono più recenti delle tue conoscenze.
  Considera veri i fatti che riportano, anche se non li conosci, e non contraddirli.
- Rispondi sempre in italiano, in modo chiaro, ordinato e professionale (puoi usare elenchi puntati).
- Usa solo le informazioni degli appunti; se su un punto non bastano, dillo.
- Dopo ogni informazione riporta il numero della fonte tra parentesi quadre, con gli stessi
  numeri usati negli appunti. Esempio: "Dopo il voto il prezzo è salito del 30% [4]."
- Non scrivere l'elenco delle fonti alla fine: lo aggiunge il programma.
- Non usare emoji.

Oggi è il {oggi}."""


def crea_client(config: Config) -> AsyncOpenAI:
    """Crea il client asincrono per il provider scelto nel .env."""
    if config.provider == "openai":
        return AsyncOpenAI(api_key=config.openai_api_key)
    # Ollama non richiede una chiave vera, ma l'SDK ne vuole comunque una
    return AsyncOpenAI(base_url=config.base_url, api_key="ollama", max_retries=1)


def _oggi() -> str:
    return date.today().strftime("%d/%m/%Y")


async def _completa(client: AsyncOpenAI, modello: str, messaggi: list[dict], temperature: float = 0) -> str:
    """Chiamata semplice (senza streaming) che restituisce solo il testo."""
    risposta = await client.chat.completions.create(model=modello, messages=messaggi, temperature=temperature)
    return (risposta.choices[0].message.content or "").strip()


def _pulisci_query(query: str, riserva: str) -> str:
    """Toglie virgolette, righe extra e trattini bassi; se la query è strana usa la riserva."""
    query = query.splitlines()[0].strip().strip('"').strip() if query else ""
    # Alcuni modelli scrivono le parole unite da "_" (es. elezione_trump_2024): le separiamo
    query = " ".join(query.replace("_", " ").split())
    return query if 0 < len(query) < 200 else riserva


async def prima_query(client: AsyncOpenAI, modello: str, domanda: str, cronologia: list[dict]) -> str:
    """Giro 1: trasforma la domanda in una query di ricerca ottimizzata."""
    messaggi = [{"role": "system", "content": PROMPT_PRIMA_QUERY.format(oggi=_oggi(), anno=date.today().year)}]
    messaggi += cronologia[-4:]  # ultimi 2 scambi, per capire domande come "e in Europa?"
    messaggi.append({"role": "user", "content": domanda})
    return _pulisci_query(await _completa(client, modello, messaggi), riserva=domanda)


def _query_valida(query: str, query_usate: list[str]) -> bool:
    """Scarta le risposte che non sembrano query (frasi lunghe, scuse, ripetizioni)."""
    q = query.lower()
    return (
        0 < len(query.split()) <= 12
        and not q.startswith(("non ", "mi dispiace", "spiacente"))
        and q not in (u.lower() for u in query_usate)
    )


async def query_successiva(client: AsyncOpenAI, modello: str, domanda: str, query_usate: list[str], giro: int) -> str:
    """Giri 2, 3, 4...: sceglie una nuova query mirata a un aspetto diverso della domanda."""
    aspetto, parole_chiave = ASPETTI_DA_APPROFONDIRE[(giro - 2) % len(ASPETTI_DA_APPROFONDIRE)]
    prompt = PROMPT_QUERY_SUCCESSIVA.format(
        domanda=domanda, query_usate="; ".join(query_usate), aspetto=aspetto, oggi=_oggi()
    )
    query = _pulisci_query(await _completa(client, modello, [{"role": "user", "content": prompt}]), riserva="")
    if _query_valida(query, query_usate):
        return query
    # Il modello non ha scritto una query sensata: usiamo la prima query + l'aspetto del giro
    return f"{query_usate[0]} {parole_chiave}"


async def riassumi(client: AsyncOpenAI, modello: str, domanda: str, contesto: str) -> str:
    """Riassume i risultati di un singolo giro di ricerca."""
    prompt = PROMPT_RIASSUNTO.format(domanda=domanda, contesto=contesto)
    riassunto = await _completa(client, modello, [{"role": "user", "content": prompt}])
    return riassunto or NESSUNA_INFORMAZIONE


async def genera_risposta(
    client: AsyncOpenAI, modello: str, domanda: str, appunti: str, cronologia: list[dict]
) -> AsyncIterator[str]:
    """Genera la risposta finale un pezzo alla volta (streaming)."""
    messaggi = [{"role": "system", "content": PROMPT_RISPOSTA.format(oggi=_oggi())}]
    messaggi += cronologia
    # Gli appunti vanno subito prima della domanda: i modelli piccoli li seguono meglio
    messaggi.append({"role": "user", "content": f"Appunti della ricerca:\n\n{appunti}\n\nDomanda: {domanda}"})

    stream = await client.chat.completions.create(
        model=modello, messages=messaggi, temperature=0.3, stream=True
    )
    async for chunk in stream:
        if chunk.choices and chunk.choices[0].delta.content:
            yield chunk.choices[0].delta.content
