import fitz  # PyMuPDF
import pytesseract  # OCR fallback
from openai import OpenAI
import json
import time
from typing import List
from dotenv import load_dotenv
from concurrent.futures import ThreadPoolExecutor, as_completed
from base64 import b64encode
import io
from PIL import Image
import os
import argparse

load_dotenv()

client = OpenAI(api_key=os.getenv("OPENAI_API_KEY"))

# Extract text and image per page
def extract_pdf_pages(pdf_path: str) -> List[dict]:
    doc = fitz.open(pdf_path)
    pages = []
    for page_num in range(len(doc)):
        page = doc.load_page(page_num)
        text = page.get_text()
        if not text.strip():
            print(f"OCR fallback used on page {page_num}")
            pix = page.get_pixmap(dpi=300)
            img_pil = Image.open(io.BytesIO(pix.tobytes("png")))
            text = pytesseract.image_to_string(img_pil)
        pix = page.get_pixmap(dpi=150)
        img_bytes = pix.tobytes("png")
        img_base64 = b64encode(img_bytes).decode('utf-8')
        pages.append({"text": text, "image": img_base64})
    doc.close()
    return pages

# Chunk pages into manageable batches
def chunk_pages(pages: List[dict], max_tokens=6000) -> List[List[dict]]:
    chunks = []
    current_chunk = []
    current_length = 0
    for page in pages:
        page_length = len(page['text'])
        if current_length + page_length > max_tokens:
            chunks.append(current_chunk)
            current_chunk = [page]
            current_length = page_length
        else:
            current_chunk.append(page)
            current_length += page_length
    if current_chunk:
        chunks.append(current_chunk)
    return chunks

