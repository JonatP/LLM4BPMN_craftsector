import reflex as rx
from typing import List, Dict, Any
import os
import base64
import tempfile
import json
import asyncio
import time
from pathlib import Path
from dotenv import load_dotenv
from openai import OpenAI

env_path = Path(__file__).parent.parent / ".env"
load_dotenv(dotenv_path=env_path)

from .bpmn_generator import BPMNGenerator, InterviewAgents
from . import db


def load_json_config(filename: str) -> Dict[str, Any]:
    config_path = Path(__file__).parent / "config" / filename
    with open(config_path, "r", encoding="utf-8") as f:
        return json.load(f)

PROCESS_INFO = load_json_config("process-info.json")
TOPIC_DEFS = load_json_config("topics.json")["topics"]
TOPIC_TITLES = {topic["key"]: topic["title"] for topic in TOPIC_DEFS}

class BPMNState(rx.State):
    process_type: str = ""
    current_topic_key: str = ""
    current_question_text: str = ""
    current_question_title: str = ""
    interview_answers: Dict[str, str] = {}
    dialog_history: List[Dict[str, str]] = []
    is_interview_active: bool = False
    interview_complete: bool = False
    interview_summary: str = ""
    topic_history: Dict[str, List[Dict[str, Any]]] = {}
    topics_completed: List[str] = []
    bpmn_xml: str = ""
    active_tab: str = "input"
    active_info_tab: str = "funktioniert"
    is_loading: bool = False
    error_message: str = ""
    answer_input: str = ""
    success_message: str = ""
    is_recording: bool = False
    is_transcribing: bool = False
    recording_duration: int = 0
    is_saving_answer: bool = False
    generation_step: str = ""
    sidebar_open: bool = True
    audio_error: str = ""

    @rx.var
    def bpmn_viewer_html(self) -> str:
        if not self.bpmn_xml:
            return ""
        escaped_bpmn = self.bpmn_xml.replace('`', '\\`')
        return f'''
        <!DOCTYPE html>
        <html>
            <head>
                <link rel="stylesheet" href="https://unpkg.com/bpmn-js@18.3.1/dist/assets/bpmn-js.css" />
                <script src="https://unpkg.com/bpmn-js@18.3.1/dist/bpmn-navigated-viewer.development.js"></script>
                <style>
                    html, body {{
                        margin: 0;
                        padding: 0;
                        overflow: hidden;
                        width: 100%;
                        height: 100%;
                    }}
                    #canvas {{
                        width: 100%;
                        height: 100%;
                        background: white;
                    }}
                    .bjs-container {{
                        width: 100% !important;
                        height: 100% !important;
                    }}
                </style>
            </head>
            <body>
                <div id="canvas"></div>
                <script>
                    (async function(){{
                        var viewer = new BpmnJS({{ container: \'#canvas\' }});
                        var bpmn = `{escaped_bpmn}`;
                        try {{
                            await viewer.importXML(bpmn);
                            var canvas = viewer.get(\'canvas\');
                            canvas.zoom(\'fit-viewport\', \'auto\');
                        }} catch (err) {{
                            console.error(\'Error loading BPMN:\', err);
                            document.body.innerHTML = \'<p style="padding: 1rem; color: red;">Fehler beim Laden des Diagramms</p>\';
                        }}
                    }})();
                </script>
            </body>
        </html>
        '''

    def toggle_sidebar(self):
        self.sidebar_open = not self.sidebar_open

    def set_process_type(self, value: str):
        self.process_type = value

    def get_process_context(self) -> str:
        info = PROCESS_INFO.get(self.process_type, {})
        return info.get("context", "Handwerklicher Geschäftsprozess")

    async def start_interview(self):
        if not self.process_type:
            self.error_message = "Bitte wählen Sie zuerst einen Prozesstyp aus."
            return
        
        self.is_loading = True
        self.generation_step = "Interview wird gestartet..."
        yield
        
        self.is_interview_active = True
        self.interview_complete = False
        self.current_topic_key = TOPIC_DEFS[0]["key"]
        self.current_question_text = ""
        self.current_question_title = ""
        self.interview_answers = {}
        self.dialog_history = []
        self.interview_summary = ""
        self.topic_history = {}
        self.topics_completed = []
        self.error_message = ""
        self.success_message = ""
        process_context = self.get_process_context()
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            self.error_message = "OpenAI API Key nicht konfiguriert"
            self.is_interview_active = False
            self.is_loading = False
            self.generation_step = ""
            return

        agents = InterviewAgents(api_key)
        self.generation_step = "Erste Frage wird vorbereitet..."
        yield
        
        topic_result = agents.run_topic_manager_agent(
            process_context=process_context,
            summary_text=self.interview_summary,
            topic_defs=TOPIC_DEFS,
            topics_completed=self.topics_completed,
            current_topic_key=self.current_topic_key,
            topic_history=self.topic_history,
        )

        if topic_result.get("complete"):
            self.topics_completed.append(self.current_topic_key)
        if len(self.topics_completed) >= len(TOPIC_DEFS):
            self.is_interview_active = False
            self.interview_complete = True
            self.dialog_history.append({
                "role": "assistant",
                "content": "Alle Themen sind bereits ausreichend beantwortet.",
                "title": "Interview abgeschlossen"
            })
            self.generation_step = ""
            self.is_loading = False
            return

        self.current_topic_key = topic_result.get("next_topic_key", self.current_topic_key)
        self.current_question_text = topic_result.get("next_question", "Beschreiben Sie bitte den Prozessstart.")
        topic_title = TOPIC_TITLES.get(self.current_topic_key, "Thema")
        topic_number = len(self.topics_completed) + 1
        self.current_question_title = f"Thema {topic_number} von {len(TOPIC_DEFS)}: {topic_title}"
        self.dialog_history.append({
            "role": "assistant",
            "content": self.current_question_text,
            "title": self.current_question_title
        })
        self.generation_step = ""
        self.is_loading = False

    async def submit_answer(self):
        if not self.answer_input.strip():
            self.is_saving_answer = False
            return
            
        self.is_saving_answer = True
        answer_text = self.answer_input
        self.answer_input = ""
        base_key = self.current_topic_key
        is_followup = False
        display_question = self.current_question_text
        self.dialog_history.append({
            "role": "user",
            "content": answer_text
        })
        
        self.is_loading = True
        self.generation_step = "Antwort wird verarbeitet..."
        
        try:
            api_key = os.getenv("OPENAI_API_KEY")
            if not api_key:
                self.error_message = "OpenAI API Key nicht konfiguriert"
                self.is_loading = False
                self.is_saving_answer = False
                return
            agents = InterviewAgents(api_key)
            process_context = self.get_process_context()
            self.generation_step = "Sicherheitsprüfung..."
            security_result = agents.run_security_agent(
                self.current_question_text,
                answer_text,
                process_context,
                self.interview_summary
            )
            
            if security_result.get("flagged"):
                self.dialog_history.append({
                    "role": "assistant",
                    "content": security_result.get("nudge", "Bitte beantworten Sie die Frage zum Prozess.")
                })
                self.is_loading = False
                return
            if base_key not in self.topic_history:
                self.topic_history[base_key] = []
            self.topic_history[base_key].append({
                "question": display_question,
                "answer": answer_text,
                "is_followup": is_followup
            })
            self.generation_step = "Antwort wird zusammengefasst..."
            combined_turns = "\n".join(
                f"Frage: {item['question']} Antwort: {item['answer']}"
                for item in self.topic_history[base_key]
            )
            summary = agents.summarize_answer(self.current_question_text, combined_turns)
            self.interview_answers[base_key] = summary
            self.generation_step = "Zusammenfassung wird aktualisiert..."
            prev_summary = self.interview_summary
            new_summary = agents.run_summary_agent(
                self.interview_summary,
                self.current_question_text,
                combined_turns,
                self.topic_history[base_key],
                process_context
            )
            if not new_summary.strip():
                self.interview_summary = prev_summary
            else:
                self.interview_summary = new_summary

            self.generation_step = "Nächste Frage wird vorbereitet..."
            topic_result = agents.run_topic_manager_agent(
                process_context=process_context,
                summary_text=self.interview_summary,
                topic_defs=TOPIC_DEFS,
                topics_completed=self.topics_completed,
                current_topic_key=self.current_topic_key,
                topic_history=self.topic_history,
            )

            if topic_result.get("complete") and base_key and base_key not in self.topics_completed:
                self.topics_completed.append(base_key)

            if len(self.topics_completed) >= len(TOPIC_DEFS):
                self.interview_complete = True
                self.is_interview_active = False
                self.dialog_history.append({
                    "role": "assistant",
                    "content": "Vielen Dank! Alle Themen sind beantwortet. Klicken Sie auf 'BPMN generieren', um Ihr Prozessdiagramm zu erstellen.",
                    "title": "Interview abgeschlossen"
                })
            else:
                self.current_topic_key = topic_result.get("next_topic_key", self.current_topic_key)
                self.current_question_text = topic_result.get("next_question", "Können Sie noch einen weiteren Schritt beschreiben?")
                topic_title = TOPIC_TITLES.get(self.current_topic_key, "Thema")
                topic_number = min(len(self.topics_completed) + 1, len(TOPIC_DEFS))
                self.current_question_title = f"Thema {topic_number} von {len(TOPIC_DEFS)}: {topic_title}"
                self.dialog_history.append({
                    "role": "assistant",
                    "content": self.current_question_text,
                    "title": self.current_question_title
                })
                
        except Exception as e:
            self.error_message = f"Fehler bei der Verarbeitung: {str(e)}"
        finally:
            self.is_loading = False
            self.generation_step = ""
            self.is_saving_answer = False

    def set_answer_input(self, value: str):
        self.answer_input = value

    def prepare_text_submit(self):
        self.is_saving_answer = True

    def reset_interview(self):
        self.is_interview_active = False
        self.interview_complete = False
        self.current_topic_key = ""
        self.current_question_text = ""
        self.current_question_title = ""
        self.interview_answers = {}
        self.dialog_history = []
        self.bpmn_xml = ""
        self.mermaid_code = ""
        self.answer_input = ""
        self.error_message = ""
        self.success_message = ""
        self.interview_summary = ""
        self.topic_history = {}
        self.topics_completed = []
        self.is_recording = False
        self.is_transcribing = False
        self.process_type = ""

    def start_recording(self):
        self.is_recording = True
        self.recording_duration = 0
        self.audio_error = ""
        self.error_message = ""

    def prepare_stop(self):
        self.is_saving_answer = True

    def update_recording_duration(self, duration: int):
        self.recording_duration = duration

    async def stop_recording_and_transcribe(self, audio_json: str):
        self.is_recording = False
        self.recording_duration = 0
        
        if not audio_json:
            self.error_message = "Keine Audiodaten empfangen"
            return
            
        self.is_transcribing = True
        self.audio_error = ""
        
        try:
            import json
            try:
                data = json.loads(audio_json)
            except json.JSONDecodeError:
                self.error_message = "Ungültige Audiodaten empfangen"
                self.is_transcribing = False
                return

            if isinstance(data, dict) and data.get("error"):
                self.error_message = f"Audioaufnahme fehlgeschlagen: {data['error']}"
                self.is_transcribing = False
                return
            
            audio_base64 = data.get("audioData", "")
            mime_type = data.get("mimeType", "audio/webm")
            if not audio_base64:
                self.error_message = "Keine Audiodaten empfangen"
                self.is_transcribing = False
                return
            audio_bytes = base64.b64decode(audio_base64)
            transcribed_text = await self._transcribe_audio_whisper(audio_bytes, mime_type)
            if transcribed_text:
                self.answer_input = transcribed_text
                self.is_transcribing = False
                await self.submit_answer()
            else:
                self.error_message = "Transkription fehlgeschlagen - keine Sprache erkannt"
                self.is_transcribing = False
                
        except Exception as e:
            self.error_message = f"Fehler bei der Transkription: {str(e)}"
            self.is_transcribing = False
        finally:
            self.is_saving_answer = False

    def handle_recording_error(self, error: str):
        if not error:
            return
        self.is_recording = False
        self.is_transcribing = False
        self.audio_error = error
        self.error_message = error

    async def _transcribe_audio_whisper(self, audio_bytes: bytes, mime_type: str) -> str:
        api_key = os.getenv("OPENAI_API_KEY")
        if not api_key:
            raise ValueError("OpenAI API Key nicht konfiguriert")
        
        client = OpenAI(api_key=api_key)
        extension = ".webm" if "webm" in mime_type else ".wav"
        with tempfile.NamedTemporaryFile(suffix=extension, delete=False) as f:
            f.write(audio_bytes)
            temp_path = f.name
        
        def _do_transcribe(path: str) -> str:
            with open(path, "rb") as audio_file:
                transcript = client.audio.transcriptions.create(
                    model="whisper-1",
                    file=audio_file,
                    language="de"
                )
            return transcript.text

        try:
            transcript_text = await asyncio.wait_for(
                asyncio.to_thread(_do_transcribe, temp_path),
                timeout=90,
            )
            return transcript_text
        finally:
            try:
                os.unlink(temp_path)
            except:
                pass

    async def generate_bpmn(self):
        self.is_loading = True
        self.error_message = ""
        self.success_message = ""
        start_time = time.time()
        
        try:
            api_key = os.getenv("OPENAI_API_KEY")
            if not api_key:
                self.error_message = "OpenAI API Key nicht konfiguriert"
                self.is_loading = False
                return
            if not self.interview_answers:
                self.error_message = "Bitte führen Sie zuerst das Interview durch."
                self.is_loading = False
                return
            process_context = self.get_process_context()
            context_parts = [f"Prozesstyp: {self.process_type}", f"Kontext: {process_context}", ""]
            
            for topic in TOPIC_DEFS:
                key = topic["key"]
                if key in self.interview_answers:
                    context_parts.append(f"Thema: {topic['title']}")
                    context_parts.append(f"Antwort: {self.interview_answers[key]}")
                    context_parts.append("")
            
            process_description = "\n".join(context_parts)
            
            if not process_description.strip():
                self.error_message = "Keine Prozessbeschreibung vorhanden."
                self.is_loading = False
                return
            generator = BPMNGenerator(api_key)
            self.generation_step = "Generiere BPMN-Modell..."
            print("BPMN prompt start: text_to_model")
            initial_xml = generator.text_to_model(process_description)
            
            if not initial_xml:
                self.error_message = "Fehler bei der initialen BPMN-Generierung"
                self.is_loading = False
                return
            self.generation_step = "Prüfe Vollständigkeit..."
            issues = generator.check_xml_completeness(initial_xml)
            
            improved_xml = initial_xml
            improvement_attempts = 0
            max_attempts = 3
            
            while issues and improvement_attempts < max_attempts:
                self.generation_step = f"Verbessere Modell (Versuch {improvement_attempts + 1}/{max_attempts})..."
                print(f"BPMN prompt start: improve_model attempt {improvement_attempts + 1}/{max_attempts}")
                improved_xml = generator.improve_model(improved_xml, process_description)
                issues = generator.check_xml_completeness(improved_xml)
                improvement_attempts += 1
            self.generation_step = "Generiere Diagramm-Layout..."
            di_xml = generator.generate_bpmn_di(improved_xml)
            if di_xml:
                self.generation_step = "Füge Diagramm zusammen..."
                final_xml = generator.merge_bpmn_xml_diagram(improved_xml, di_xml)
            else:
                final_xml = improved_xml
            
            self.bpmn_xml = final_xml
            self.generation_step = "Erstelle Visualisierung..."
            self.mermaid_code = bpmn_to_mermaid(final_xml)
            generation_duration = time.time() - start_time
            self.generation_step = "Speichere in Datenbank..."
            try:
                db.save_bpmn_generation(
                    process_type=self.process_type,
                    ai_model="gpt-5.2",
                    chat_history=list(self.dialog_history),
                    interview_summary=self.interview_summary,
                    bpmn_xml=final_xml,
                    generation_duration_seconds=generation_duration
                )
                print(f"BPMN generation saved to database (duration: {generation_duration:.2f}s)")
            except Exception as db_error:
                print(f"Warning: Could not save to database: {db_error}")
            
            self.active_tab = "output"
            self.success_message = "BPMN-Diagramm erfolgreich erstellt!"
            print("BPMN model successfully created and visualized")
            
        except Exception as e:
            self.error_message = f"Fehler bei der BPMN-Generierung: {str(e)}"
        finally:
            self.is_loading = False
            self.generation_step = ""

    def set_active_tab(self, tab: str):
        self.active_tab = tab
        if tab == "output":
            self.sidebar_open = False
        elif tab == "input":
            self.sidebar_open = True

    def set_info_tab(self, tab: str):
        self.active_info_tab = tab

    def prepare_generation(self):
        self.is_loading = True
        self.generation_step = "Prozessmodell wird erstellt..."


