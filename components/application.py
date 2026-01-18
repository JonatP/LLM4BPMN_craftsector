import streamlit as st
import streamlit.components.v1 as components
from openai import OpenAI
from components.BPMNGenerator import BPMNGenerator
from components.SignavioConnector import (
    SignavioConnector,
    AuthenticationError,
    UploadError,
    ExportError,
)
from io import BytesIO
import xml.etree.ElementTree as ET
import subprocess
import tempfile
import os
import html
import json


# Load localization
@st.cache_data
def load_localization(lang="de"):
    """Load localization texts from JSON file"""
    try:
        with open(f"locales/{lang}/{lang}.json", "r", encoding="utf-8") as f:
            return json.load(f)
    except Exception as e:
        st.error(f"Error loading localization: {e}")
        return {}


def load_api_key():
    """Load OpenAI API key from streamlit"""
    api_key = st.secrets.get("openai-api-key")
    return api_key if api_key else ''


def run_app():
    # -------------------------
    # Load texts + page config
    # -------------------------
    texts = load_localization("de")

    st.set_page_config(
        layout="wide",
        page_title=texts.get("app", {}).get("title", "BPMN Generator"),
        initial_sidebar_state="collapsed",
    )

    # Load external CSS
    with open('css/styles.css', 'r', encoding='utf-8') as f:
        css = f.read()
    
    st.markdown(f'<style>{css}</style>', unsafe_allow_html=True)

    # -------------------------
    # Session State defaults
    # -------------------------
    st.session_state.setdefault("final_bpmn", None)
    st.session_state.setdefault("visualization_ready", False)
    st.session_state.setdefault("show_signavio_export", False)
    st.session_state.setdefault("show_mermaid", False)
    st.session_state.setdefault("mermaid_code", "")

    st.session_state.setdefault("show_dialog", False)
    st.session_state.setdefault("dialog_step", 0)
    st.session_state.setdefault("dialog_answers", {})
    st.session_state.setdefault("process_description", "")
    st.session_state.setdefault("validation_feedback", {})
    st.session_state.setdefault("validation_passed", False)
    st.session_state.setdefault("scroll_to_dialog", False)
    st.session_state.setdefault("validated_current", False)

    # -------------------------
    # Helper functions
    # -------------------------
    def clear_session():
        st.session_state["final_bpmn"] = None
        st.session_state["visualization_ready"] = False

    def clear_all_session():
        st.session_state["final_bpmn"] = None
        st.session_state["visualization_ready"] = False
        st.session_state["show_dialog"] = False
        st.session_state["dialog_step"] = 0
        st.session_state["dialog_answers"] = {}
        st.session_state["process_description"] = ""
        st.session_state["validation_feedback"] = {}
        st.session_state["validation_passed"] = False
        st.session_state["show_signavio_export"] = False
        st.session_state["show_mermaid"] = False
        st.session_state["mermaid_code"] = ""
        st.session_state["validated_current"] = False

    def start_dialog():
        st.session_state["show_dialog"] = True
        st.session_state["dialog_step"] = 0
        st.session_state["dialog_answers"] = {}
        st.session_state["validation_feedback"] = {}
        st.session_state["validated_current"] = False
        st.session_state["validation_passed"] = False
        st.session_state["scroll_to_dialog"] = True

    def next_question():
        st.session_state["dialog_step"] = st.session_state.get("dialog_step", 0) + 1

    def previous_question():
        st.session_state["validated_current"] = False
        st.session_state["dialog_step"] -= 1

    def validate_single_answer(api_key, question_key, answer_text):
        """
        Validate a single answer using a focused prompt tailored to one question.
        NOTE: If your prompt file contains JSON braces { } you must escape them for .format().
        """
        try:
            client = OpenAI(api_key=api_key)
            q_text = next((q["question"] for q in questions if q["key"] == question_key), question_key)

            prompt_path = "input/prompts/validation-prompt.txt"
            with open(prompt_path, "r", encoding="utf-8") as f:
                template = f.read()

            single_prompt = template.format(question=q_text, answer=answer_text)

            response = client.chat.completions.create(
                model="gpt-5.2",
                messages=[
                    {
                        "role": "system",
                        "content": "Du antwortest ausschließlich mit validem JSON. Keine Erklärungen oder zusätzlicher Text.",
                    },
                    {"role": "user", "content": single_prompt},
                ],
                temperature=0.1,
                max_completion_tokens=800,
            )

            response_text = response.choices[0].message.content.strip()

            if response_text.startswith("```json"):
                response_text = response_text[7:]
            if response_text.endswith("```"):
                response_text = response_text[:-3]
            response_text = response_text.strip()

            result = json.loads(response_text)

            if "feedback" not in result:
                result["feedback"] = {
                    "status": "verbesserung_nötig",
                    "feedback": "Keine Bewertung erhalten.",
                    "suggestions": "Bitte Antwort prüfen.",
                }

            return result

        except Exception as e:
            print(f"Single validation error: {e}")
            return {
                "overall_quality": "mittel",
                "ready_for_bpmn": False,
                "feedback": {
                    "status": "verbesserung_nötig",
                    "feedback": "Validierung fehlgeschlagen.",
                    "suggestions": "Bitte überprüfen Sie die Antwort manuell.",
                },
                "general_feedback": f"Validierung für {question_key} fehlgeschlagen: {e}",
            }

    def complete_dialog():
        answers = st.session_state["dialog_answers"]
        description = f"""Prozessbeschreibung basierend auf Benutzerangaben:

1. Prozessname und Zweck: {answers.get('question1', '').strip()}
2. Beteiligte Akteure/Rollen: {answers.get('question2', '').strip()}
3. Eingangsdaten und Auslöser: {answers.get('question3', '').strip()}
4. Hauptschritte des Prozesses: {answers.get('question4', '').strip()}
5. Prozessende und Output: {answers.get('question5', '').strip()}"""

        st.session_state["process_description"] = description.strip()
        st.session_state["show_dialog"] = False
        st.session_state["dialog_step"] = 0
        st.session_state["validation_passed"] = True

    def show_summary():
        st.session_state["dialog_step"] = len(questions)

    # -------------------------
    # Questions from localization
    # -------------------------
    questions = []
    for i, q in enumerate(texts.get("questions", {}).get("questions_list", [])):
        questions.append(
            {
                "title": q.get("title", f"Schritt {i+1} von 5"),
                "question": q.get("question", ""),
                "placeholder": q.get("placeholder", ""),
                "key": f"question{i+1}",
            }
        )

    # -------------------------
    # Header + Layout
    # -------------------------
    st.markdown(
        f"""
        <div class="main-header">
            <h1 class="main-title">{texts.get("app", {}).get("title","BPMN Generator")}</h1>
            <p class="main-subtitle">{texts.get("app", {}).get("subtitle","")}</p>
        </div>
        """,
        unsafe_allow_html=True,
    )

    col1, col2 = st.columns([0.35, 0.65], gap="large")

    # -------------------------
    # Left Column
    # -------------------------
    with col1:
        st.markdown(
            f'<h3 class="card-header">{texts.get("app", {}).get("configuration","Konfiguration")}</h3>',
            unsafe_allow_html=True,
        )

        with st.expander(texts.get("privacy", {}).get("title", "Datenschutzhinweis"), expanded=False):
            st.markdown(
                f"""
                <div class="privacy-notice privacy-expander">
                    <br>
                    {texts.get("privacy", {}).get("text","")}
                    <br><br>
                </div>
                """,
                unsafe_allow_html=True,
            )

        # API Key von JSON-Datei laden
        api_key = load_api_key()
        
        # Manuelle API-Key Eingabe (auskommentiert - wird jetzt aus api-key.json geladen)
        # api_key = st.text_input(
        #     texts.get("api_key", {}).get("label", "OpenAI API Schlüssel"),
        #     type="password",
        #     help=texts.get("api_key", {}).get("help", ""),
        #     on_change=clear_session,
        # )

        st.markdown(
            f'<h3 class="card-header">{texts.get("app", {}).get("process_description","Prozessbeschreibung")}</h3>',
            unsafe_allow_html=True,
        )

        if st.session_state["process_description"] and st.session_state["validation_passed"]:
            st.success(texts.get("questions", {}).get("complete_message", "Prozessbeschreibung erfasst!"))
            with st.expander(texts.get("questions", {}).get("show_answers", "Antworten anzeigen")):
                st.markdown(f"```\n{st.session_state['process_description']}\n```")
            if st.button(
                texts.get("questions", {}).get("restart_button", "Erneut ausfüllen"),
                type="secondary",
                use_container_width=True,
            ):
                start_dialog()
                st.rerun()
        else:
            st.markdown(f"**{texts.get('questions', {}).get('title','Beantworten Sie 5 Fragen zu Ihrem Prozess:')}**")
            if st.button(
                texts.get("questions", {}).get("start_button", "Fragen starten"),
                type="primary",
                use_container_width=True,
            ):
                start_dialog()
                st.rerun()

        input_available = bool(st.session_state["process_description"]) and st.session_state["validation_passed"]

        if input_available:
            if st.button(
                texts.get("generation", {}).get("create_button", "BPMN-Diagramm erstellen"),
                type="primary",
                disabled=not api_key,
                use_container_width=True,
            ):
                text = st.session_state["process_description"]

                try:
                    bpmn_generator = BPMNGenerator(None, text, api_key)
                    with col2:
                        with st.spinner(texts.get("generation", {}).get("first_version", "Erste BPMN-Version wird generiert...")):
                            bpmn_generator.text_to_model()
                        with st.spinner(texts.get("generation", {}).get("improving", "BPMN-Modell wird verbessert...")):
                            bpmn_generator.improve_model()
                        with st.spinner(texts.get("generation", {}).get("checking", "Vollständigkeit wird geprüft...")):
                            complete = bpmn_generator.check_xml_completeness()

                        if not complete:
                            with st.spinner(texts.get("generation", {}).get("completing", "BPMN-Modell wird vervollständigt...")):
                                bpmn_di = bpmn_generator.generate_bpmn_di()
                                bpmn_generator.merge_bpmn_xml_diagram(bpmn_di)

                        st.session_state["final_bpmn"] = bpmn_generator.get_bpmn()
                        st.session_state["visualization_ready"] = True

                except Exception as e:
                    with col2:
                        st.error(f"{texts.get('generation', {}).get('completing','Fehler:')} {e}")
                    st.stop()

            if not api_key:
                st.caption(texts.get("generation", {}).get("api_key_required", "OpenAI API-Schlüssel erforderlich"))
        else:
            st.caption(texts.get("questions", {}).get("instruction", "Beantworten Sie zuerst die Fragen, um fortzufahren."))

    # -------------------------
    # Right Column (Output)
    # -------------------------
    with col2:
        st.markdown('<h3 class="card-header">Output</h3>', unsafe_allow_html=True)

        if st.session_state["visualization_ready"]:
            st.success(texts.get("generation", {}).get("success", "BPMN-Diagramm erfolgreich generiert!"))

            final_bpmn = st.session_state["final_bpmn"]
            escaped_bpmn = final_bpmn.replace("`", "\\`")

            components.html(
                f"""
                <html>
                    <head>
                        <link rel="stylesheet" href="https://unpkg.com/bpmn-js@18.3.1/dist/assets/bpmn-js.css" />
                        <script src="https://unpkg.com/bpmn-js@18.3.1/dist/bpmn-navigated-viewer.development.js"></script>
                        <style>
                            #canvas {{
                                width: 100%;
                                height: 650px;
                                border-radius: 8px;
                                border: 1px solid #e5e7eb;
                                overflow: hidden;
                            }}
                        </style>
                    </head>
                    <body>
                        <div id="canvas"></div>
                        <script>
                            (async function(){{
                                var viewer = new BpmnJS({{ container: '#canvas' }});
                                var bpmn = `{escaped_bpmn}`;
                                try{{
                                    await viewer.importXML(bpmn);
                                    viewer.get('canvas').zoom('fit-viewport');
                                }}catch (err) {{
                                    console.error('Error loading BPMN:', err);
                                }}
                            }})();
                        </script>
                    </body>
                </html>
                """,
                height=670,
            )

            st.markdown(f"### {texts.get('export', {}).get('title', 'Export-Optionen')}")
            col_xml, col_mermaid, col_signavio = st.columns([1, 1, 1])

            with col_xml:
                st.download_button(
                    label=texts.get("export", {}).get("xml_button", "Als XML herunterladen"),
                    data=st.session_state["final_bpmn"],
                    file_name="process.bpmn",
                    mime="application/xml",
                    use_container_width=True,
                )

            with col_mermaid:
                if st.button(texts.get("export", {}).get("mermaid_button", "Als Mermaid anzeigen"), use_container_width=True):
                    st.session_state["show_mermaid"] = True
                    st.rerun()

            with col_signavio:
                if st.button(
                    texts.get("export", {}).get("signavio_button", "Nach SAP Signavio exportieren"),
                    use_container_width=True,
                ):
                    st.session_state["show_signavio_export"] = True
                    st.rerun()

            if st.session_state.get("show_mermaid", False):
                st.markdown(f"### {texts.get('mermaid', {}).get('title', 'Mermaid-Diagramm')}")

                try:
                    with tempfile.NamedTemporaryFile(mode="w", suffix=".bpmn", delete=False) as tmp_bpmn:
                        tmp_bpmn.write(st.session_state["final_bpmn"])
                        tmp_bpmn_path = tmp_bpmn.name

                    result = subprocess.run(
                        ["python3", "bpmn2mermaid.py", tmp_bpmn_path, "-d", "LR"],
                        capture_output=True,
                        text=True,
                        cwd=os.getcwd(),
                    )

                    os.unlink(tmp_bpmn_path)

                    if result.returncode == 0:
                        mermaid_code = result.stdout

                        col_mermaid_view, col_mermaid_close = st.columns([4, 1])
                        with col_mermaid_close:
                            if st.button("Schließen", key="close_mermaid"):
                                st.session_state["show_mermaid"] = False
                                st.rerun()

                        st.text_area(
                            texts.get("mermaid", {}).get("code_label", "Mermaid Code:"),
                            value=mermaid_code,
                            height=320,
                            help=texts.get("mermaid", {}).get("code_help", ""),
                        )

                        mermaid_escaped = html.escape(mermaid_code)

                        components.html(
                            f"""
                            <!doctype html>
                            <html>
                                <head>
                                    <meta charset="utf-8" />
                                    <script src="https://cdn.jsdelivr.net/npm/mermaid@10/dist/mermaid.min.js"></script>
                                    <style>
                                        /* Larger container for better Mermaid visualization */
                                        #mermaid-diagram {{
                                            width: 100%;
                                            height: 800px;
                                            border: 1px solid #e5e7eb;
                                            border-radius: 8px;
                                            overflow: auto;
                                            background: white;
                                            padding: 20px;
                                        }}
                                        /* Make the generated SVG scale to container width while keeping height auto */
                                        #mermaid-diagram svg {{
                                            width: 100% !important;
                                            height: auto !important;
                                            display: block;
                                        }}
                                        /* Ensure inner mermaid div does not constrain layout */
                                        #mermaid-diagram .mermaid {{
                                            width: 100%;
                                        }}
                                    </style>
                                </head>
                                <body>
                                    <div id="mermaid-diagram"><div class="mermaid">{mermaid_escaped}</div></div>
                                    <script>
                                        (async () => {{
                                            mermaid.initialize({{
                                                startOnLoad: false,
                                                theme: "default",
                                                themeVariables: {{ fontSize: "18px" }},
                                                flowchart: {{ useMaxWidth: true, htmlLabels: false }}
                                            }});
                                            await mermaid.run();
                                            const host = document.getElementById("mermaid-diagram");
                                            const svg = host.querySelector("svg");
                                            if (svg && svg.viewBox && svg.viewBox.baseVal) {{
                                                svg.setAttribute("preserveAspectRatio", "xMinYMin meet");
                                            }}
                                        }})();
                                    </script>
                                </body>
                            </html>
                                """,
                                height=1000,
                        )
                    else:
                        st.error(f"{texts.get('mermaid', {}).get('conversion_error','Fehler bei Mermaid-Konvertierung:')} {result.stderr}")
                except Exception as e:
                    st.error(f"{texts.get('mermaid', {}).get('creation_error','Fehler beim Erstellen des Mermaid-Diagramms:')} {str(e)}")

            if st.session_state["show_signavio_export"]:
                with st.expander(texts.get("signavio", {}).get("title", "SAP Signavio Export-Konfiguration"), expanded=True):
                    st.markdown(f"**{texts.get('signavio', {}).get('credentials','Geben Sie Ihre SAP Signavio Zugangsdaten ein:')}**")

                    signavio_username = st.text_input(texts.get("signavio", {}).get("username", "Benutzername"), key="signavio_user")
                    signavio_password = st.text_input(
                        texts.get("signavio", {}).get("password", "Passwort"),
                        type="password",
                        key="signavio_pass",
                    )
                    signavio_instance = st.selectbox(
                        texts.get("signavio", {}).get("instance", "Instanz"),
                        ("https://academic.signavio.com", "https://editor.signavio.com"),
                        index=None,
                        placeholder=texts.get("signavio", {}).get("instance_placeholder", "Wählen Sie Ihre Instanz"),
                        key="signavio_inst",
                    )
                    signavio_workspace = st.text_input(
                        texts.get("signavio", {}).get("workspace_id", "Workspace ID"),
                        help=texts.get("signavio", {}).get("workspace_help", ""),
                        key="signavio_ws",
                    )
                    signavio_directory = st.text_input(
                        texts.get("signavio", {}).get("directory_id", "Zielverzeichnis ID"),
                        help=texts.get("signavio", {}).get("directory_help", ""),
                        key="signavio_dir",
                    )

                    col_export, col_cancel = st.columns([1, 1])
                    with col_export:
                        signavio_ready = all(
                            [signavio_username, signavio_password, signavio_instance, signavio_workspace, signavio_directory]
                        )
                        if st.button(
                            texts.get("signavio", {}).get("export_button", "Jetzt exportieren"),
                            type="primary",
                            disabled=not signavio_ready,
                            use_container_width=True,
                            key="do_export",
                        ):
                            try:
                                signavio_connector = SignavioConnector(
                                    signavio_instance,
                                    signavio_workspace,
                                    signavio_directory,
                                )

                                with st.spinner(texts.get("signavio", {}).get("authenticating", "SAP Signavio Authentifizierung...")):
                                    signavio_connector.authenticate(signavio_username, signavio_password)

                                with st.spinner(texts.get("signavio", {}).get("exporting", "Prozessmodell wird exportiert...")):
                                    signavio_connector.import_model(final_bpmn)

                                st.success(texts.get("signavio", {}).get("success"))
                                st.session_state["show_signavio_export"] = False

                                try:
                                    with st.spinner(texts.get("signavio", {}).get("loading_preview", "Vorschaubild wird geladen...")):
                                        png = signavio_connector.export_model()
                                    st.image(BytesIO(png), caption=texts.get("signavio", {}).get("preview", "Ihr Modell in SAP Signavio"))
                                except Exception:
                                    st.info(texts.get("signavio", {}).get("preview_error", "Vorschaubild konnte nicht geladen werden."))

                            except (AuthenticationError, UploadError, ExportError) as e:
                                st.error(f"{texts.get('signavio', {}).get('export_failed','Export fehlgeschlagen')}: {str(e)}")
                            except Exception as e:
                                st.error(f"{texts.get('signavio', {}).get('unexpected_error')}: {str(e)}")

                    with col_cancel:
                        if st.button("Abbrechen", use_container_width=True, key="cancel_export"):
                            st.session_state["show_signavio_export"] = False
                            st.rerun()

                    if not signavio_ready:
                        st.caption(texts.get("signavio", {}).get("all_fields_required"))
        else:
            st.markdown(
                """
                <div class="output-section">
                    <div class="output-placeholder">
                        <div class="output-placeholder-inner">
                            <div class="output-placeholder-title">Hier erscheint Ihr BPMN-Diagramm</div>
                            <div class="output-placeholder-text">
                                Beschreiben Sie Ihren Geschäftsprozess indem sie ein paar Fragen beantworten und klicken Sie auf
                                <strong>„BPMN-Diagramm erstellen"</strong>.<br><br>
                                Die Erstellung kann bis zu 60 Sekunden dauern.
                            </div>
                        </div>
                    </div>
                </div>
                """,
                unsafe_allow_html=True,
            )

    # -------------------------
    # Dialog Modal Implementation
    # -------------------------
    if st.session_state["show_dialog"]:
        st.markdown("<div style='height: 80px;'></div>", unsafe_allow_html=True)
        current_step = st.session_state["dialog_step"]

        if current_step < len(questions):
            current_q = questions[current_step]
            progress = (current_step / len(questions)) * 100

            st.markdown(
                f"""
                <div class="dialog-header" id="dialog-section">
                    <h2 class="dialog-title">{current_q["title"]}</h2>
                    <div class="dialog-progress">
                        <div class="dialog-progress-bar" style="width: {progress}%"></div>
                    </div>
                    <p style="color: #6b7280; margin: 0;">{current_q["question"]}</p>
                </div>
                """,
                unsafe_allow_html=True,
            )

            if st.session_state.get("scroll_to_dialog", False):
                components.html(
                    """
                    <script>
                      (function() {
                        const el = window.parent.document.getElementById('dialog-section');
                        if (el) el.scrollIntoView({ behavior: 'smooth', block: 'start' });
                      })();
                    </script>
                    """,
                    height=0,
                )
                st.session_state["scroll_to_dialog"] = False

            if current_q["key"] in st.session_state["validation_feedback"]:
                feedback_data = st.session_state["validation_feedback"][current_q["key"]]
                status = feedback_data.get("status", "unbekannt")
                feedback_text = feedback_data.get("feedback", "")
                suggestions = feedback_data.get("suggestions", "")

                if status == "verbesserung_nötig":
                    st.warning(f"**{texts.get('feedback', {}).get('improvement_needed','Verbesserung empfohlen:')}** {feedback_text}")
                    if suggestions:
                        st.info(f"**{texts.get('feedback', {}).get('suggestion','Vorschlag:')}** {suggestions}")
                elif status == "gut":
                    st.success(texts.get("feedback", {}).get("good", "Ihre Antwort ist gut!"))
                else:
                    st.info(f"**{texts.get('feedback', {}).get('feedback_label','Feedback:')}** {feedback_text}")

            col_text, col_audio = st.columns([2, 1])

            # Audio-converted-key früh definieren, damit es in beiden Spalten verfügbar ist
            audio_converted_key = f"audio_converted_{current_step}"

            with col_text:
                widget_key = f"dialog_input_{current_step}"
                audio_widget_key = f"{widget_key}_audio"

                # ensure dialog_answers exists
                st.session_state.setdefault("dialog_answers", {})
                st.session_state["dialog_answers"].setdefault(current_q["key"], "")

                def sync_dialog_answer():
                    st.session_state["dialog_answers"][current_q["key"]] = st.session_state.get(widget_key, "")
                    st.session_state["validated_current"] = False

                def sync_audio_answer():
                    # copy from audio-only widget into dialog_answers and mark as not validated
                    st.session_state["dialog_answers"][current_q["key"]] = st.session_state.get(audio_widget_key, "")
                    st.session_state["validated_current"] = False
                    # clear the audio flag so normal widget is used afterwards
                    if st.session_state.get(audio_converted_key, False):
                        st.session_state[audio_converted_key] = False

                # If we just transcribed audio, render a value-based textarea with a distinct key
                if st.session_state.get(audio_converted_key, False) and st.session_state["dialog_answers"].get(current_q["key"], ""):
                    answer = st.text_area(
                        texts.get("questions", {}).get("label", "Ihre Antwort:"),
                        value=st.session_state["dialog_answers"][current_q["key"]],
                        placeholder=current_q["placeholder"],
                        key=audio_widget_key,
                        height=95,
                        on_change=sync_audio_answer,
                    )
                else:
                    # normal input widget
                    if widget_key not in st.session_state:
                        st.session_state[widget_key] = st.session_state["dialog_answers"][current_q["key"]]

                    answer = st.text_area(
                        texts.get("questions", {}).get("label", "Ihre Antwort:"),
                        placeholder=current_q["placeholder"],
                        key=widget_key,
                        height=95,
                        on_change=sync_dialog_answer,
                    )

            with col_audio:
                audio_answer = st.audio_input(
                    texts.get("audio", {}).get("label", "Ihre Antwort (Audio):"),
                    key=f"audio_input_{current_step}",
                )

                # Track ob für diese Audio-Datei bereits konvertiert wurde
                current_audio_id = str(audio_answer) if audio_answer else None
                
                # Prüfen ob neue Audio-Eingabe vorliegt
                if current_audio_id:
                    last_audio_id = st.session_state.get(f"last_audio_{current_step}", None)
                    if current_audio_id != last_audio_id:
                        # Neue Audio-Eingabe - Button wieder anzeigen
                        st.session_state[audio_converted_key] = False
                        st.session_state[f"last_audio_{current_step}"] = current_audio_id

                # Button nur anzeigen wenn Audio vorhanden, API Key da und noch nicht konvertiert
                show_convert_button = (
                    audio_answer and 
                    api_key and 
                    not st.session_state.get(audio_converted_key, False)
                )

                if show_convert_button:
                    if st.button("Audio zu Text", key=f"convert_{current_step}", use_container_width=True):
                        try:
                            with st.spinner(texts.get("audio", {}).get("processing", "Audio wird verarbeitet...")):
                                client = OpenAI(api_key=api_key)
                                transcription = client.audio.transcriptions.create(
                                    model="whisper-1",
                                    file=audio_answer,
                                )
                                transcribed_text = transcription.text
                                # Text nur in dialog_answers speichern
                                st.session_state["dialog_answers"][current_q["key"]] = transcribed_text
                                # Als konvertiert markieren
                                st.session_state[audio_converted_key] = True
                                # Erfolgsmeldung und transkribierten Text für Anzeige speichern
                                st.session_state["audio_conversion_success"] = {
                                    "message": texts.get("audio", {}).get("success", "Audio erfolgreich konvertiert!"),
                                    "text": transcribed_text,
                                    "step": current_step
                                }
                                # Seite neu laden, damit der Text im Eingabefeld erscheint
                                st.rerun()
                        except Exception as e:
                            st.error(f"{texts.get('audio', {}).get('processing_error','Fehler')}: {e}")
                elif audio_answer and not api_key:
                    st.warning(texts.get("audio", {}).get("api_key_required", "OpenAI API Key erforderlich"))

            # Audio-Konvertierung Erfolgsmeldung anzeigen (falls vorhanden)
            if "audio_conversion_success" in st.session_state:
                success_data = st.session_state["audio_conversion_success"]
                if success_data.get("step") == current_step:
                    st.success(success_data.get("message", "Audio erfolgreich konvertiert!"))
                    # Meldung nach Anzeige löschen, damit sie nicht bei jedem Reload erscheint
                    del st.session_state["audio_conversion_success"]

            col_prev, col_next, col_cancel = st.columns([1, 1, 1])

            with col_prev:
                if current_step > 0:
                    if st.button(texts.get("questions", {}).get("back_button", "Zurück"), key="prev_btn", use_container_width=True):
                        previous_question()
                        st.rerun()

            # Use a single placeholder in the center column to ensure only one center button/widget is rendered
            center_slot = col_next.empty()
            validated = st.session_state.get("validated_current", False)

            # Center button logic for intermediate steps
            if current_step < len(questions) - 1:
                if not validated:
                    # Widget-State VOR Button-Evaluation explizit synchronisieren
                    audio_widget_key = f"{widget_key}_audio"
                    if audio_widget_key in st.session_state:
                        st.session_state["dialog_answers"][current_q["key"]] = st.session_state.get(audio_widget_key, "")
                    else:
                        st.session_state["dialog_answers"][current_q["key"]] = st.session_state.get(widget_key, "")

                    if center_slot.button(
                        texts.get("questions", {}).get("validate_button"),
                        key=f"validate_btn_{current_step}",
                        type="primary",
                        use_container_width=True,
                        disabled=not api_key,
                    ):
                        answer_text = st.session_state["dialog_answers"].get(current_q["key"], "").strip()

                        if answer_text:
                            try:
                                with st.spinner(texts.get("questions", {}).get("validating")):
                                    single_result = validate_single_answer(api_key, current_q["key"], answer_text)

                                st.session_state.setdefault("validation_feedback", {})
                                st.session_state["validation_feedback"][current_q["key"]] = single_result.get(
                                    "feedback",
                                    {
                                        "status": "verbesserung_nötig",
                                        "feedback": "Keine Bewertung erhalten.",
                                        "suggestions": "Bitte Antwort prüfen.",
                                    },
                                )
                                st.session_state["validated_current"] = True
                                st.session_state["scroll_to_next_button"] = True
                                if single_result.get("ready_for_bpmn"):
                                    st.session_state["validation_passed"] = True
                            except Exception as e:
                                st.error(f"{texts.get('feedback', {}).get('validation_failed')}: {e}")
                            # For immediate UI update (ensure feedback and scroll appear now)
                            st.rerun()
                else:
                    # Scroll-Script nach Validierung
                    if st.session_state.get("scroll_to_next_button", False):
                        components.html(
                            """
                            <script>
                              (function() {
                                setTimeout(() => {
                                  const buttons = window.parent.document.querySelectorAll('button');
                                  for (let btn of buttons) {
                                    if (btn.textContent.includes('Weiter') || btn.textContent.includes('Zur Zusammenfassung')) {
                                      btn.scrollIntoView({ behavior: 'smooth', block: 'center' });
                                      break;
                                    }
                                  }
                                }, 100);
                              })();
                            </script>
                            """,
                            height=0,
                        )
                        st.session_state["scroll_to_next_button"] = False

                    if center_slot.button(
                        texts.get("questions", {}).get("next_button", "Weiter"),
                        key="next_btn",
                        type="primary",
                        use_container_width=True,
                    ):
                        st.session_state["validated_current"] = False
                        next_question()
                        st.rerun()

            # Center button logic for last step
            else:
                if not validated:
                    if center_slot.button(
                        texts.get("questions", {}).get("validate_button", "Antwort validieren"),
                        key="validate_last",
                        type="primary",
                        use_container_width=True,
                        disabled=not api_key,
                    ):
                        # Für alle Fragen: Direkt answer-Wert verwenden (zuverlässigster)
                        answer_text = st.session_state["dialog_answers"].get(current_q["key"], "").strip()

                        if answer_text:
                            # Sofort speichern und validieren
                            st.session_state["dialog_answers"][current_q["key"]] = answer_text
                            try:
                                with st.spinner(texts.get("questions", {}).get("validating", "Validierung läuft...")):
                                    single_result = validate_single_answer(api_key, current_q["key"], answer_text)

                                st.session_state.setdefault("validation_feedback", {})
                                st.session_state["validation_feedback"][current_q["key"]] = single_result.get(
                                    "feedback",
                                    {
                                        "status": "verbesserung_nötig",
                                        "feedback": "Keine Bewertung erhalten.",
                                        "suggestions": "Bitte Antwort prüfen.",
                                    },
                                )
                                st.session_state["validated_current"] = True
                                st.session_state["scroll_to_next_button"] = True
                                if single_result.get("ready_for_bpmn"):
                                    st.session_state["validation_passed"] = True
                            except Exception as e:
                                st.error(f"{texts.get('feedback', {}).get('validation_failed','Validierung fehlgeschlagen')}: {e}")
                            # For immediate UI update (ensure feedback and scroll appear now)
                            st.rerun()
                else:
                    # Scroll-Script nach Validierung
                    if st.session_state.get("scroll_to_next_button", False):
                        components.html(
                            """
                            <script>
                              (function() {
                                setTimeout(() => {
                                  const buttons = window.parent.document.querySelectorAll('button');
                                  for (let btn of buttons) {
                                    if (btn.textContent.includes('Zur Zusammenfassung') || btn.textContent.includes('Weiter')) {
                                      btn.scrollIntoView({ behavior: 'smooth', block: 'center' });
                                      break;
                                    }
                                  }
                                }, 100);
                              })();
                            </script>
                            """,
                            height=0,
                        )
                        st.session_state["scroll_to_next_button"] = False

                    if center_slot.button(
                        texts.get("questions", {}).get("summary_button", "Zur Zusammenfassung"),
                        key="summary_btn",
                        type="primary",
                        use_container_width=True,
                    ):
                        show_summary()
                        st.rerun()

            with col_cancel:
                if st.button(texts.get("questions", {}).get("cancel_button", "Abbrechen"), key="cancel_btn", use_container_width=True):
                    st.session_state["show_dialog"] = False
                    st.rerun()

        elif current_step == len(questions):
            st.markdown(
                """
                <div class="dialog-header">
                    <h2 class="dialog-title">Zusammenfassung Ihrer Antworten</h2>
                    <p style="color: #6b7280; margin: 0;">Sie können Ihre Antworten bearbeiten. Die Vorschau aktualisiert sich automatisch</p>
                </div>
                """,
                unsafe_allow_html=True,
            )

            def sync_summary_answer(q_key: str):
                widget_key = f"summary__{q_key}"
                new_val = st.session_state.get(widget_key, "")
                old_val = st.session_state["dialog_answers"].get(q_key, "")
                st.session_state["dialog_answers"][q_key] = new_val
                if new_val != old_val:
                    st.session_state["summary_saved_for"] = q_key

            st.markdown(f"### {texts.get('summary', {}).get('answers_title', 'Ihre Antworten')}")

            for i, q in enumerate(questions, start=1):
                q_key = q["key"]
                widget_key = f"summary__{q_key}"

                st.session_state["dialog_answers"].setdefault(q_key, "")

                st.markdown(f"**{i}. {q['question']}**")
                st.text_area(
                    texts.get("summary", {}).get("answer_label", "Antwort:"),
                    value=st.session_state["dialog_answers"][q_key],
                    placeholder=q["placeholder"],
                    key=widget_key,
                    height=100,
                    label_visibility="collapsed",
                    on_change=sync_summary_answer,
                    args=(q_key,),
                )

                if st.session_state.get("summary_saved_for") == q_key:
                    st.success(texts.get("summary", {}).get("saved_message", "Änderung gespeichert"))
                    st.session_state["summary_saved_for"] = None

            # Preview removed: only editable summary questions are shown here now.

            col_back, col_direct, col_cancel = st.columns([1, 1, 1])

            with col_back:
                if st.button(texts.get("summary", {}).get("back_button", "Zurück zu Fragen"), key="back_to_last", use_container_width=True):
                    st.session_state["dialog_step"] = len(questions) - 1
                    st.rerun()

            with col_direct:
                if st.button(
                    texts.get("summary", {}).get("continue_button", "Direkt fortfahren"),
                    key="skip_validation",
                    type="secondary",
                    use_container_width=True,
                ):
                    complete_dialog()
                    st.rerun()

            with col_cancel:
                if st.button(texts.get("summary", {}).get("cancel_button", "Abbrechen"), key="cancel_summary", use_container_width=True):
                    st.session_state["show_dialog"] = False
                    st.rerun()

    # -------------------------
    # Footer
    # -------------------------
    st.markdown(
        """
        <div class="footer">
            Entwickelt xxxx von y z an der Uni •
            <a href="mailto:y.z@Uni.de">Kontakt</a>
        </div>
        """,
        unsafe_allow_html=True,
    )
