import re
import json
import logging
import xml.etree.ElementTree as ET
from openai import OpenAI
from typing import Optional, Dict, Any

from .prompt_loader import load_prompt, format_prompt, Prompts

logger = logging.getLogger(__name__)
logger.setLevel(logging.DEBUG)

if not logger.handlers:
    console_handler = logging.StreamHandler()
    console_handler.setLevel(logging.DEBUG)
    formatter = logging.Formatter('\n%(asctime)s - %(name)s - %(levelname)s\n%(message)s\n')
    console_handler.setFormatter(formatter)
    logger.addHandler(console_handler)


def extract_xml_content(response: str) -> str:
    xml_pattern = r"<\?xml.*?>.*</.*?>"
    match = re.search(xml_pattern, response, re.DOTALL)
    if match:
        return match.group(0)
    bpmn_pattern = r"<bpmn:definitions[\s\S]*</bpmn:definitions>"
    match = re.search(bpmn_pattern, response, re.DOTALL)
    if match:
        return match.group(0)
    return response.strip()


def extract_xml_diagram_content(response: str) -> str:
    xml_diagram_pattern = r"(<bpmndi:BPMNDiagram[\s\S]*?</bpmndi:BPMNDiagram>)"
    match = re.search(xml_diagram_pattern, response, re.DOTALL)
    if match:
        return match.group(1)
    return ""


def extract_json_content(response: str) -> Dict[str, Any]:
    json_pattern = r"\{.*\}"
    match = re.search(json_pattern, response, re.DOTALL)
    if match:
        json_text = match.group(0)
        return json.loads(json_text)
    return {}