def header() -> rx.Component:
    return rx.box(
        rx.hstack(
            sidebar_toggle_button(),
            rx.icon("workflow", size=34, color="white"),
            rx.vstack(
                rx.heading(
                    "BPMN Generator",
                    size="8",
                    color="white",
                    class_name="hero-title",
                ),
                rx.text(
                    "Geführte Prozessaufnahme für Handwerksbetriebe",
                    color="rgba(255,255,255,0.85)",
                    size="3",
                    class_name="section-subtitle",
                ),
                align="start",
                spacing="1",
            ),
            rx.spacer(),
            width="100%",
            padding="1.5rem 2.5rem",
            align="center",
            class_name="hero-row",
            gap="1rem",
        ),
        background=(
            "linear-gradient(120deg, rgba(8, 90, 78, 0.98) 0%, "
            "rgba(12, 128, 98, 0.98) 55%, rgba(10, 150, 112, 0.98) 100%)"
        ),
        border_bottom="1px solid rgba(255,255,255,0.18)",
        width="100%",
    )

def sidebar_toggle_button() -> rx.Component:
    return rx.button(
        rx.cond(
            BPMNState.sidebar_open,
            rx.icon("x", size=20),
            rx.icon("menu", size=20),
        ),
        rx.cond(
            BPMNState.sidebar_open,
            "",
            " Informationen anzeigen",
        ),
        on_click=BPMNState.toggle_sidebar,
        variant="outline",
        color_scheme="teal",
        size="2",
        class_name="sidebar-toggle-btn",
    )

