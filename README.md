# BPMN Generator für Handwerksbetriebe

Eine Web-Anwendung zur automatischen Generierung von BPMN-Prozessdiagrammen durch ein geführtes Interview. Entwickelt speziell für Handwerksbetriebe.

## Voraussetzungen

- Python 3.10 oder höher
- OpenAI API Key
- (Optional) Neon PostgreSQL Datenbank für Persistenz

## Installation

1. **Repository klonen:**
   ```bash
   git clone <repository-url>
   cd LLM4BPMN_reflex
   ```

2. **Virtuelle Umgebung erstellen (empfohlen):**
   ```bash
   python -m venv .venv
   source .venv/bin/activate  # macOS/Linux
   # oder
   .venv\Scripts\activate     # Windows
   ```

3. **Abhängigkeiten installieren:**
   ```bash
   pip install -r requirements.txt
   ```

4. **Reflex initialisieren:**
   ```bash
   reflex init
   ```

## Konfiguration

Erstelle eine `.env` Datei im Projektverzeichnis:

```env
# OpenAI API Key (erforderlich)
OPENAI_API_KEY=sk-proj-...

# Neon PostgreSQL Datenbank (optional)
DATABASE_URL=postgresql://user:password@host/database?sslmode=require
```

### OpenAI API Key erhalten

1. Gehe zu [platform.openai.com](https://platform.openai.com)
2. Erstelle einen Account oder melde dich an
3. Navigiere zu "API Keys"
4. Erstelle einen neuen API Key

## Anwendung starten

```bash
reflex run
```

Die Anwendung ist dann unter [http://localhost:3000](http://localhost:3000) erreichbar.

### Entwicklungsmodus

Für Hot-Reloading während der Entwicklung:
```bash
reflex run --env dev
```

## Nutzung

1. **Prozess auswählen** - Wähle den Prozesstyp (z.B. Angebots- und Auftragserstellung)
2. **Interview starten** - Beantworte die Fragen per Text oder Spracheingabe
3. **BPMN generieren** - Nach Abschluss des Interviews wird das Diagramm erstellt
4. **Exportieren** - Lade das BPMN-Diagramm als XML herunter

## Projektstruktur

```
LLM4BPMN_reflex/
├── LLM4BPMN_reflex/
│   ├── LLM4BPMN_reflex.py  # Haupt-App (UI & State)
│   ├── bpmn_generator.py    # BPMN-Generierung & Interview-Agents
│   ├── prompt_loader.py     # Prompt-Management
│   ├── db.py                # Datenbank-Modul
│   ├── config/              # JSON-Konfigurationen
│   └── prompts/             # KI-Prompts
├── assets/                  # Statische Dateien (CSS, JS, BPMN)
├── requirements.txt
├── rxconfig.py              # Reflex-Konfiguration
└── .env                     # Umgebungsvariablen (nicht im Git)
```

## Technologien

- **[Reflex](https://reflex.dev)** - Python Web Framework
- **[OpenAI GPT](https://openai.com)** - BPMN-Generierung & Interview
- **[OpenAI Whisper](https://openai.com)** - Spracherkennung
- **[bpmn-js](https://bpmn.io)** - BPMN-Visualisierung
- **[Neon PostgreSQL](https://neon.tech)** - Serverless Datenbank

## Lizenz

MIT License
