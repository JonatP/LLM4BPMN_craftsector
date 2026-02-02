"""Prompt Loader - Lädt Prompts aus dem prompts Ordner"""

import os
from pathlib import Path
from functools import lru_cache

# Pfad zum prompts Ordner
PROMPTS_DIR = Path(__file__).parent / "prompts"


@lru_cache(maxsize=None)
def load_prompt(prompt_name: str) -> str:
    """
    Lädt einen Prompt aus einer Textdatei.
    
    Args:
        prompt_name: Name der Prompt-Datei (ohne .txt Endung)
        
    Returns:
        Der Prompt-Text
        
    Raises:
        FileNotFoundError: Wenn die Prompt-Datei nicht existiert
    """
    prompt_path = PROMPTS_DIR / f"{prompt_name}.txt"
    
    if not prompt_path.exists():
        raise FileNotFoundError(f"Prompt '{prompt_name}' nicht gefunden: {prompt_path}")
    
    with open(prompt_path, "r", encoding="utf-8") as f:
        return f.read()


def format_prompt(prompt_name: str, **kwargs) -> str:
    """
    Lädt einen Prompt und ersetzt Platzhalter mit den übergebenen Werten.
    
    Args:
        prompt_name: Name der Prompt-Datei (ohne .txt Endung)
        **kwargs: Werte für die Platzhalter im Prompt
        
    Returns:
        Der formatierte Prompt-Text
    """
    template = load_prompt(prompt_name)
    
    result = template
    for key, value in kwargs.items():
        result = result.replace("{" + key + "}", str(value) if value else "")
    return result


# Verfügbare Prompts als Konstanten für einfachen Zugriff
class Prompts:
    """Konstanten für verfügbare Prompt-Namen"""
    COT = "cot_prompt"
    IMPROVEMENT = "improvement_prompt"
    DI_GENERATION = "di_generation_prompt"
    SECURITY_AGENT = "security_agent_prompt"
    SUMMARY_AGENT = "summary_agent_prompt"
    PROBING_AGENT = "probing_agent_prompt"
    SUMMARIZE_ANSWER = "summarize_answer_prompt"
    TOPIC_MANAGER = "topic_manager_prompt"


def list_available_prompts() -> list:
    """Listet alle verfügbaren Prompts auf"""
    if not PROMPTS_DIR.exists():
        return []
    return [f.stem for f in PROMPTS_DIR.glob("*.txt")]