def sidebar_nav_item(label: str, tab_id: str, icon_name: str) -> rx.Component:
    return rx.button(
        rx.hstack(
            rx.icon(icon_name, size=26),
            rx.text(label),
            gap="1.25rem",
            width="100%",
            justify="start",
            align="center",
        ),
        on_click=lambda: BPMNState.set_info_tab(tab_id),
        variant=rx.cond(
            (BPMNState.active_info_tab == tab_id) & (BPMNState.active_tab != "output"),
            "solid",
            "ghost"
        ),
        color_scheme="teal",
        width="100%",
        padding="1.25rem 1.5rem",
        size="4",
        font_size="1.1rem",
        class_name="sidebar-nav-item",
    )


def sidebar_panel() -> rx.Component:
    return rx.cond(
        BPMNState.sidebar_open,
        rx.box(
            rx.vstack(
                rx.hstack(
                    rx.heading("Information", size="6", color="#0f766e"),
                    width="100%",
                    align="center",
                ),
                rx.divider(margin_y="1rem"),
                sidebar_nav_item("Anleitung", "funktioniert", "list_ordered"),
                sidebar_nav_item("Was ist BPMN?", "allgemein", "help_circle"),
                sidebar_nav_item("Beispielprozess", "beispiel", "file_text"),
                width="100%",
                gap="0.5rem",
                align="center",
            ),
            position="fixed",
            left="0",
            top="0",
            height="100vh",
            width="320px",
            background="rgba(255,255,255,0.98)",
            border_right="1px solid rgba(15, 23, 42, 0.1)",
            box_shadow="4px 0 20px rgba(0,0,0,0.1)",
            padding="1.75rem",
            z_index="1000",
            class_name="sidebar-panel",
        ),
    )

def info_content_section() -> rx.Component:
    return rx.box(
        rx.match(
            BPMNState.active_info_tab,
            ("allgemein", allgemein_content()),
            ("beispiel", beispiel_content()),
            ("funktioniert", funktioniert_content()),
            allgemein_content(),
        ),
        padding="1.75rem",
        background="rgba(255,255,255,0.92)",
        border_radius="20px",
        box_shadow="var(--shadow)",
        border="1px solid rgba(15, 23, 42, 0.08)",
        width="100%",
        class_name="glass-panel",
    )


