import json
import base64
import io
import os
from PIL import Image
from pdf2image import convert_from_bytes
from openai import OpenAI
from dotenv import load_dotenv

load_dotenv()  # ✅ Load environment variables

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

def extract_text_from_combined_input(input_text):
    prompt = [
        {
            "role": "user",
            "content": (
                f"Extract the **amount** and **reason** from this input:\n\n\"{input_text}\"\n\n"
                "Return JSON like:\n"
                '{\n  "amount": "$136.42",\n  "reason": "March compute"\n}'
            )
        }
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
            "role": "system",
            "content": "Respond ONLY with valid JSON. Do not include any explanation or text outside the JSON block."
        },
        {
            "role": "user",
            "content": [
                {
                    "type": "text",
                    "text": (
                        f"You are an AI reimbursement validator.\n"
                        f"The user submitted:\n- Amount: ${amount}\n- Reason: {reason}\n\n"
                        "From the attached invoice or receipt, extract the following fields:\n\n"
                        "{\n"
                        '  "provider": "Amazon",\n'
                        '  "invoice_number": "INV-0455",\n'
                        '  "invoice_date": "2024-03-15",\n'
                        '  "billing_period": "Mar 2024",\n'
                        '  "invoice_account_id": "320567679581",\n'
                        '  "payer_account_id": "320567679581",\n'
                        '  "account_id": "320567679581",\n'
                        '  "payment_method": "Visa **** 1234",\n'
                        '  "amount": "$136.42",\n'
                        '  "tax_amount": "$10.00",\n'
                        '  "total_amount": "$146.42",\n'
                        '  "line_items": [\n'
                        '    {"description": "Meta Llama 3.1 70B via Amazon Bedrock", "amount": "$100.00"}\n'
                        '  ]\n'
                        "}"
                    )
                },
                {
                    "type": "image_url",
                    "image_url": {"url": data_url}
                }
            ]
        }
    ]
    vision_resp = client.chat.completions.create(
        model="gpt-4-turbo", messages=vision_prompt, max_tokens=1000
    )
    result = vision_resp.choices[0].message.content.strip()
    if result.startswith("```"):
        result = result.strip("` \n")
        if result.startswith("json"):
            result = result[len("json"):].strip()
    return json.loads(result)

def extract_llm_amount_and_items(extracted_json):
    llm_terms = ["llm", "language model", "openai", "gpt", "chatgpt", "anthropic",
                 "claude", "bedrock", "cohere", "mistral", "meta llama", "llama", "inference"]

    raw_items = extracted_json.get("line_items", [])
    llm_items = [
        item for item in raw_items
        if any(term in item.get("description", "").lower() for term in llm_terms)
        or any(term in extracted_json.get("provider", "").lower() for term in llm_terms)
    ]

    llm_total = 0.0
    for item in llm_items:
        try:
            llm_total += float(item["amount"].replace("$", "").replace(",", ""))
        except:
            continue

    return llm_items, f"${llm_total:.2f}"
