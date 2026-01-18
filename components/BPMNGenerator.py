import os
from openai import OpenAI
from components.utils import extract_xml_diagram_content, extract_xml_content
import xml.etree.ElementTree as ET

class BPMNGenerator:
    def __init__(self, audio, text, api_key):
        self.audio = audio
        self.api_key = api_key
        self.text = text
        self.bpmn = ""
        self.namespace = {
            'bpmn': 'http://www.omg.org/spec/BPMN/20100524/MODEL',
            'bpmndi': 'http://www.omg.org/spec/BPMN/20100524/DI',
            'dc': 'http://www.omg.org/spec/DD/20100524/DC',
            'di': 'http://www.omg.org/spec/DD/20100524/DI'
        }

    def get_bpmn(self):
        return self.bpmn

    def speech_to_text(self):
        client = OpenAI(api_key=self.api_key)

        transcription = client.audio.transcriptions.create(
            model="whisper-1",
            file=self.audio
        )
        self.text = transcription.text

    def text_to_model(self):
        client = OpenAI(api_key=self.api_key)
        base_dir = os.path.dirname(__file__)
        project_root = os.path.abspath(os.path.join(base_dir, ".."))
        prompt_file_path = os.path.join(project_root, "input", "prompts", "cot-prompt.txt")
        description_1_file_path = os.path.join(project_root, "input", "examples", "example-1", "description.txt")
        process_1_file_path = os.path.join(project_root, "input", "examples", "example-1", "process.bpmn")
        description_2_file_path = os.path.join(project_root, "input", "examples", "example-2", "description.txt")
        process_2_file_path = os.path.join(project_root, "input", "examples", "example-2", "process.bpmn")
        with open(prompt_file_path) as file:
            cot_prompt = file.read()
        with open(description_1_file_path) as file:
            text_1 = file.read()
        with open(process_1_file_path) as file:
            bpmn_1 = file.read()
        with open(description_2_file_path) as file:
            text_2 = file.read()
        with open(process_2_file_path) as file:
            bpmn_2 = file.read()

        prompt = cot_prompt #+ "\nExample 1\nText: " + text_1 + "\nBPMN: " + bpmn_1 + "\nExample2:\nText: " + text_2 + "\nBPMN: " + bpmn_2 + "\n\nTask Text: " + self.text

        response = client.chat.completions.create(
            model="gpt-5.2",
            messages=[
                {
                    "role": "user",
                    "content": prompt
                }
            ]
        ).choices[0].message.content

        xml_content = extract_xml_content(response)

        self.bpmn = xml_content

    def improve_model(self):
        client = OpenAI(api_key=self.api_key)
        base_dir = os.path.dirname(__file__)
        project_root = os.path.abspath(os.path.join(base_dir, ".."))
        prompt_path = os.path.join(project_root, "input", "prompts", "improvement.txt")
        with open(prompt_path) as file:
            improvement_prompt = file.read()
        
        prompt = improvement_prompt + "\n\nBPMN XML: " + self.bpmn + "\n\nTextual Process Description: " + self.text

        response_improved = client.chat.completions.create(
            model="gpt-5.2",
            messages=[
                {
                    "role": "user",
                    "content": prompt
                }
            ]
        ).choices[0].message.content

        xml_content_improved = extract_xml_content(response_improved)

        self.bpmn = xml_content_improved

    def check_xml_completeness(self):
        root = ET.fromstring(self.bpmn)

        node_tags = [
            'task',
            'subProcess',
            'startEvent',
            'endEvent',
            'intermediateThrowEvent',
            'intermediateCatchEvent',
            'exclusiveGateway',
            'parallelGateway',
            'inclusiveGateway',
            'gateway',
            'boundaryEvent',
            'dataObjectReference',
            'dataStoreReference'
        ]

        flow_tags = [
            'sequenceFlow',
            'messageFlow',
            'dataOutputAssociation',
            'dataInputAssociation'
        ]

        node_count = 0
        for tag in node_tags:
            elements = root.findall(f".//bpmn:{tag}", self.namespace)
            node_count += len(elements)

        flow_count = 0
        for tag in flow_tags:
            elements = root.findall(f".//bpmn:{tag}", self.namespace)
            flow_count += len(elements)
        
        bpmn_shapes = root.findall(".//bpmndi:BPMNShape", self.namespace)
        di_shape_count = len(bpmn_shapes)
        bpmn_edges = root.findall(".//bpmndi:BPMNEdge", self.namespace)
        di_edge_count = len(bpmn_edges)

        return node_count <= di_shape_count and flow_count <= di_edge_count

    def generate_bpmn_di(self):
        client = OpenAI(api_key=self.api_key)
        base_dir = os.path.dirname(__file__)
        project_root = os.path.abspath(os.path.join(base_dir, ".."))
        prompt_path = os.path.join(project_root, "input", "prompts", "di-generation.txt")
        with open(prompt_path) as file:
            di_generation_prompt = file.read()
        
        prompt = di_generation_prompt + "\n\nBPMN XML: " + self.bpmn + "\n\nTextual Process Description: " + self.text

        response_completed = client.chat.completions.create(
            model="o1",
            messages=[
                {
                    "role": "user",
                    "content": prompt
                }
            ]
        ).choices[0].message.content

        di_part = extract_xml_diagram_content(response_completed)

        return di_part

    def merge_bpmn_xml_diagram(self, diagram):

        root = ET.fromstring(self.bpmn)
        definitions_tag = f"{{{self.namespace['bpmn']}}}definitions"
        definitions_el = root if root.tag == definitions_tag else root.find('.//bpmn:definitions', self.namespace)
        if definitions_el is None:
            print("ERROR: Could not find <bpmn:definitions> element in the original BPMN file.")
            return
        for diagram_el in definitions_el.findall('./bpmndi:BPMNDiagram', self.namespace):
            definitions_el.remove(diagram_el)

        wrapped_diagram = f'''<temp
            xmlns="http://www.omg.org/spec/BPMN/20100524/MODEL"
            xmlns:xsi="http://www.w3.org/2001/XMLSchema-instance"
            xmlns:bpmndi="http://www.omg.org/spec/BPMN/20100524/DI"
            xmlns:omgdc="http://www.omg.org/spec/DD/20100524/DC"
            xmlns:omgdi="http://www.omg.org/spec/DD/20100524/DI"
            xmlns:di="http://www.omg.org/spec/DD/20100524/DI"
            xmlns:dc="http://www.omg.org/spec/DD/20100524/DC">
            {diagram}
        </temp>'''
        wrapped_root = ET.fromstring(wrapped_diagram)
        diagram_element = wrapped_root.find('.//bpmndi:BPMNDiagram', self.namespace)
        if diagram_element is None:
            print("ERROR: no diagram element found")
            return
        
        definitions_el.append(diagram_element)

        final_bpmn = ET.ElementTree(definitions_el)
        self.bpmn = final_bpmn