def info_tabs() -> rx.Component:
    return info_content_section()


def allgemein_content() -> rx.Component:
    return rx.vstack(
        rx.heading("Was ist BPMN?", size="5", color="#0f766e", class_name="section-title"),
        rx.text(
            "BPMN (Business Process Model and Notation) ist ein internationaler Standard zur grafischen Darstellung von Geschäftsprozessen. Mit BPMN können Sie Abläufe in Ihrem Betrieb visualisieren und optimieren.",
            color="#4a5568",
            class_name="section-subtitle",
        ),
        rx.divider(margin_y="1rem"),
        rx.heading("Warum BPMN im Handwerk?", size="5", color="#0f766e", class_name="section-title"),
        rx.hstack(
            benefit_item("Klare Dokumentation", "Prozesse werden für alle Mitarbeiter verständlich dokumentiert"),
            benefit_item("Effizienzsteigerung", "Schwachstellen und Optimierungspotenziale werden sichtbar"),
            benefit_item("Einarbeitung", "Neue Mitarbeiter lernen Abläufe schneller kennen"),
            benefit_item("Qualitätssicherung", "Standardisierte Prozesse führen zu gleichbleibender Qualität"),
            flex_wrap="nowrap",
            gap="2rem",
            justify="between",
            align="stretch",
            width="100%",
        ),
        rx.divider(margin_y="1.5rem"),
        rx.heading("Welche Symbole gibt es?", size="5", color="#0f766e", margin_bottom="1rem", class_name="section-title"),
        rx.box(
            bpmn_element_svg(
                rx.el.svg(
                    rx.el.circle(cx="18", cy="18", r="12", class_name="bpmn-start"),
                    view_box="0 0 36 36",
                ),
                "Start-Ereignis",
                "Markiert den Beginn eines Prozesses",
            ),
            bpmn_element_svg(
                rx.el.svg(
                    rx.el.circle(cx="18", cy="18", r="12", class_name="bpmn-end"),
                    rx.el.circle(cx="18", cy="18", r="9.5", class_name="bpmn-end-outer"),
                    view_box="0 0 36 36",
                ),
                "End-Ereignis",
                "Markiert das Ende eines Prozesses",
            ),
            bpmn_element_svg(
                rx.el.svg(
                    rx.el.rect(x="4", y="6", width="28", height="20", rx="3", class_name="bpmn-task"),
                    view_box="0 0 36 36",
                ),
                "Task/Aufgabe",
                "Eine auszuführende Aktivität",
            ),
            bpmn_element_svg(
                rx.el.svg(
                    rx.el.rect(x="4", y="8", width="28", height="20", rx="2", class_name="bpmn-message"),
                    rx.el.path(d="M4 8l14 10 14-10", class_name="bpmn-message"),
                    view_box="0 0 36 36",
                ),
                "Nachricht",
                "Kommunikation zwischen Beteiligten",
            ),
            bpmn_element_svg(
                rx.el.svg(
                    rx.el.polygon(points="18,3 33,18 18,33 3,18", class_name="bpmn-gateway"),
                    rx.el.path(d="M12 12l12 12 M24 12l-12 12", class_name="bpmn-gateway-mark"),
                    view_box="0 0 36 36",
                ),
                "Exklusives Gateway",
                "Entweder/Oder-Entscheidung",
            ),
            bpmn_element_svg(
                rx.el.svg(
                    rx.el.polygon(points="18,3 33,18 18,33 3,18", class_name="bpmn-gateway"),
                    rx.el.path(d="M18 8v20 M8 18h20", class_name="bpmn-gateway-mark"),
                    view_box="0 0 36 36",
                ),
                "Paralleles Gateway",
                "Parallele Pfade (alle werden ausgeführt)",
            ),
            bpmn_element_svg(
                rx.el.svg(
                    rx.el.line(x1="4", y1="18", x2="30", y2="18", class_name="bpmn-flow"),
                    rx.el.polygon(points="30,18 24,14 24,22", class_name="bpmn-flow"),
                    view_box="0 0 36 36",
                ),
                "Sequenzfluss",
                "Verbindet Elemente im Ablauf",
            ),
            bpmn_element_svg(
                rx.el.svg(
                    rx.el.path(d="M8 6h16l4 4v16H8z", class_name="bpmn-data"),
                    rx.el.path(d="M24 6v4h4z", class_name="bpmn-data-fold"),
                    view_box="0 0 36 36",
                ),
                "Datenobjekt",
                "Daten die im Prozess verwendet werden",
            ),
            display="flex",
            flex_wrap="wrap",
            gap="1rem",
            width="100%",
        ),
        align="start",
        width="100%",
    )


def benefit_item(title: str, description: str) -> rx.Component:
    return rx.vstack(
        rx.text(title, font_weight="bold", color="#0f766e", font_size="1.35rem"),
        rx.text(description, font_size="1.2rem", color="#000000", text_align="center"),
        padding="1.25rem",
        background="white",
        border_radius="16px",
        border="1px solid rgba(15, 118, 110, 0.2)",
        style={"flex": "1 1 22%", "minWidth": "200px"},
        align="center",
        height="100%",
    )

def bpmn_element_svg(icon: rx.Component, name: str, description: str, wide: bool = False) -> rx.Component:
    return rx.hstack(
        rx.el.span(
            icon,
            class_name="bpmn-icon" + (" bpmn-icon-wide" if wide else ""),
        ),
        rx.vstack(
            rx.text(name, font_weight="bold", font_size="1.25rem"),
            rx.text(description, font_size="1.15rem", color="#1a1a1a"),
            align="start",
            gap="0.25rem",
        ),
        padding="0.75rem",
        style={"flex": "1 1 45%", "minWidth": "300px"},
        align="center",
        gap="1rem",
    )


def bpmn_legend_item(icon: rx.Component, label: str, wide: bool = False) -> rx.Component:
    return rx.hstack(
        rx.el.span(
            icon,
            class_name="bpmn-icon" + (" bpmn-icon-wide" if wide else ""),
        ),
        rx.text(label),
        class_name="bpmn-legend-item",
        align="center",
    )


