import os
import io
import base64
import json
import re
import discord
from discord.ext import commands
from discord import app_commands
from dotenv import load_dotenv
from openai import OpenAI
from pdf2image import convert_from_bytes
from PIL import Image
import aiosqlite
from datetime import datetime

# Load environment variables
load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
DISCORD_SERVER_ID = os.getenv("DISCORD_SERVER_ID")
GUILD_ID = discord.Object(id=int(DISCORD_SERVER_ID))

# Ensure uploads directory exists
os.makedirs("uploads", exist_ok=True)

# Initialize OpenAI client
client = OpenAI(api_key=OPENAI_API_KEY)

# Bot setup
class MyBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.dm_messages = True
        super().__init__(command_prefix="/", intents=intents)

    async def setup_hook(self):
        self.tree.copy_global_to(guild=GUILD_ID)
        await self.tree.sync(guild=GUILD_ID)

        async with aiosqlite.connect("expenses.db") as db:
            await db.execute("DROP TABLE IF EXISTS expenses")
            await db.execute("""
                CREATE TABLE expenses (
                    id INTEGER PRIMARY KEY AUTOINCREMENT,
                    user_id TEXT,
                    username TEXT,
                    user_input_raw TEXT,
                    user_input_amount TEXT,
                    user_input_reason TEXT,
                    requested_amount TEXT,
                    user_reason TEXT,
                    extracted_json TEXT,
                    match_status TEXT,
                    file_name TEXT,
                    invoice_date TEXT,
                    invoice_number TEXT,
                    invoice_account_id TEXT,
                    provider TEXT,
                    billing_period TEXT,
                    payment_method TEXT,
                    tax_amount TEXT,
                    total_amount TEXT,
                    llm_total_amount TEXT,
                    line_items TEXT,
                    extra_data TEXT,
                    created_at TEXT DEFAULT CURRENT_TIMESTAMP
                )
            """)
            await db.commit()

bot = MyBot()

@bot.event
async def on_ready():
    print(f"✅ Bot is online as {bot.user}")

