import json
from openai import OpenAI
from utils import create_unique_file_name, extract_xml_content
import os

base_dir = os.path.abspath(os.path.join(os.getcwd(), '..'))
with open(os.path.join(base_dir, "api-key.json")) as file:
    client = OpenAI(api_key=json.load(file)["openai"])

def speech_to_model(prompting_method, file_path, examples, output_folder):

    ### 1. Step: Transformation of speech to text

    audio = open(os.path.join(base_dir,file_path), "rb")
    transcription = client.audio.transcriptions.create(
        model="whisper-1",
        file=audio
    ).text
    print("Audio transformed to text")

    ### 2. Step: Information extraction and BPMN creation

    if "instruction" in prompting_method:
        with open(os.path.join(base_dir,"./gpt-input/bpmn-generation/zero-shot/prompt3.txt")) as file:
            base_prompt = file.read()
    else:
        with open(os.path.join(base_dir,"./gpt-input/bpmn-generation/chain-of-thought/prompt3.txt")) as file:
            base_prompt = file.read()

    if examples:
        with open(os.path.join(base_dir,examples["1"]["text"])) as file:
            example_text_1 = file.read()
        with open(os.path.join(base_dir,examples["2"]["text"])) as file:
            example_text_2 = file.read()
        with open(os.path.join(base_dir,examples["1"]["bpmn"])) as file:
            example_bpmn_1 = file.read()
        with open(os.path.join(base_dir,examples["2"]["bpmn"])) as file:
            example_bpmn_2 = file.read()
        prompt = base_prompt + "\nExample 1\nText: " + example_text_1 + "\nBPMN: " + example_bpmn_1 + "\nExample2:\nText: " + example_text_2 + "\nBPMN: " + example_bpmn_2 + "\n\nTask Text: " + transcription
    else:
        prompt = base_prompt

    response = client.chat.completions.create(
        model="o1",
        messages=[
            {
                "role": "user",
                "content": prompt
            }
        ]
    ).choices[0].message.content

    print("XML created")

    xml_content = extract_xml_content(response)

    ### 3. Step: Improvement iteration

    if "improvement" in prompting_method:
        with open(os.path.join(base_dir,"./gpt-input/bpmn-generation/refinement/prompt3.txt")) as file:
            improvement_prompt = file.read()

        prompt = improvement_prompt + "\n\nBPMN XML: " + xml_content + "\n\nTextual Process Description: " + transcription

        response_improved = client.chat.completions.create(
            model="o1",
            messages=[
                {
                    "role": "user",
                    "content": prompt
                }
            ]
        ).choices[0].message.content

        xml_content_improved = extract_xml_content(response_improved)

        print("XML improved")

        final_bpmn = xml_content_improved
    else:
        final_bpmn = xml_content

    base_name = "model"
    extension = "bpmn"
    output_file_path = create_unique_file_name(output_folder, base_name, extension)

    with open(os.path.join(base_dir,output_file_path), 'w', encoding='utf-8') as file:
        file.write(final_bpmn)

    print("Model created")

if __name__ == "__main__":
    audio_1 = "./data/controlled-environment/1-speech/1-ExamRegistration.m4a"
    audio_2 = "./data/controlled-environment/1-speech/2-FrontendMergeRequest.m4a"
    audio_3 = "./data/controlled-environment/1-speech/3-RestaurantOrderWithQuickDelivery.m4a"
    audio_4 = "./data/controlled-environment/1-speech/4-StartupEvaluationVC.m4a"
    text_1 = "./data/controlled-environment/2-text/1-ExamRegistration.txt"
    text_2 = "./data/controlled-environment/2-text/2-FrontendMergeRequest.txt"
    text_3 = "./data/controlled-environment/2-text/3-RestaurantOrderWithQuickDelivery.txt"
    text_4 = "./data/controlled-environment/2-text/4-StartupEvaluationVC.txt"
    example_bpmn_1 = "./data/controlled-environment/3-bpmn/1-ExamRegistration.bpmn"
    example_bpmn_2 = "./data/controlled-environment/3-bpmn/2-FrontendMergeRequest.bpmn"
    example_bpmn_3 = "./data/controlled-environment/3-bpmn/3-RestaurantOrderWithQuickDelivery.bpmn"
    example_bpmn_4 = "./data/controlled-environment/3-bpmn/4-StartupEvaluationVC.bpmn"

    speech_to_model("cot+two-shot+improvement", audio_4, {"1": {"text": text_1, "bpmn": example_bpmn_1}, "2": {"text": text_2, "bpmn": example_bpmn_2}}, "./output/test")