def beispiel_content() -> rx.Component:
    import os
    bpmn_file_path = os.path.join(os.path.dirname(os.path.dirname(__file__)), "assets", "beispiel_prozess.bpmn")
    with open(bpmn_file_path, "r", encoding="utf-8") as f:
        bpmn_xml = f.read()
    
    escaped_bpmn = bpmn_xml.replace('`', '\\`')
    iframe_html = f'''
    <!DOCTYPE html>
    <html>
        <head>
            <link rel="stylesheet" href="https://unpkg.com/bpmn-js@18.3.1/dist/assets/bpmn-js.css" />
            <script src="https://unpkg.com/bpmn-js@18.3.1/dist/bpmn-navigated-viewer.development.js"></script>
            <style>
                html, body {{
                    margin: 0;
                    padding: 0;
                    overflow: hidden;
                    width: 100%;
                    height: 100%;
                }}
                #canvas {{
                    width: 100%;
                    height: 100%;
                    background: white;
                }}
                .bjs-container {{
                    width: 100% !important;
                    height: 100% !important;
                }}
            </style>
        </head>
        <body>
            <div id="canvas"></div>
            <script>
                (async function(){{
                    var viewer = new BpmnJS({{ container: '#canvas' }});
                    var bpmn = `{escaped_bpmn}`;
                    try {{
                        await viewer.importXML(bpmn);
                        var canvas = viewer.get('canvas');
                        canvas.zoom('fit-viewport', 'auto');
                    }} catch (err) {{
                        console.error('Error loading BPMN:', err);
                        document.body.innerHTML = '<p style="padding: 1rem; color: red;">Fehler beim Laden des Diagramms</p>';
                    }}
                }})();
            </script>
        </body>
    </html>
    '''
    
    return rx.vstack(
        rx.heading("Beispiel: Auftragsannahme im Handwerksbetrieb", size="5", color="#0f766e", class_name="section-title"),
        rx.text(
            "So könnte ein typischer Auftragsannahme-Prozess in Ihrem Betrieb aussehen:",
            color="#4a5568",
            font_size="1.1rem",
        ),
        rx.el.iframe(
            src_doc=iframe_html,
            style={
                "width": "100%",
                "height": "450px",
                "border": "1px solid #e2e8f0",
                "borderRadius": "12px",
                "background": "white",
            },
        ),
        rx.heading("Welche Symbole gibt es?", size="5", color="#0f766e", margin_top="1.5rem", margin_bottom="1rem", class_name="section-title"),
        rx.box(
            bpmn_element_svg(
                rx.el.svg(
                    rx.el.circle(cx="18", cy="18", r="12", class_name="bpmn-start"),
                    view_box="0 0 36 36",
                ),
                "Start-Ereignis",
                "Markiert den Beginn eines Prozesses",
            ),
            bpmn_element_svg(
                rx.el.svg(
                    rx.el.circle(cx="18", cy="18", r="12", class_name="bpmn-end"),
                    rx.el.circle(cx="18", cy="18", r="9.5", class_name="bpmn-end-outer"),
                    view_box="0 0 36 36",
                ),
                "End-Ereignis",
                "Markiert das Ende eines Prozesses",
            ),
            bpmn_element_svg(
                rx.el.svg(
                    rx.el.rect(x="4", y="6", width="28", height="20", rx="3", class_name="bpmn-task"),
                    view_box="0 0 36 36",
                ),
                "Task/Aufgabe",
                "Eine auszuführende Aktivität",
            ),
            bpmn_element_svg(
                rx.el.svg(
                    rx.el.rect(x="4", y="8", width="28", height="20", rx="2", class_name="bpmn-message"),
                    rx.el.path(d="M4 8l14 10 14-10", class_name="bpmn-message"),
                    view_box="0 0 36 36",
                ),
                "Nachricht",
                "Kommunikation zwischen Beteiligten",
            ),
            bpmn_element_svg(
                rx.el.svg(
                    rx.el.polygon(points="18,3 33,18 18,33 3,18", class_name="bpmn-gateway"),
                    rx.el.path(d="M12 12l12 12 M24 12l-12 12", class_name="bpmn-gateway-mark"),
                    view_box="0 0 36 36",
                ),
                "Exklusives Gateway",
                "Entweder/Oder-Entscheidung",
            ),
            bpmn_element_svg(
                rx.el.svg(
                    rx.el.polygon(points="18,3 33,18 18,33 3,18", class_name="bpmn-gateway"),
                    rx.el.path(d="M18 8v20 M8 18h20", class_name="bpmn-gateway-mark"),
                    view_box="0 0 36 36",
                ),
                "Paralleles Gateway",
                "Parallele Pfade (alle werden ausgeführt)",
            ),
            bpmn_element_svg(
                rx.el.svg(
                    rx.el.line(x1="4", y1="18", x2="30", y2="18", class_name="bpmn-flow"),
                    rx.el.polygon(points="30,18 24,14 24,22", class_name="bpmn-flow"),
                    view_box="0 0 36 36",
                ),
                "Sequenzfluss",
                "Verbindet Elemente im Ablauf",
            ),
            bpmn_element_svg(
                rx.el.svg(
                    rx.el.path(d="M8 6h16l4 4v16H8z", class_name="bpmn-data"),
                    rx.el.path(d="M24 6v4h4z", class_name="bpmn-data-fold"),
                    view_box="0 0 36 36",
                ),
                "Datenobjekt",
                "Daten die im Prozess verwendet werden",
            ),
            display="flex",
            flex_wrap="wrap",
            gap="1rem",
            width="100%",
        ),
        align="start",
        width="100%",
        gap="1rem",
    )


def funktioniert_content() -> rx.Component:
    return rx.vstack(
        rx.heading("So nutzen Sie den BPMN Generator", size="5", color="#0f766e", class_name="section-title"),
        rx.ordered_list(
            rx.list_item(
                rx.text(
                    rx.text.strong("Prozess auswählen: "),
                    "Wählen sie den Prozess, den Sie modellieren möchten",
                    font_size="1.2rem",
                ),
            ),
            rx.list_item(
                rx.text(
                    rx.text.strong("Interview starten: "),
                    "Beantworten Sie die Fragen zu Ihrem Prozess: Sie können tippen oder per Spracheingabe antworten",
                    font_size="1.2rem",
                ),
            ),
            rx.list_item(
                rx.text(
                    rx.text.strong("BPMN generieren: "),
                    "Ihr Prozessmodell wird automatisch erstellt und visualisiert",
                    font_size="1.2rem",
                ),
            ),
            rx.list_item(
                rx.text(
                    rx.text.strong("Exportieren: "),
                    "Sie können das BPMN-Diagramm als XML-Datei herunterladen ",
                    font_size="1.2rem",
                ),
            ),
            margin_left="1.5rem",
            style={"fontSize": "1.2rem", "lineHeight": "1.8"},
        ),
        gap="1.5rem",
        align="start",
        width="100%",
    )


def chat_message(msg: Dict[str, Any]) -> rx.Component:
    is_assistant = msg["role"] == "assistant"
    return rx.box(
        rx.vstack(
            rx.cond(
                msg.get("title", "") != "",
                rx.text(
                    msg.get("title", ""),
                    size="2",
                    color="#0f766e",
                    font_weight="bold",
                ),
            ),
            rx.text(
                msg["content"],
                color="#1a1a1a",
                font_size="1.1rem",
                line_height="1.6",
            ),
            gap="0.35rem",
            align="start",
        ),
        padding="1.25rem 1.5rem",
        background=rx.cond(is_assistant, "rgba(15, 118, 110, 0.08)", "white"),
        border_radius="20px",
        margin_y="0.35rem",
        border="1px solid",
        border_color=rx.cond(is_assistant, "rgba(15, 118, 110, 0.2)", "#e2e8f0"),
        max_width="85%",
        margin_left=rx.cond(is_assistant, "0", "auto"),
        margin_right=rx.cond(is_assistant, "auto", "0"),
    )