@bot.tree.command(name="submit_expense", description="Privately submit a receipt for LLM-related expenses.")
async def submit_expense(interaction: discord.Interaction):
    await interaction.response.send_message("📩 Please check your DMs to upload your receipt!", ephemeral=True)

    try:
        dm = await interaction.user.create_dm()
        await dm.send("👋 Hi! Please upload your **receipt file** (image or PDF). You have 2 minutes.")

        def attachment_check(m):
            return m.author == interaction.user and isinstance(m.channel, discord.DMChannel) and m.attachments

        receipt_msg = await bot.wait_for("message", check=attachment_check, timeout=120)
        attachment = receipt_msg.attachments[0]
        file_bytes = await attachment.read()
        file_name = attachment.filename

        # Save file to uploads directory
        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        safe_filename = f"{interaction.user.id}_{timestamp}_{file_name}"
        save_path = os.path.join("uploads", safe_filename)
        with open(save_path, "wb") as f:
            f.write(file_bytes)

        # Convert receipt to image
        image = (
            convert_from_bytes(file_bytes)[0]
            if file_name.lower().endswith(".pdf")
            else Image.open(io.BytesIO(file_bytes))
        )

        # Encode image
        buffered = io.BytesIO()
        image.save(buffered, format="PNG")
        data_url = f"data:image/png;base64,{base64.b64encode(buffered.getvalue()).decode()}"

        # Ask for combined user input
        await dm.send("💬 Please enter your **requested amount and purpose** in one line (e.g., `$136.42 for March compute`):")

        def message_check(m):
            return m.author == interaction.user and isinstance(m.channel, discord.DMChannel)

        input_msg = await bot.wait_for("message", check=message_check, timeout=90)
        combined_input = input_msg.content.strip()

        # Extract amount and reason using LLM
        user_input_prompt = [
            {
                "role": "user",
                "content": (
                    f"Extract the **amount** and **reason** from this input:\n\n\"{combined_input}\"\n\n"
                    "Return JSON like:\n"
                    '{\n  "amount": "$136.42",\n  "reason": "March compute"\n}'
                )
            }
        ]
        user_input_resp = client.chat.completions.create(
            model="gpt-4-turbo",
            messages=user_input_prompt,
            max_tokens=100
        )
        extracted_user_input = json.loads(user_input_resp.choices[0].message.content.strip())
        reimbursement_amount = extracted_user_input.get("amount", "").replace("$", "").strip()
        reimbursement_reason = extracted_user_input.get("reason", "").strip()

        # Check amount limit
        try:
            if float(reimbursement_amount) > 6000:
                await dm.send("🚫 The requested amount exceeds the $6000/month limit. Please revise and resubmit.")
                return
        except ValueError:
            await dm.send("❌ Could not parse the amount. Please try again.")
            return

        # Vision prompt
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
                            f"The user submitted:\n- Amount: ${reimbursement_amount}\n- Reason: {reimbursement_reason}\n\n"
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
                            "}\n\n"
                            "Include all line items, and ensure values like account IDs and LLM-related services are captured if visible. Respond with valid JSON only."
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
            model="gpt-4-turbo",
            messages=vision_prompt,
            max_tokens=1000
        )
        result_text = vision_resp.choices[0].message.content.strip()

        # Ensure it's not empty and valid before attempting JSON parse
        if result_text.startswith("```"):
            result_text = result_text.strip("` \n")
            if result_text.startswith("json"):
                result_text = result_text[len("json"):].strip()

        # Abort early if no JSON present
        if not result_text or not result_text.startswith("{"):
            await dm.send("❌ LLM response did not contain valid JSON. Please try a clearer receipt image or PDF.")
            print(f"[LLM JSON Error] result_text: {result_text}")
            return

        # Set defaults
        extracted_json = {}
        invoice_date = invoice_number = provider = billing_period = ""
        payment_method = tax_amount = total_amount = line_items = extra_data_str = ""
        match_status = "⚠️ Parsing failed"
        llm_total_amount = "0.00"
        invoice_account_id = (
            extracted_json.get("invoice_account_id") or
            extracted_json.get("payer_account_id") or
            extracted_json.get("account_id") or
            ""
        )

        try:
            extracted_json = json.loads(result_text)
            raw_items = extracted_json.get("line_items", [])
            total_amount_val = float(extracted_json.get("total_amount", "0").replace("$", "").replace(",", ""))
            user_amount_val = float(reimbursement_amount.replace(",", ""))

            match_status = "✅ Match" if abs(total_amount_val - user_amount_val) < 0.01 else "❗Mismatch"

            llm_terms = [
                "llm", "language model", "openai", "gpt", "chatgpt", "anthropic",
                "claude", "bedrock", "cohere", "mistral", "meta llama", "llama", "inference"
            ]

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

            extracted_json["line_items"] = llm_items
            extracted_json["llm_total_amount"] = f"${llm_total:.2f}"
            llm_total_amount = f"${llm_total:.2f}"

            invoice_date = extracted_json.get("invoice_date", "")
            invoice_number = extracted_json.get("invoice_number", "")
            provider = extracted_json.get("provider", "")
            billing_period = extracted_json.get("billing_period", "")
            payment_method = extracted_json.get("payment_method", "")
            tax_amount = extracted_json.get("tax_amount", "")
            total_amount = extracted_json.get("total_amount", "")
            line_items = json.dumps(llm_items)
            extra_data_str = json.dumps({k: v for k, v in extracted_json.items() if k not in {
                "invoice_date", "invoice_number", "provider", "billing_period",
                "payment_method", "amount", "tax_amount", "total_amount", "line_items", "llm_total_amount"
            }})

        except Exception as e:
            print(f"[Receipt Parse Error] raw result_text: {result_text}")
            extracted_json = {"error": f"Error parsing receipt JSON: {e}"}

        await dm.send(f"✅ AI Response:\n```json\n{json.dumps(extracted_json, indent=2)}\n```")
        await dm.send(f"🔍 Amount Match Check: {match_status}")

        # Store in DB
        async with aiosqlite.connect("expenses.db") as db:
            await db.execute("""
                INSERT INTO expenses (
                    user_id, username, user_input_raw, user_input_amount, user_input_reason,
                    requested_amount, user_reason,
                    extracted_json, match_status, file_name,
                    invoice_date, invoice_number, invoice_account_id, provider, billing_period,
                    payment_method, tax_amount, total_amount, llm_total_amount,
                    line_items, extra_data
                )
                VALUES (?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?, ?)
            """, (
                str(interaction.user.id),
                str(interaction.user),
                combined_input,
                f"${reimbursement_amount}",
                reimbursement_reason,
                f"${reimbursement_amount}",
                reimbursement_reason,
                json.dumps(extracted_json),
                match_status,
                safe_filename,
                invoice_date,
                invoice_number,
                invoice_account_id,
                provider,
                billing_period,
                payment_method,
                tax_amount,
                total_amount,
                llm_total_amount,
                line_items,
                extra_data_str
            ))
            await db.commit()

        # Confirmation message
        await dm.send("🎉 Your expense has been successfully processed and recorded. Thank you!")

    except Exception as e:
        await interaction.user.send(f"❌ Something went wrong: {e}")
        print(f"[Bot Error] {e}")

bot.run(DISCORD_TOKEN)
