"""Chiamate all'LLM: riformulazione della domanda e risposta finale in streaming.

Usiamo l'SDK di OpenAI sia con OpenAI sia con Ollama, perché Ollama espone
un'API compatibile su http://localhost:11434/v1.
"""

from collections.abc import AsyncIterator
from datetime import date

from openai import AsyncOpenAI

from config import Config

PROMPT_RIFORMULA = """Sei un esperto di motori di ricerca.
Trasforma la domanda dell'utente in una query di ricerca web breve ed efficace:
- solo parole chiave (massimo 10 parole), senza punteggiatura inutile;
- tieni conto della conversazione precedente se la domanda vi fa riferimento;
- se utile, aggiungi l'anno o la data;
- rispondi SOLO con la query, senza spiegazioni e senza virgolette.
Oggi è il {oggi}."""

PROMPT_RISPOSTA = """Sei Info Segugio, un assistente che risponde a domande di attualità
usando SOLO i risultati di ricerca web forniti qui sotto.

Regole:
- Rispondi sempre in italiano, in modo chiaro e ordinato (puoi usare elenchi puntati).
- Usa solo le informazioni dei risultati; se non bastano, dillo onestamente.
- Quando usi un'informazione, indica tra parentesi quadre il numero della fonte, es. [1].
- Termina SEMPRE con una sezione intitolata "**Fonti**" con l'elenco puntato
  dei link usati in formato markdown: - [Titolo](URL)

Oggi è il {oggi}.

Risultati della ricerca:
{contesto}"""


def crea_client(config: Config) -> AsyncOpenAI:
    """Crea il client asincrono per il provider scelto nel .env."""
    if config.provider == "openai":
        return AsyncOpenAI(api_key=config.openai_api_key)
    # Ollama non richiede una chiave vera, ma l'SDK ne vuole comunque una
    return AsyncOpenAI(base_url=config.base_url, api_key="ollama")


def _oggi() -> str:
    return date.today().strftime("%d/%m/%Y")


async def riformula_query(client: AsyncOpenAI, modello: str, domanda: str, cronologia: list[dict]) -> str:
    """Chiede all'LLM di trasformare la domanda in una query di ricerca ottimizzata."""
    messaggi = [{"role": "system", "content": PROMPT_RIFORMULA.format(oggi=_oggi())}]
    messaggi += cronologia[-4:]  # ultimi 2 scambi, per capire domande come "e in Europa?"
    messaggi.append({"role": "user", "content": domanda})

    risposta = await client.chat.completions.create(model=modello, messages=messaggi, temperature=0)
    query = (risposta.choices[0].message.content or "").strip().strip('"').strip()
    # Se il modello risponde in modo strano, usiamo la domanda originale
    return query if 0 < len(query) < 200 else domanda


async def genera_risposta(
    client: AsyncOpenAI, modello: str, domanda: str, contesto: str, cronologia: list[dict]
) -> AsyncIterator[str]:
    """Genera la risposta finale un pezzo alla volta (streaming)."""
    messaggi = [{"role": "system", "content": PROMPT_RISPOSTA.format(oggi=_oggi(), contesto=contesto)}]
    messaggi += cronologia
    messaggi.append({"role": "user", "content": domanda})

    stream = await client.chat.completions.create(
        model=modello, messages=messaggi, temperature=0.3, stream=True
    )
    async for chunk in stream:
        if chunk.choices and chunk.choices[0].delta.content:
            yield chunk.choices[0].delta.content