def interview_section() -> rx.Component:
    return rx.box(
        rx.vstack(
            rx.hstack(
                rx.icon("message_circle", size=20, color="#0f766e"),
                rx.heading("Geführtes Interview", size="5", color="#0f766e"),
                align="center",
                gap="0.5rem",
            ),
            rx.cond(
                ~BPMNState.is_interview_active & ~BPMNState.interview_complete & (BPMNState.dialog_history.length() == 0),
                rx.vstack(
                    rx.text(
                        "Wählen Sie zuerst den Prozess, den sie modellieren möchten:",
                        color="#718096",
                        size="5",
                    ),
                    rx.select(
                        ["Angebots- und Auftragserstellung", "Personalmanagement", "Materialplanung"],
                        placeholder="Prozesstyp auswählen...",
                        value=BPMNState.process_type,
                        on_change=BPMNState.set_process_type,
                        width="100%",
                        size="3",
                        style={"fontSize": "1.2rem", "padding": "0.75rem"},
                    ),
                    rx.button(
                        rx.cond(
                            BPMNState.is_loading,
                            rx.hstack(
                                rx.spinner(size="2"),
                                rx.text("Fragen werden geladen..."),
                                gap="0.5rem",
                                align="center",
                            ),
                            rx.hstack(
                                rx.icon("play", size=18),
                                rx.text("Interview starten"),
                                gap="0.5rem",
                                align="center",
                            ),
                        ),
                        on_click=BPMNState.start_interview,
                        color_scheme="teal",
                        size="4",
                        disabled=(BPMNState.process_type == "") | BPMNState.is_loading,
                    ),
                    gap="1.25rem",
                    align="start",
                    width="100%",
                ),
            ),
            rx.cond(
                BPMNState.is_interview_active | BPMNState.interview_complete,
                rx.hstack(
                    rx.badge(BPMNState.process_type, color_scheme="teal"),
                    rx.cond(
                        BPMNState.generation_step != "",
                        rx.hstack(
                            rx.spinner(size="1"),
                            rx.text(BPMNState.generation_step, size="2", color="#475569"),
                            gap="0.5rem",
                            padding="0.25rem 0.5rem",
                            background="rgba(15, 118, 110, 0.08)",
                            border_radius="999px",
                        ),
                    ),
                    gap="1rem",
                    flex_wrap="wrap",
                ),
            ),
            rx.cond(
                BPMNState.dialog_history.length() > 0,
                rx.box(
                    rx.foreach(
                        BPMNState.dialog_history,
                        chat_message,
                    ),
                    max_height="350px",
                    overflow_y="auto",
                    width="100%",
                    padding="1rem",
                    background="#f7fafc",
                    border_radius="md",
                    id="chat-history",
                ),
            ),
            rx.cond(
                BPMNState.is_interview_active,
                rx.vstack(
                    rx.hstack(
                        rx.cond(
                            ~BPMNState.is_recording & ~BPMNState.is_transcribing,
                            rx.button(
                                rx.icon("mic", size=20),
                                "Aufnahme starten",
                                on_click=[
                                    BPMNState.start_recording,
                                    rx.call_script(
                                        """
                                        (() => {
                                            if (!window.audioRecorder || !window.audioRecorder.start) {
                                                return "Audio Recorder nicht geladen";
                                            }
                                            return window.audioRecorder.start().then((ok) => {
                                                if (ok) return "";
                                                return window.audioRecorder.getLastError?.() || "Aufnahme fehlgeschlagen";
                                            });
                                        })()
                                        """,
                                        callback=BPMNState.handle_recording_error,
                                    )
                                ],
                                color_scheme="teal",
                                size="4",
                                disabled=BPMNState.is_loading,
                            ),
                        ),
                        rx.cond(
                            BPMNState.is_recording,
                            rx.hstack(
                                rx.box(
                                    width="12px",
                                    height="12px",
                                    background="red",
                                    border_radius="50%",
                                    class_name="pulse-animation",
                                ),
                                rx.el.span(
                                    "00:00",
                                    id="recording-duration",
                                    style={
                                        "font_family": "monospace",
                                        "font_size": "1.25rem",
                                        "font_weight": "bold",
                                        "color": "#e53e3e",
                                        "min_width": "60px",
                                    },
                                ),
                                rx.button(
                                    rx.icon("square", size=20),
                                    rx.cond(
                                        BPMNState.is_saving_answer,
                                        "Antwort wird gespeichert",
                                        "Aufnahme stoppen",
                                    ),
                                    on_click=[
                                        BPMNState.prepare_stop,
                                        rx.call_script(
                                            """
                                            (() => {
                                                try {
                                                    if (window.audioRecorder && window.audioRecorder.stopSync) {
                                                        window.audioRecorder.stopSync();
                                                    }
                                                    return new Promise((resolve) => {
                                                        const start = Date.now();
                                                        const timer = setInterval(() => {
                                                            const err = window.audioRecorder?.getLastError?.();
                                                            if (err) {
                                                                clearInterval(timer);
                                                                resolve(JSON.stringify({ error: String(err) }));
                                                                return;
                                                            }
                                                            const data = window.audioRecorder?.getLastRecording?.();
                                                            if (data) {
                                                                clearInterval(timer);
                                                                resolve(data);
                                                                return;
                                                            }
                                                            if (Date.now() - start > 15000) {
                                                                clearInterval(timer);
                                                                resolve(JSON.stringify({ error: "Timeout beim Lesen der Audiodaten" }));
                                                            }
                                                        }, 200);
                                                    });
                                                } catch (err) {
                                                    return JSON.stringify({ error: String(err) });
                                                }
                                            })()
                                            """,
                                            callback=BPMNState.stop_recording_and_transcribe,
                                        ),
                                    ],
                                    color_scheme="red",
                                    size="3",
                                    disabled=BPMNState.is_saving_answer,
                                ),
                                gap="0.75rem",
                                align="center",
                            ),
                        ),
                        gap="1rem",
                        justify="center",
                        width="100%",
                    ),
                    rx.hstack(
                        rx.input(
                            placeholder="Ihre Antwort...",
                            value=BPMNState.answer_input,
                            on_change=BPMNState.set_answer_input,
                            width="100%",
                            size="3",
                            height="48px",
                            disabled=BPMNState.is_loading | BPMNState.is_recording | BPMNState.is_transcribing,
                        ),
                        rx.button(
                            rx.icon("send", size=20),
                            rx.cond(
                                BPMNState.is_transcribing,
                                "Verarbeite...",
                                rx.cond(
                                    BPMNState.is_saving_answer,
                                    "Speichere...",
                                    "",
                                ),
                            ),
                            on_click=[BPMNState.prepare_text_submit, BPMNState.submit_answer],
                            color_scheme="teal",
                            size="3",
                            height="48px",
                            padding_x="1.25rem",
                            loading=BPMNState.is_saving_answer | BPMNState.is_transcribing,
                            disabled=BPMNState.is_recording | BPMNState.is_transcribing | BPMNState.is_saving_answer,
                        ),
                        width="100%",
                        gap="0.5rem",
                    ),
                    width="100%",
                    gap="0.75rem",
                    align="center",
                ),
            ),
            rx.cond(
                BPMNState.interview_complete,
                rx.vstack(
                    rx.hstack(
                        rx.button(
                            rx.icon("sparkles", size=16),
                            rx.cond(
                                BPMNState.is_loading,
                                "Prozessmodell wird erstellt...",
                                "BPMN generieren",
                            ),
                            on_click=[BPMNState.prepare_generation, BPMNState.generate_bpmn],
                            color_scheme="teal",
                            size="3",
                            disabled=BPMNState.is_loading,
                        ),
                        rx.button(
                            rx.icon("rotate_ccw", size=16),
                            "Neu starten",
                            on_click=BPMNState.reset_interview,
                            variant="outline",
                            color_scheme="gray",
                            disabled=BPMNState.is_loading,
                        ),
                        gap="1rem",
                        flex_wrap="wrap",
                    ),
                    gap="0.75rem",
                    align="start",
                ),
            ),
            width="100%",
            gap="1rem",
            align="start",
        ),
        padding="2rem",
        background="rgba(255,255,255,0.92)",
        border_radius="20px",
        box_shadow="var(--shadow)",
        border="1px solid rgba(15, 23, 42, 0.08)",
        width="100%",
        class_name="glass-panel",
    )


