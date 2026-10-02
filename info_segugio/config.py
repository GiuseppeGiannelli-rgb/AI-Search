"""Configurazione di Info Segugio: legge il file .env e sceglie il provider LLM."""

import os
from dataclasses import dataclass

from dotenv import load_dotenv

# Indirizzo dell'API compatibile OpenAI esposta da Ollama in locale
OLLAMA_BASE_URL = "http://localhost:11434/v1"


@dataclass
class Config:
    """Tutte le impostazioni dell'app, raccolte in un unico oggetto."""

    tavily_api_key: str
    provider: str  # "ollama" oppure "openai"
    openai_api_key: str
    llm_model: str
    base_url: str | None  # None = server ufficiale OpenAI
    tavily_max_results: int
    tavily_search_depth: str


def scegli_provider() -> str:
    """Restituisce il provider da usare.

    Se PROVIDER è vuoto: openai quando c'è OPENAI_API_KEY, altrimenti ollama.
    """
    provider = os.getenv("PROVIDER", "").strip().lower()
    if provider in ("ollama", "openai"):
        return provider
    if provider:
        raise ValueError(f"PROVIDER non valido: '{provider}'. Usa 'ollama' oppure 'openai'.")
    return "openai" if os.getenv("OPENAI_API_KEY", "").strip() else "ollama"


def carica_config() -> Config:
    """Costruisce la configurazione leggendo le variabili d'ambiente."""
    # Rilegge il .env a ogni nuova chat: così le modifiche valgono senza riavviare
    load_dotenv(override=True)
    provider = scegli_provider()

    if provider == "openai":
        modello = os.getenv("OPENAI_LLM_MODEL", "").strip() or "gpt-4o-mini"
        base_url = None
    else:
        modello = os.getenv("OLLAMA_LLM_MODEL", "").strip() or "llama3.2"
        base_url = OLLAMA_BASE_URL

    return Config(
        tavily_api_key=os.getenv("TAVILY_API_KEY", "").strip(),
        provider=provider,
        openai_api_key=os.getenv("OPENAI_API_KEY", "").strip(),
        llm_model=modello,
        base_url=base_url,
        tavily_max_results=int(os.getenv("TAVILY_MAX_RESULTS", "").strip() or 5),
        tavily_search_depth=os.getenv("TAVILY_SEARCH_DEPTH", "").strip() or "advanced",
    )
