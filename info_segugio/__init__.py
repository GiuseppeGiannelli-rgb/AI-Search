"""Info Segugio: chat Chainlit che indaga sul web con Tavily e risponde citando le fonti.

Ogni domanda viene approfondita con un ciclo di ricerca in più giri (4 di default):
a ogni giro l'LLM sceglie una query, Tavily cerca e l'LLM riassume i risultati.
Alla fine i riassunti diventano gli "appunti" da cui nasce la risposta.

Avvio:  poetry run chainlit run info_segugio/__init__.py -w --port 8002
"""

import re
import sys
from pathlib import Path

# Chainlit esegue questo file come script: aggiungiamo la sua cartella a sys.path
# così gli import "from config import ..." funzionano sempre.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import chainlit as cl  # noqa: E402
from openai import APIConnectionError, APIStatusError, AuthenticationError, NotFoundError  # noqa: E402

from config import carica_config  # noqa: E402
from llm import (  # noqa: E402
    NESSUNA_INFORMAZIONE,
    crea_client,
    genera_risposta,
    prima_query,
    query_successiva,
    riassumi,
)
from search import ErroreRicerca, cerca, controlla_chiave, costruisci_contesto  # noqa: E402

# Quanti messaggi della conversazione ricordare (domande + risposte)
MAX_MESSAGGI_CRONOLOGIA = 6

# Nome dell'avatar mostrato durante la ricerca: public/avatars/segugio_ricerca.svg
# (le risposte usano invece public/avatars/info_segugio.svg, dal nome dell'app)
AVATAR_RICERCA = "segugio_ricerca"


@cl.on_chat_start
async def avvio_chat():
    """Prepara la sessione: configurazione, client LLM e cronologia vuota."""
    try:
        config = carica_config()
    except ValueError as e:
        await cl.Message(content=f"Errore di configurazione: {e}").send()
        return

    cl.user_session.set("config", config)
    cl.user_session.set("client", crea_client(config))
    cl.user_session.set("cronologia", [])

    avviso = ""
    if not config.tavily_api_key:
        avviso = "\n\n**Attenzione:** nel file `.env` manca `TAVILY_API_KEY`, le ricerche non funzioneranno."

    await cl.Message(
        content=(
            "Benvenuto in **Info Segugio**. Fammi una domanda di attualità: "
            f"condurrò una ricerca sul web in {config.giri_ricerca} passaggi "
            "e ti risponderò citando le fonti.\n\n"
            f"_Modello: `{config.llm_model}` ({config.provider})_{avviso}"
        )
    ).send()


async def giro_di_ricerca(numero_giro: int, totale_giri: int, domanda: str, stato: dict) -> None:
    """Esegue un giro del ciclo: query -> ricerca -> riassunto.

    `stato` contiene ciò che si accumula tra un giro e l'altro:
    query_usate, riassunti e fonti (dizionario url -> risultato numerato).
    """
    config = cl.user_session.get("config")
    client = cl.user_session.get("client")

    # L'avatar del passo è quello "da ricerca", diverso da quello delle risposte
    async with cl.Step(
        name=f"Ricerca {numero_giro} di {totale_giri}",
        type="tool",
        metadata={"avatarName": AVATAR_RICERCA},
        show_input=False,
    ) as step:
        # 1. Scelta della query
        if numero_giro == 1:
            cronologia = cl.user_session.get("cronologia") or []
            query = await prima_query(client, config.llm_model, domanda, cronologia)
        else:
            query = await query_successiva(client, config.llm_model, domanda, stato["query_usate"], numero_giro)
        stato["query_usate"].append(query)
        step.output = f"**Query:** {query}\n\nRicerca in corso..."
        await step.update()

        # 2. Ricerca con Tavily: teniamo solo le fonti non ancora viste e le numeriamo
        dati = await cerca(query, config.tavily_api_key, config.tavily_max_results, config.tavily_search_depth)
        nuove = []
        for r in dati.get("results", []):
            if r.get("url") and r["url"] not in stato["fonti"]:
                r["numero"] = len(stato["fonti"]) + 1
                stato["fonti"][r["url"]] = r
                nuove.append(r)

        if not nuove:
            step.output = f"**Query:** {query}\n\nNessuna fonte nuova trovata."
            return

        step.output = f"**Query:** {query}\n\n{elenco_fonti(nuove)}\n\nSintesi in corso..."
        await step.update()

        # 3. Riassunto dei risultati del giro: l'LLM li vede numerati 1, 2, 3...
        #    poi trasformiamo le sue citazioni nei numeri globali delle fonti
        riassunto = await riassumi(client, config.llm_model, domanda, costruisci_contesto(nuove))
        riassunto = rinumera_citazioni(riassunto, [r["numero"] for r in nuove])
        if not riassunto.startswith(NESSUNA_INFORMAZIONE):
            stato["riassunti"].append(f"Giro {numero_giro} (query: {query}):\n{riassunto}")
        step.output = f"**Query:** {query}\n\n{elenco_fonti(nuove)}\n\n**Sintesi**\n\n{riassunto}"


def elenco_fonti(risultati: list[dict]) -> str:
    """Elenco markdown delle fonti: [n] Titolo, con il titolo cliccabile."""
    return "\n".join(f"- **[{r['numero']}]** [{r.get('title') or r['url']}]({r['url']})" for r in risultati)


# Una citazione tra parentesi quadre: [3] oppure [2, 5]
CITAZIONE = re.compile(r"\[(\d+(?:\s*,\s*\d+)*)\]")