# Worker to call GPT-4 on a single chunk
def process_chunk(idx: int, chunk: List[dict], log_file=None, force=False):
    try:
        start_time = time.time()
        text = "\n\n".join([p['text'] for p in chunk])

        response_path = f'chunk_{idx+1}_raw_response.json'
        if os.path.exists(response_path) and not force:
            with open(response_path, 'r') as f:
                content = f.read()
        else:
            response = client.chat.completions.create(
                model="gpt-4-turbo",
                messages=[
                    {"role": "system", "content": (
                        "You are an expert invoice parser. Extract structured billing and usage information from the following invoice text.\n"
                        "Return the data strictly in JSON format. Include all the following fields when available:\n"
                        "- invoice_number\n"
                        "- invoice_date\n"
                        "- due_date\n"
                        "- billing_period_start\n"
                        "- billing_period_end\n"
                        "- account_id\n"
                        "- team_id\n"
                        "- customer_id / user_id\n"
                        "- payer_name / payer_email\n"
                        "- vendor_name / service_provider\n"
                        "- company or org name (e.g. OpenAI, Groq, Together AI, X.AI, Fireworks AI, Google Cloud, etc.)\n"
                        "- address of payer or provider\n"
                        "- currency\n"
                        "- payment_method\n"
                        "- region\n"
                        "- service_name\n"
                        "- category / department / environment (e.g. dev, staging, production)\n"
                        "- resource_type (e.g. EC2, API, LLM)\n"
                        "- model or instance_type (e.g. g5.12xlarge, Llama3-70B)\n"
                        "- model_provider\n"
                        "- description\n"
                        "- usage_unit\n"
                        "- usage_quantity / units_used\n"
                        "- duration (e.g. hourly, monthly)\n"
                        "- start_time\n"
                        "- end_time\n"
                        "- price_per_unit / price_per_token / price_per_request\n"
                        "- number_of_tokens / number_of_requests\n"
                        "- base_amount\n"
                        "- line_total_amount\n"
                        "- subtotal\n"
                        "- discount / discount_percent\n"
                        "- tax / tax_percent\n"
                        "- adjustments / credits\n"
                        "- total\n"
                        "- amount_due\n"
                        "- payment_status\n"
                        "- link_to_pay / pay_online_url\n"
                        "Only include values that are explicitly stated. Do not include any items with a $0 total\n"
                        "unless they explicitly reference LLM usage, token counts, or named models like Llama.\n"
                        "After extracting data, analyze it and extend the result with these fields. Think carefully\n"
                        "and try to get a summary of expenses in the invoice that are related to Llama, LLM or inference\n"
                        "- total_spent_on_llm or total_spent_on_inference\n"
                        "- total_spent_on_llama\n"
                        "- total_llama_tokens_used\n"
                        "- total_llm_tokens_used\n"
                        "- total_spent_by_provider (e.g. {'OpenAI': 12.50, 'Grok': 5.00})\n"
                        "If the JSON output is malformed or partially invalid, attempt to fix it and return valid JSON."
                    )},
                    {"role": "user", "content": [
                        {"type": "text", "text": text},
                        *[
                            {"type": "image_url", "image_url": {"url": f"data:image/png;base64,{p['image']}"}}
                            for p in chunk
                        ]
                    ]}
                ],
                temperature=0.0,
                max_tokens=1500,
            )
            content = response.choices[0].message.content.strip()
            if content.strip() and content != "```json```":
                with open(response_path, 'w') as f:
                    f.write(content)
            else:
                print(f"Empty or markdown-only content on chunk {idx+1}, retrying once...")
                time.sleep(2)
                return process_chunk(idx, chunk, log_file)

        if not content:
            print(f"Empty response on chunk {idx+1}.")
            return None

        if content.startswith("```json"):
            content = content[7:]
        if content.endswith("```"):
            content = content[:-3]
        content = content.strip()

        try:
            data = json.loads(content)
            duration = time.time() - start_time
            log_msg = f"Chunk {idx+1} processed successfully in {duration:.2f} seconds."
            print(log_msg)
            if log_file:
                with open(log_file, 'a') as lf:
                    lf.write(log_msg + '\n')
            return data
        except json.JSONDecodeError as e:
            print(f"JSON decoding failed on chunk {idx+1}: {str(e)}")
            print("Raw response content:")
            print(content)
            return None

    except Exception as e:
        error_msg = str(e)
        print(f"An error occurred on chunk {idx+1}: {error_msg}")
        if 'rate_limit' in error_msg or '429' in error_msg:
            print("Rate limit hit. Sleeping for 10 seconds and retrying...")
            time.sleep(10)
            return process_chunk(idx, chunk, log_file, force)
        return None

# Process all chunks in parallel
def extract_invoice_details(pdf_path: str, force: bool = False, log_file: str = None):
    total_start_time = time.time()
    pages = extract_pdf_pages(pdf_path)
    chunks = chunk_pages(pages)

    structured_data = []
    with ThreadPoolExecutor(max_workers=16) as executor:
        futures = [executor.submit(process_chunk, idx, chunk, log_file, force) for idx, chunk in enumerate(chunks)]
        results_with_index = [(future.result(), idx) for idx, future in enumerate(futures)]
    results_with_index.sort(key=lambda x: x[1])
    for result, _ in results_with_index:
        if result is not None:
            structured_data.append(result)

    total_duration = time.time() - total_start_time
    final_log = f"\nTotal extraction time: {total_duration:.2f} seconds."
    print(final_log)
    if log_file:
        with open(log_file, 'a') as lf:
            lf.write(final_log + '\n')

    return structured_data

# Example usage
if __name__ == '__main__':
    parser = argparse.ArgumentParser()
    parser.add_argument('--pdf', type=str, required=True, help='Path to invoice PDF')
    parser.add_argument('--force', action='store_true', help='Force reprocessing all chunks')
    parser.add_argument('--log', type=str, default='processing.log', help='Path to log file')
    args = parser.parse_args()

    pdf_file = args.pdf
    invoice_data = extract_invoice_details(pdf_file, force=args.force, log_file=args.log)
    with open('extracted_invoice_data.json', 'w') as f:
        json.dump(invoice_data, f, indent=2)
    print("Invoice data extraction completed and saved.")