def parse_json_payload(text: str) -> Dict[str, Any]:
    cleaned = text.strip()
    
    if cleaned.startswith('"') and cleaned.endswith('"'):
        cleaned_inner = cleaned[1:-1].lower()
        if cleaned_inner == "flagged":
            return {"flagged": True, "reason": "Antwort wurde als unangemessen erkannt", "nudge": "Bitte beantworten Sie die Frage zum Geschäftsprozess."}
        if cleaned_inner in ("true", "false"):
            return {"flagged": cleaned_inner == "true", "reason": "", "nudge": ""}
    
    if cleaned.startswith("```json"):
        cleaned = cleaned[7:]
    if cleaned.startswith("```"):
        cleaned = cleaned[3:]
    if cleaned.endswith("```"):
        cleaned = cleaned[:-3]
    cleaned = cleaned.strip()
    
    try:
        return json.loads(cleaned)
    except json.JSONDecodeError:
        match = re.search(r"\{[^{}]*\}", cleaned, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
        match = re.search(r"\{.*\}", cleaned, re.DOTALL)
        if match:
            try:
                return json.loads(match.group(0))
            except json.JSONDecodeError:
                pass
        if "flagged" in cleaned.lower():
            is_flagged = "true" in cleaned.lower() or cleaned.lower().strip() == "flagged"
            return {"flagged": is_flagged, "reason": "", "nudge": "Bitte beantworten Sie die Frage zum Geschäftsprozess." if is_flagged else ""}
        if "followup" in cleaned.lower():
            return {"ask_followup": False, "question": ""}
        
        return {}

class BPMNGenerator:
    NAMESPACE = {
        'bpmn': 'http://www.omg.org/spec/BPMN/20100524/MODEL',
        'bpmndi': 'http://www.omg.org/spec/BPMN/20100524/DI',
        'dc': 'http://www.omg.org/spec/DD/20100524/DC',
        'di': 'http://www.omg.org/spec/DD/20100524/DI',
        'bioc': 'http://bpmn.io/schema/bpmn/biocolor/1.0'
    }

    def __init__(self, api_key: str):
        self.api_key = api_key
        self.client = OpenAI(api_key=api_key)

    def text_to_model(self, process_description: str) -> str:
        cot_prompt = load_prompt(Prompts.COT)
        prompt = cot_prompt + "\n\nTextual Process Description: " + process_description
        
        response = self.client.chat.completions.create(
            model="gpt-5.2",
            messages=[
                {"role": "user", "content": prompt}
            ],
            temperature=0.7,
        )
        
        initial_xml = extract_xml_content(response.choices[0].message.content)
        improved_xml = self.improve_model(initial_xml, process_description)
        return improved_xml

    def improve_model(self, bpmn_xml: str, process_description: str) -> str:
        improvement_prompt = load_prompt(Prompts.IMPROVEMENT)
        prompt = (
            improvement_prompt + 
            "\n\nBPMN XML: " + bpmn_xml + 
            "\n\nTextual Process Description: " + process_description
        )
        
        response = self.client.chat.completions.create(
            model="gpt-5.2",
            messages=[
                {"role": "user", "content": prompt}
            ],
            temperature=0.5,
        )
        
        xml_content = extract_xml_content(response.choices[0].message.content)
        return xml_content

    def check_xml_completeness(self, bpmn_xml: str) -> list:
        issues = []
        try:
            root = ET.fromstring(bpmn_xml)
        except ET.ParseError as e:
            return [f"XML Parse Error: {e}"]

        node_tags = [
            'task', 'subProcess', 'startEvent', 'endEvent',
            'intermediateThrowEvent', 'intermediateCatchEvent',
            'exclusiveGateway', 'parallelGateway', 'inclusiveGateway',
            'gateway', 'boundaryEvent', 'dataObjectReference', 'dataStoreReference'
        ]

        flow_tags = [
            'sequenceFlow', 'messageFlow', 
            'dataOutputAssociation', 'dataInputAssociation'
        ]

        node_count = sum(
            len(root.findall(f".//bpmn:{tag}", self.NAMESPACE))
            for tag in node_tags
        )

        flow_count = sum(
            len(root.findall(f".//bpmn:{tag}", self.NAMESPACE))
            for tag in flow_tags
        )
        
        di_shape_count = len(root.findall(".//bpmndi:BPMNShape", self.NAMESPACE))
        di_edge_count = len(root.findall(".//bpmndi:BPMNEdge", self.NAMESPACE))

        if node_count > di_shape_count:
            issues.append(f"Missing DI shapes: {node_count - di_shape_count}")
        if flow_count > di_edge_count:
            issues.append(f"Missing DI edges: {flow_count - di_edge_count}")
            
        return issues

    def generate_bpmn_di(self, bpmn_xml: str, process_description: str = "") -> str:
        di_prompt = load_prompt(Prompts.DI_GENERATION)
        prompt = (
            di_prompt + 
            "\n\nBPMN XML: " + bpmn_xml
        )
        if process_description:
            prompt += "\n\nTextual Process Description: " + process_description

        response = self.client.chat.completions.create(
            model="gpt-5.2",
            messages=[
                {"role": "user", "content": prompt}
            ],
            temperature=0.5,
        )

        di_part = extract_xml_diagram_content(response.choices[0].message.content)
        return di_part

    def merge_bpmn_xml_diagram(self, bpmn_xml: str, diagram: str) -> str:
        try:
            root = ET.fromstring(bpmn_xml)
        except ET.ParseError:
            return bpmn_xml
            
        definitions_tag = f"{{{self.NAMESPACE['bpmn']}}}definitions"
        definitions_el = root if root.tag == definitions_tag else root.find('.//bpmn:definitions', self.NAMESPACE)
        
        if definitions_el is None:
            return bpmn_xml
        for diagram_el in definitions_el.findall('./bpmndi:BPMNDiagram', self.NAMESPACE):
            definitions_el.remove(diagram_el)
        wrapped_diagram = f'''<temp
            xmlns="http://www.omg.org/spec/BPMN/20100524/MODEL"
            xmlns:bpmndi="http://www.omg.org/spec/BPMN/20100524/DI"
            xmlns:dc="http://www.omg.org/spec/DD/20100524/DC"
            xmlns:di="http://www.omg.org/spec/DD/20100524/DI">
            {diagram}
        </temp>'''
        try:
            wrapped_root = ET.fromstring(wrapped_diagram)
            diagram_element = wrapped_root.find('.//bpmndi:BPMNDiagram', self.NAMESPACE)
            
            if diagram_element is not None:
                definitions_el.append(diagram_element)
                return ET.tostring(definitions_el, encoding="unicode")
        except ET.ParseError:
            pass
            
        return bpmn_xml


class InterviewAgents:
    def __init__(self, api_key: str):
        self.api_key = api_key
        self.client = OpenAI(api_key=api_key)

    def run_security_agent(
        self, 
        question_text: str, 
        answer_text: str, 
        process_context: str,
        summary_text: str
    ) -> Dict[str, Any]:
        logger.info("=== SECURITY AGENT START ===")
        
        prompt = format_prompt(
            Prompts.SECURITY_AGENT,
            process_context=process_context,
            summary_text=summary_text,
            question_text=question_text,
            answer_text=answer_text
        )
        logger.debug(f"Prompt: {prompt}")
        
        response = self.client.chat.completions.create(
            model="gpt-5.2",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.1,
            max_completion_tokens=250,
        )
        
        raw_response = response.choices[0].message.content.strip()
        logger.info(f"Security Agent RAW Response: {raw_response}")
        
        result = parse_json_payload(raw_response)
        logger.info(f"Security Agent Parsed Result: {result}")
        logger.info("=== SECURITY AGENT END ===")
        
        return result

    def run_summary_agent(
        self,
        summary_text: str,
        question_text: str,
        answer_text: str,
        topic_history: list,
        process_context: str
    ) -> str:
        logger.info("=== SUMMARY AGENT START ===")
        logger.debug(f"Question: {question_text}")
        logger.debug(f"Previous Summary Length: {len(summary_text)} chars")
        
        prompt = format_prompt(
            Prompts.SUMMARY_AGENT,
            summary_text=summary_text,
            answer_text=answer_text,
            process_context=process_context,
            question_text=question_text,
            topic_history=topic_history
        )
        
        response = self.client.chat.completions.create(
            model="gpt-5.2",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_completion_tokens=1500,
        )
        
        result = response.choices[0].message.content.strip()
        logger.info(f"Summary Agent Response: {result[:300]}..." if len(result) > 300 else f"Summary Agent Response: {result}")
        logger.info("=== SUMMARY AGENT END ===")
        
        return result

    def run_probing_agent(
        self,
        question_text: str,
        answer_text: str,
        summary_text: str,
        topic_history: list,
        process_context: str
    ) -> Dict[str, Any]:
        logger.info("=== PROBING AGENT START ===")
        logger.debug(f"Question: {question_text}")
        logger.debug(f"Answer: {answer_text[:200]}..." if len(answer_text) > 200 else f"Answer: {answer_text}")
        
        prompt = format_prompt(
            Prompts.PROBING_AGENT,
            summary_text=summary_text,
            question_text=question_text,
            process_context=process_context,
            topic_history=topic_history,
            answer_text=answer_text
        )
        
        response = self.client.chat.completions.create(
            model="gpt-5.2",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.3,
            max_completion_tokens=200,
        )
        
        raw_response = response.choices[0].message.content.strip()
        logger.info(f"Probing Agent RAW Response: {raw_response}")
        
        result = parse_json_payload(raw_response)
        logger.info(f"Probing Agent Parsed Result: {result}")
        logger.info("=== PROBING AGENT END ===")
        
        return result

    def run_topic_manager_agent(
        self,
        process_context: str,
        summary_text: str,
        topic_defs: list,
        topics_completed: list,
        current_topic_key: str,
        topic_history: dict,
    ) -> Dict[str, Any]:
        logger.info("=== TOPIC MANAGER START ===")
        current_topic_title = current_topic_key
        for topic in topic_defs:
            if topic.get("key") == current_topic_key:
                current_topic_title = topic.get("title", current_topic_key)
                break

        prompt = format_prompt(
            Prompts.TOPIC_MANAGER,
            process_context=process_context,
            summary_text=summary_text,
            topic_defs=topic_defs,
            topics_completed=topics_completed,
            current_topic_title=current_topic_title,
            topic_history=topic_history,
        )

        response = self.client.chat.completions.create(
            model="gpt-5.2",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_completion_tokens=250,
        )

        raw_response = response.choices[0].message.content.strip()
        logger.info(f"Topic Manager RAW Response: {raw_response}")

        result = parse_json_payload(raw_response)
        logger.info(f"Topic Manager Parsed Result: {result}")
        logger.info("=== TOPIC MANAGER END ===")

        remaining = [t for t in topic_defs if t.get("key") not in topics_completed]
        if not remaining:
            return {"complete": True}

        topic_complete = bool(result.get("complete"))
        if topic_complete:
            remaining_after_current = [t for t in remaining if t.get("key") != current_topic_key]
            if remaining_after_current:
                next_topic = remaining_after_current[0]
            else:
                return {"complete": True}
        else:
            current_topic = next((t for t in topic_defs if t.get("key") == current_topic_key), topic_defs[0])
            next_topic = current_topic
        next_key = next_topic["key"]
        next_title = next_topic["title"]
        next_question = result.get("next_question") or f"Können Sie kurz etwas zu {next_title} sagen?"

        return {
            "complete": topic_complete,
            "next_topic_key": next_key,
            "next_topic_title": next_title,
            "next_question": next_question,
        }

    def summarize_answer(self, question_text: str, answer_text: str) -> str:
        logger.info("=== SUMMARIZE ANSWER START ===")
        logger.debug(f"Question: {question_text}")
        
        if not answer_text:
            logger.info("No answer text, returning empty")
            return answer_text
        
        prompt = format_prompt(
            Prompts.SUMMARIZE_ANSWER,
            question_text=question_text,
            answer_text=answer_text
        )
        
        response = self.client.chat.completions.create(
            model="gpt-5.2",
            messages=[{"role": "user", "content": prompt}],
            temperature=0.2,
            max_completion_tokens=200,
        )
        
        result = response.choices[0].message.content.strip() or answer_text
        logger.info(f"Summarize Answer Response: {result}")
        logger.info("=== SUMMARIZE ANSWER END ===")
        
        return result


def bpmn_to_mermaid(bpmn_xml: str) -> str:
    try:
        root = ET.fromstring(bpmn_xml)
    except ET.ParseError:
        return "graph TD\n    Error[Fehler beim Parsen]"
    
    ns = {'bpmn': 'http://www.omg.org/spec/BPMN/20100524/MODEL'}
    
    mermaid_lines = ["graph TD"]
    start_events = root.findall(".//bpmn:startEvent", ns)
    end_events = root.findall(".//bpmn:endEvent", ns)
    tasks = root.findall(".//bpmn:task", ns)
    user_tasks = root.findall(".//bpmn:userTask", ns)
    service_tasks = root.findall(".//bpmn:serviceTask", ns)
    xor_gateways = root.findall(".//bpmn:exclusiveGateway", ns)
    and_gateways = root.findall(".//bpmn:parallelGateway", ns)
    sequence_flows = root.findall(".//bpmn:sequenceFlow", ns)
    for se in start_events:
        sid = se.get('id', 'start')
        name = se.get('name', 'Start')
        mermaid_lines.append(f"    {sid}((({name})))")
    
    for ee in end_events:
        eid = ee.get('id', 'end')
        name = ee.get('name', 'Ende')
        mermaid_lines.append(f"    {eid}((({name})))")
    
    all_tasks = tasks + user_tasks + service_tasks
    for task in all_tasks:
        tid = task.get('id', 'task')
        name = task.get('name', 'Task')
        name = name.replace('"', "'")
        mermaid_lines.append(f'    {tid}["{name}"]')
    
    for gw in xor_gateways:
        gid = gw.get('id', 'gateway')
        name = gw.get('name', 'X')
        mermaid_lines.append(f"    {gid}{{{name}}}")
    
    for gw in and_gateways:
        gid = gw.get('id', 'gateway')
        name = gw.get('name', '+')
        mermaid_lines.append(f"    {gid}{{{name}}}")
    for flow in sequence_flows:
        source = flow.get('sourceRef', '')
        target = flow.get('targetRef', '')
        name = flow.get('name', '')
        if source and target:
            if name:
                mermaid_lines.append(f"    {source} -->|{name}| {target}")
            else:
                mermaid_lines.append(f"    {source} --> {target}")
    
    return "\n".join(mermaid_lines)