def live_summary_section() -> rx.Component:
    return rx.box(
        rx.vstack(
            rx.hstack(
                rx.icon("file_text", size=20, color="#0f766e"),
                rx.heading("Live-Zusammenfassung", size="5", color="#0f766e"),
                align="center",
                gap="0.5rem",
            ),
            rx.divider(margin_y="0.5rem"),
            rx.box(
                rx.cond(
                    BPMNState.interview_summary != "",
                    rx.text(
                        BPMNState.interview_summary,
                        color="#4a5568",
                        font_size="1.1rem",
                        line_height="1.7",
                        white_space="pre-wrap",
                    ),
                    rx.text(
                        "Noch keine Zusammenfassung vorhanden.",
                        color="#a0aec0",
                        font_size="1rem",
                    ),
                ),
                overflow_y="auto",
                width="100%",
                padding="0.5rem",
                background="#f7fafc",
                border_radius="md",
                height="100%",
            ),
            width="100%",
            gap="0.5rem",
            align="start",
            height="100%",
        ),
        padding="1.75rem",
        background="rgba(255,255,255,0.92)",
        border_radius="20px",
        box_shadow="var(--shadow)",
        border="1px solid rgba(15, 23, 42, 0.08)",
        width="100%",
        height="100%",
        class_name="glass-panel",
    )


def input_section() -> rx.Component:
    return rx.vstack(
        rx.hstack(
            rx.box(
                interview_section(),
                width="50%",
                min_width="360px",
                style={"flex": "1 1 0"},
                class_name="interview-panel",
            ),
            rx.box(
                live_summary_section(),
                width="50%",
                min_width="360px",
                style={"flex": "1 1 0"},
                class_name="summary-panel",
            ),
            align="stretch",
            width="100%",
            gap="1.5rem",
            flex_wrap="wrap",
            height="100%",
        ),
        rx.cond(
            BPMNState.error_message != "",
            rx.callout(
                BPMNState.error_message,
                icon="triangle_alert",
                color_scheme="red",
            ),
        ),
        rx.cond(
            BPMNState.success_message != "",
            rx.callout(
                BPMNState.success_message,
                icon="circle_check",
                color_scheme="green",
            ),
        ),
        width="100%",
        gap="1.5rem",
    )


