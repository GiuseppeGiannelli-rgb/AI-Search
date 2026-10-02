"""Ricerca sul web con Tavily e preparazione del contesto per l'LLM."""

import tiktoken
from tavily import AsyncTavilyClient
from tavily.errors import (
    InvalidAPIKeyError,
    MissingAPIKeyError,
    TimeoutError as TavilyTimeoutError,
    UsageLimitExceededError,
)

# Numero massimo di token di contesto passati all'LLM per ogni giro di ricerca
# (i modelli piccoli come llama3.2 lavorano meglio con contesti brevi)
MAX_TOKEN_CONTESTO = 3000

# Secondi massimi di attesa per una ricerca
TIMEOUT_RICERCA = 30


class ErroreRicerca(Exception):
    """Errore 'leggibile' da mostrare direttamente all'utente in chat."""


def controlla_chiave(api_key: str) -> None:
    """Solleva un errore chiaro se la chiave Tavily non è stata impostata."""
    if not api_key:
        raise ErroreRicerca(
            "Manca la chiave **TAVILY_API_KEY** nel file `.env`.\n\n"
            "Puoi ottenerne una gratuita su https://app.tavily.com, poi apri una nuova chat."
        )


async def cerca(query: str, api_key: str, max_results: int = 5, search_depth: str = "advanced") -> dict:
    """Esegue la ricerca con Tavily e restituisce il dizionario della risposta.

    La risposta contiene: query, answer, follow_up_questions, images, results.
    Ogni elemento di results ha: title, url, content, score, raw_content.
    """
    controlla_chiave(api_key)

    client = AsyncTavilyClient(api_key=api_key)
    try:
        return await client.search(
            query,
            search_depth=search_depth,
            max_results=max_results,
            include_answer=True,
            timeout=TIMEOUT_RICERCA,
        )
    except (InvalidAPIKeyError, MissingAPIKeyError):
        raise ErroreRicerca(
            "La chiave **TAVILY_API_KEY** non è valida. Controlla il file `.env` "
            "(la trovi su https://app.tavily.com) e apri una nuova chat."
        )
    except UsageLimitExceededError:
        raise ErroreRicerca("Hai esaurito il credito mensile di Tavily. Riprova più tardi.")
    except TavilyTimeoutError:
        raise ErroreRicerca(
            f"La ricerca ha impiegato più di {TIMEOUT_RICERCA} secondi. Riprova tra poco."
        )
    except Exception as e:
        # Qualsiasi altro problema (rete assente, servizio non disponibile, ...)
        raise ErroreRicerca(f"Errore durante la ricerca su Tavily: {e}")


# Tokenizer usato per stimare la lunghezza dei testi
_encoding = tiktoken.get_encoding("cl100k_base")


def conta_token(testo: str) -> int:
    """Stima il numero di token di un testo (encoding usato dai modelli OpenAI)."""
    return len(_encoding.encode(testo))


def costruisci_contesto(risultati: list[dict], max_token: int = MAX_TOKEN_CONTESTO) -> str:
    """Trasforma i risultati di Tavily in un testo numerato da passare all'LLM.

    I risultati sono numerati da 1: l'LLM userà questi numeri nelle citazioni [n].
    Si aggiungono i risultati uno alla volta finché non si supera il limite di token.
    """
    blocchi = []
    token_usati = 0
    for i, r in enumerate(risultati, start=1):
        blocco = (
            f"[{i}] Titolo: {r.get('title', '')}\n"
            f"URL: {r.get('url', '')}\n"
            f"Contenuto: {r.get('content', '')}\n"
        )
        token_blocco = conta_token(blocco)
        if blocchi and token_usati + token_blocco > max_token:
            break  # il primo risultato lo teniamo sempre
        blocchi.append(blocco)
        token_usati += token_blocco
    return "\n".join(blocchi)
