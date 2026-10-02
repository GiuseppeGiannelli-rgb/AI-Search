"""Info Segugio: chat Chainlit che cerca sul web con Tavily e risponde citando le fonti.

Avvio:  poetry run chainlit run info_segugio/__init__.py -w --port 8002
"""

import sys
from pathlib import Path

# Chainlit esegue questo file come script: aggiungiamo la sua cartella a sys.path
# così gli import "from config import ..." funzionano sempre.
sys.path.insert(0, str(Path(__file__).resolve().parent))

import chainlit as cl  # noqa: E402
from openai import APIConnectionError, APIStatusError, AuthenticationError, NotFoundError  # noqa: E402

from config import carica_config  # noqa: E402
from llm import crea_client, genera_risposta, riformula_query  # noqa: E402
from search import ErroreRicerca, cerca, controlla_chiave, costruisci_contesto  # noqa: E402

# Quanti messaggi della conversazione ricordare (domande + risposte)
MAX_MESSAGGI_CRONOLOGIA = 6


@cl.on_chat_start
async def avvio_chat():
    """Prepara la sessione: configurazione, client LLM e cronologia vuota."""
    try:
        config = carica_config()
    except ValueError as e:
        await cl.Message(content=f"⚠️ Errore di configurazione: {e}").send()
        return

    cl.user_session.set("config", config)
    cl.user_session.set("client", crea_client(config))
    cl.user_session.set("cronologia", [])

    avviso = ""
    if not config.tavily_api_key:
        avviso = "\n\n⚠️ Attenzione: nel `.env` manca **TAVILY_API_KEY**, le ricerche non funzioneranno."

    await cl.Message(
        content=(
            "Ciao! Sono **Info Segugio** 🐕 — fammi una domanda di attualità, "
            "cerco sul web e ti rispondo citando le fonti.\n\n"
            f"_Modello: `{config.llm_model}` ({config.provider})_{avviso}"
        )
    ).send()


@cl.on_message
async def nuovo_messaggio(message: cl.Message):
    config = cl.user_session.get("config")
    client = cl.user_session.get("client")
    cronologia: list[dict] = cl.user_session.get("cronologia") or []
    if config is None:
        await cl.Message(content="⚠️ Configurazione non valida: correggi il `.env` e ricarica la pagina.").send()
        return

    domanda = message.content.strip()

    try:
        # Controlliamo subito la chiave Tavily, prima di disturbare l'LLM
        controlla_chiave(config.tavily_api_key)

        # 1-3. Riformulazione della domanda e ricerca su Tavily
        async with cl.Step(name="Ricerca sul web", type="tool") as step:
            query = await riformula_query(client, config.llm_model, domanda, cronologia)
            step.input = query
            dati = await cerca(query, config.tavily_api_key, config.tavily_max_results, config.tavily_search_depth)
            risultati = dati.get("results", [])
            elenco = "\n".join(f"- [{r.get('title') or r['url']}]({r['url']})" for r in risultati)
            step.output = f"Query: **{query}**\n\n{elenco or 'Nessun risultato'}"

        if not risultati:
            await cl.Message(
                content=f"🤷 Non ho trovato risultati per **{query}**. Prova a riformulare la domanda."
            ).send()
            return

        # 4. Contesto per l'LLM
        contesto = costruisci_contesto(risultati)

        # 5. Risposta in streaming
        risposta = cl.Message(content="")
        async for pezzo in genera_risposta(client, config.llm_model, domanda, contesto, cronologia):
            await risposta.stream_token(pezzo)
        await risposta.send()

    # 6. Gestione errori: messaggi chiari in chat, nessun crash
    except ErroreRicerca as e:
        await cl.Message(content=str(e)).send()
        return
    except APIConnectionError:
        if config.provider == "ollama":
            testo = "🦙 Non riesco a contattare Ollama. Avvialo (`ollama serve`) e riprova."
        else:
            testo = "🌐 Non riesco a contattare OpenAI. Controlla la connessione."
        await cl.Message(content=testo).send()
        return
    except AuthenticationError:
        await cl.Message(content="🔑 La chiave **OPENAI_API_KEY** non è valida. Controlla il `.env`.").send()
        return
    except NotFoundError:
        testo = f"❓ Il modello `{config.llm_model}` non è disponibile."
        if config.provider == "ollama":
            testo += f" Scaricalo con `ollama pull {config.llm_model}`."
        await cl.Message(content=testo).send()
        return
    except APIStatusError as e:
        await cl.Message(content=f"⚠️ Errore dal modello linguistico ({e.status_code}): {e.message}").send()
        return

    # Salviamo lo scambio nella cronologia (solo gli ultimi messaggi)
    cronologia += [
        {"role": "user", "content": domanda},
        {"role": "assistant", "content": risposta.content},
    ]
    cl.user_session.set("cronologia", cronologia[-MAX_MESSAGGI_CRONOLOGIA:])