# Una sezione "Fonti" scritta dal modello in fondo alla risposta (da "Fonti" alla fine del testo)
FONTI_SCRITTE_DAL_MODELLO = re.compile(
    r"\n[#*\s]*Fonti[*:\s]*(?:\[\d+\][,\s]*)*(?:\n.*)?\Z", re.IGNORECASE | re.DOTALL
)


def rinumera_citazioni(testo: str, numeri_globali: list[int]) -> str:
    """Trasforma le citazioni locali di un giro ([1], [2]...) nei numeri globali delle fonti.

    I numeri che non corrispondono a nessuna fonte del giro vengono eliminati.
    """

    def sostituisci(m: re.Match) -> str:
        nuovi = [
            str(numeri_globali[int(n) - 1])
            for n in m.group(1).split(",")
            if 1 <= int(n) <= len(numeri_globali)
        ]
        return f"[{', '.join(nuovi)}]" if nuovi else ""

    return CITAZIONE.sub(sostituisci, testo)


def collega_citazioni(testo: str, fonti: list[dict]) -> str:
    """Rende cliccabili le citazioni nel testo: [2, 5] diventa [2] [5], ognuno con il suo link."""
    url_per_numero = {f["numero"]: f["url"] for f in fonti}

    def sostituisci(m: re.Match) -> str:
        link = [
            f"[[{n}]]({url_per_numero[int(n)]})" if int(n) in url_per_numero else f"[{n}]"
            for n in (x.strip() for x in m.group(1).split(","))
        ]
        return " ".join(link)

    return CITAZIONE.sub(sostituisci, testo)


def fonti_citate(testo: str, fonti: list[dict]) -> list[dict]:
    """Restituisce le fonti citate nel testo con [n] (lista vuota se non ce ne sono)."""
    numeri = {int(n) for gruppo in CITAZIONE.findall(testo) for n in gruppo.split(",")}
    return [f for f in fonti if f["numero"] in numeri]


@cl.on_message
async def nuovo_messaggio(message: cl.Message):
    config = cl.user_session.get("config")
    client = cl.user_session.get("client")
    cronologia: list[dict] = cl.user_session.get("cronologia") or []
    if config is None:
        await cl.Message(content="Configurazione non valida: correggi il file `.env` e ricarica la pagina.").send()
        return

    domanda = message.content.strip()
    stato = {"query_usate": [], "riassunti": [], "fonti": {}}

    try:
        # Controlliamo subito la chiave Tavily, prima di disturbare l'LLM
        controlla_chiave(config.tavily_api_key)

        # 1-3. Ciclo di ricerca: a ogni giro query, ricerca e riassunto
        for giro in range(1, config.giri_ricerca + 1):
            try:
                await giro_di_ricerca(giro, config.giri_ricerca, domanda, stato)
            except ErroreRicerca:
                # Se un giro successivo al primo fallisce, proseguiamo con ciò che abbiamo
                if not stato["fonti"]:
                    raise
                break

        fonti = list(stato["fonti"].values())
        if not fonti:
            await cl.Message(
                content="Non ho trovato risultati per questa domanda. Prova a riformularla."
            ).send()
            return

        # 4. Gli appunti per la risposta finale sono i riassunti utili di tutti i giri
        # (se nessun riassunto è utile, passiamo direttamente i risultati della ricerca)
        appunti = "\n\n".join(stato["riassunti"]) or costruisci_contesto(fonti)

        # 5. Risposta in streaming, poi l'elenco delle fonti citate
        risposta = cl.Message(content="")
        async for pezzo in genera_risposta(client, config.llm_model, domanda, appunti, cronologia):
            await risposta.stream_token(pezzo)
        # Se il modello ha scritto comunque un suo elenco di fonti, lo togliamo:
        # quello ufficiale lo aggiungiamo noi (corpo serve anche per la cronologia)
        corpo = FONTI_SCRITTE_DAL_MODELLO.sub("", risposta.content).rstrip()

        # A fine streaming: citazioni cliccabili ed elenco delle fonti citate.
        # Se la risposta non cita nulla, mostriamo le fonti citate negli appunti (o tutte).
        citate = fonti_citate(corpo, fonti) or fonti_citate(appunti, fonti) or fonti
        risposta.content = collega_citazioni(corpo, fonti) + "\n\n**Fonti**\n\n" + elenco_fonti(citate)
        await risposta.send()

    # 6. Gestione errori: messaggi chiari in chat, nessun crash
    except ErroreRicerca as e:
        await cl.Message(content=str(e)).send()
        return
    except APIConnectionError:
        if config.provider == "ollama":
            testo = "Non riesco a contattare Ollama. Avvialo (`ollama serve`) e riprova."
        else:
            testo = "Non riesco a contattare OpenAI. Controlla la connessione."
        await cl.Message(content=testo).send()
        return
    except AuthenticationError:
        await cl.Message(content="La chiave `OPENAI_API_KEY` non è valida. Controlla il file `.env`.").send()
        return
    except NotFoundError:
        testo = f"Il modello `{config.llm_model}` non è disponibile."
        if config.provider == "ollama":
            testo += f" Scaricalo con `ollama pull {config.llm_model}`."
        await cl.Message(content=testo).send()
        return
    except APIStatusError as e:
        await cl.Message(content=f"Errore dal modello linguistico ({e.status_code}): {e.message}").send()
        return

    # Salviamo lo scambio nella cronologia (solo gli ultimi messaggi)
    cronologia += [
        {"role": "user", "content": domanda},
        {"role": "assistant", "content": corpo},
    ]
    cl.user_session.set("cronologia", cronologia[-MAX_MESSAGGI_CRONOLOGIA:])
