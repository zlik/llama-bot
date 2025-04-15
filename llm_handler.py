import base64
import io
import json
import os

import yaml
from dotenv import load_dotenv
from openai import OpenAI
from pdf2image import convert_from_bytes
from PIL import Image


load_dotenv()  # ✅ Load environment variables

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))


def load_prompts(path="config/prompts.yaml"):
    with open(path, "r") as f:
        return yaml.safe_load(f)


prompts = load_prompts()


def extract_text_from_combined_input(input_text):
    prompt = [
        {"role": p["role"], "content": p["content"].format(input_text=input_text)}
        for p in prompts["extract_text_from_combined_input"]
    ]

    response = client.chat.completions.create(
        model="gpt-4-turbo", messages=prompt, max_tokens=100
    )

    raw = response.choices[0].message.content.strip()

    if not raw.startswith("{"):
        raise ValueError("LLM returned invalid or empty JSON")

    return json.loads(raw)


def prepare_image_data_url(file_bytes, file_name):
    image = (
        convert_from_bytes(file_bytes)[0]
        if file_name.lower().endswith(".pdf")
        else Image.open(io.BytesIO(file_bytes))
    )
    buffered = io.BytesIO()
    image.save(buffered, format="PNG")
    return f"data:image/png;base64,{base64.b64encode(buffered.getvalue()).decode()}"


def parse_receipt_with_vision(amount, reason, data_url):
    vision_prompt = [
        {
            "role": prompts["parse_receipt_with_vision"][0]["role"],
            "content": prompts["parse_receipt_with_vision"][0]["content"],
        },
        {
            "role": prompts["parse_receipt_with_vision"][1]["role"],
            "content": [
                {
                    "type": "text",
                    "text": prompts["parse_receipt_with_vision"][1]["content"][0][
                        "text"
                    ].format(amount=amount, reason=reason),
                },
                {"type": "image_url", "image_url": {"url": data_url}},
            ],
        },
    ]

    vision_resp = client.chat.completions.create(
        model="gpt-4-turbo", messages=vision_prompt, max_tokens=1000
    )
    result = vision_resp.choices[0].message.content.strip()
    if result.startswith("```"):
        result = result.strip("` \n")
        if result.startswith("json"):
            result = result[len("json") :].strip()
    return json.loads(result)


def extract_llm_amount_and_items(extracted_json):
    llm_terms = [
        "meta llama",
        "llama",
    ]

    raw_items = extracted_json.get("line_items", [])
    llm_items = [
        item
        for item in raw_items
        if any(term in item.get("description", "").lower() for term in llm_terms)
        or any(term in extracted_json.get("provider", "").lower() for term in llm_terms)
        or any(term in extracted_json.get("model_version_range", "").lower() for term in llm_terms)
        or any(term in extracted_json.get("model", "").lower() for term in llm_terms)
        or any(term in extracted_json.get("llm_model", "").lower() for term in llm_terms)
    ]

    llm_total = 0.0
    for item in llm_items:
        try:
            llm_total += float(item["amount"].replace("$", "").replace(",", ""))
        except:
            continue

    return llm_items, f"${llm_total:.2f}"