def output_section() -> rx.Component:
    return rx.cond(
        BPMNState.bpmn_xml != "",
        rx.vstack(
            rx.hstack(
                rx.icon("circle_check", size=28, color="#0f766e"),
                rx.heading("Generiertes BPMN", size="7", color="#0f766e"),
                align="center",
                gap="0.5rem",
            ),
            # BPMN Viewer - volle Breite
            rx.box(
                rx.el.iframe(
                    src_doc=BPMNState.bpmn_viewer_html,
                    style={
                        "width": "100%",
                        "height": "500px",
                        "border_radius": "16px",
                        "border": "1px solid rgba(15, 23, 42, 0.12)",
                        "background": "white",
                    },
                ),
                width="100%",
            ),
            rx.heading("Legende", size="5", color="#0f766e", margin_top="1.5rem", margin_bottom="1rem", class_name="section-title"),
            rx.box(
                bpmn_element_svg(
                    rx.el.svg(
                        rx.el.circle(cx="18", cy="18", r="12", class_name="bpmn-start"),
                        view_box="0 0 36 36",
                    ),
                    "Start-Ereignis",
                    "Markiert den Beginn eines Prozesses",
                ),
                bpmn_element_svg(
                    rx.el.svg(
                        rx.el.circle(cx="18", cy="18", r="12", class_name="bpmn-end"),
                        rx.el.circle(cx="18", cy="18", r="9.5", class_name="bpmn-end-outer"),
                        view_box="0 0 36 36",
                    ),
                    "End-Ereignis",
                    "Markiert das Ende eines Prozesses",
                ),
                bpmn_element_svg(
                    rx.el.svg(
                        rx.el.rect(x="4", y="6", width="28", height="20", rx="3", class_name="bpmn-task"),
                        view_box="0 0 36 36",
                    ),
                    "Task/Aufgabe",
                    "Eine auszuführende Aktivität",
                ),
                bpmn_element_svg(
                    rx.el.svg(
                        rx.el.rect(x="4", y="8", width="28", height="20", rx="2", class_name="bpmn-message"),
                        rx.el.path(d="M4 8l14 10 14-10", class_name="bpmn-message"),
                        view_box="0 0 36 36",
                    ),
                    "Nachricht",
                    "Kommunikation zwischen Beteiligten",
                ),
                bpmn_element_svg(
                    rx.el.svg(
                        rx.el.polygon(points="18,3 33,18 18,33 3,18", class_name="bpmn-gateway"),
                        rx.el.path(d="M12 12l12 12 M24 12l-12 12", class_name="bpmn-gateway-mark"),
                        view_box="0 0 36 36",
                    ),
                    "Exklusives Gateway",
                    "Entweder/Oder-Entscheidung",
                ),
                bpmn_element_svg(
                    rx.el.svg(
                        rx.el.polygon(points="18,3 33,18 18,33 3,18", class_name="bpmn-gateway"),
                        rx.el.path(d="M18 8v20 M8 18h20", class_name="bpmn-gateway-mark"),
                        view_box="0 0 36 36",
                    ),
                    "Paralleles Gateway",
                    "Parallele Pfade (alle werden ausgeführt)",
                ),
                bpmn_element_svg(
                    rx.el.svg(
                        rx.el.line(x1="4", y1="18", x2="30", y2="18", class_name="bpmn-flow"),
                        rx.el.polygon(points="30,18 24,14 24,22", class_name="bpmn-flow"),
                        view_box="0 0 36 36",
                    ),
                    "Sequenzfluss",
                    "Verbindet Elemente im Ablauf",
                ),
                bpmn_element_svg(
                    rx.el.svg(
                        rx.el.path(d="M8 6h16l4 4v16H8z", class_name="bpmn-data"),
                        rx.el.path(d="M24 6v4h4z", class_name="bpmn-data-fold"),
                        view_box="0 0 36 36",
                    ),
                    "Datenobjekt",
                    "Daten die im Prozess verwendet werden",
                ),
                display="flex",
                flex_wrap="wrap",
                gap="1rem",
                width="100%",
            ),
            rx.hstack(
                rx.button(
                    rx.icon("download", size=16),
                    "XML herunterladen",
                    color_scheme="teal",
                    variant="solid",
                    on_click=rx.call_script(
                        """
                        (() => {
                            const xmlEl = document.getElementById("bpmn-xml");
                            if (!xmlEl) return;
                            const xml = xmlEl.textContent || "";
                            const blob = new Blob([xml], { type: "application/xml" });
                            const url = URL.createObjectURL(blob);
                            const a = document.createElement("a");
                            a.href = url;
                            a.download = "prozessmodell.bpmn";
                            document.body.appendChild(a);
                            a.click();
                            a.remove();
                            URL.revokeObjectURL(url);
                        })();
                        """
                    ),
                ),
                rx.button(
                    rx.icon("rotate_ccw", size=16),
                    "Neuen Prozess",
                    on_click=BPMNState.reset_interview,
                    color_scheme="gray",
                    variant="outline",
                ),
                gap="1rem",
                flex_wrap="wrap",
                margin_top="1rem",
            ),
            rx.el.pre(
                BPMNState.bpmn_xml,
                id="bpmn-xml",
                style={"display": "none"},
            ),
            width="100%",
            gap="1rem",
            padding="2rem",
            background="rgba(255,255,255,0.92)",
            border_radius="20px",
            box_shadow="var(--shadow)",
            border="1px solid rgba(15, 23, 42, 0.08)",
            class_name="glass-panel",
        ),
        rx.box(
            rx.vstack(
                rx.icon("file_text", size=48, color="#a0aec0"),
                rx.text(
                    "Noch kein BPMN generiert",
                    color="#718096",
                    size="4",
                    font_weight="medium",
                ),
                rx.text(
                    "Führen Sie das Interview durch oder geben Sie eine Prozessbeschreibung ein",
                    color="#a0aec0",
                    size="2",
                    text_align="center",
                ),
                rx.button(
                    "Zur Eingabe",
                    on_click=lambda: BPMNState.set_active_tab("input"),
                    color_scheme="teal",
                    variant="outline",
                    margin_top="1rem",
                ),
                gap="0.75rem",
                align="center",
                padding="3rem",
            ),
            width="100%",
            background="rgba(255,255,255,0.92)",
            border_radius="20px",
            box_shadow="var(--shadow)",
            border="1px solid rgba(15, 23, 42, 0.08)",
            class_name="glass-panel",
        ),
    )


def main_content() -> rx.Component:
    return rx.box(
        rx.tabs.root(
            rx.tabs.list(
                rx.tabs.trigger(
                    rx.hstack(
                        rx.icon("pencil", size=20),
                        "Eingabe",
                        gap="0.75rem",
                    ),
                    value="input",
                    font_size="1.1rem",
                    padding="0.75rem 1.5rem",
                ),
                rx.tabs.trigger(
                    rx.hstack(
                        rx.icon("file_down", size=20),
                        "Ergebnis",
                        gap="0.75rem",
                    ),
                    value="output",
                    font_size="1.1rem",
                    padding="0.75rem 1.5rem",
                ),
                justify="start",
                gap="1rem",
            ),
            rx.tabs.content(
                input_section(),
                value="input",
                padding_top="1.5rem",
            ),
            rx.tabs.content(
                output_section(),
                value="output",
                padding_top="1.5rem",
            ),
            value=BPMNState.active_tab,
            on_change=BPMNState.set_active_tab,
            color_scheme="teal",
            width="100%",
        ),
        width="100%",
    )


def footer() -> rx.Component:
    return rx.box(
        rx.hstack(
            rx.text(
                color="#94a3b8",
                font_size="0.7rem",
                line_height="1",
            ),
            rx.spacer(),
            rx.hstack(
                rx.link(
                    "Hilfe",
                    href="#",
                    color="#94a3b8",
                    font_size="0.7rem",
                    line_height="1",
                ),
                rx.link(
                    "Datenschutz",
                    href="#",
                    color="#94a3b8",
                    font_size="0.7rem",
                    line_height="1",
                ),
                gap="0.75rem",
            ),
            width="100%",
            padding_x="2rem",
            align="center",
            height="100%",
        ),
        border_top="1px solid rgba(15, 23, 42, 0.05)",
        width="100%",
        background="rgba(248, 250, 252, 0.95)",
        height="28px",
        min_height="28px",
        max_height="28px",
        position="fixed",
        bottom="0",
        left=rx.cond(BPMNState.sidebar_open, "320px", "0"),
        right="0",
        z_index="50",
        transition="left 0.2s ease-out",
    )

def index() -> rx.Component:
    return rx.fragment(
        rx.el.link(rel="stylesheet", href="/styles.css"),
        rx.script(src="/audio_recorder.js"),
        sidebar_panel(),
        rx.box(
            header(),
            rx.vstack(
                main_content(),
                rx.cond(
                    BPMNState.active_tab != "output",
                    info_tabs(),
                ),
                width="100%",
                gap="2rem",
                padding_y="1.5rem",
                padding_x="2.5rem",
                padding_bottom="3rem",
                class_name="page-container",
            ),
            footer(),
            background="transparent",
            min_height="100vh",
            margin_left=rx.cond(BPMNState.sidebar_open, "320px", "0"),
            transition="margin-left 0.2s ease-out",
        ),
    )

app = rx.App(
    theme=rx.theme(
        accent_color="teal",
        gray_color="slate",
        radius="medium",
    ),
)

app.add_page(index, route="/", title="BPMN Generator - Für das Handwerk")
