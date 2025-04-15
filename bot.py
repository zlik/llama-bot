import io
import json
import os
from datetime import datetime

import discord
from db import init_db, insert_expense
from discord import app_commands
from discord.ext import commands
from dotenv import load_dotenv

from llm_handler import (
    extract_llm_amount_and_items,
    extract_text_from_combined_input,
    parse_receipt_with_vision,
    prepare_image_data_url,
)

# Load environment variables
load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
DISCORD_SERVER_ID = os.getenv("DISCORD_SERVER_ID")
GUILD_ID = discord.Object(id=int(DISCORD_SERVER_ID))

# Ensure uploads directory exists
os.makedirs("uploads", exist_ok=True)


class MyBot(commands.Bot):
    def __init__(self):
        intents = discord.Intents.default()
        intents.message_content = True
        intents.dm_messages = True
        super().__init__(command_prefix="/", intents=intents)

    async def setup_hook(self):
        self.tree.copy_global_to(guild=GUILD_ID)
        await self.tree.sync(guild=GUILD_ID)
        await init_db()


bot = MyBot()


@bot.event
async def on_ready():
    print(f"✅ Bot is online as {bot.user}")


@bot.tree.command(
    name="submit_expense",
    description="Privately submit a receipt for LLM-related expenses.",
)
async def submit_expense(interaction: discord.Interaction):
    await interaction.response.send_message(
        "📩 Please check your DMs to upload your receipt!", ephemeral=True
    )

    try:
        dm = await interaction.user.create_dm()
        await dm.send(
            "👋 Hi! Please upload your **receipt file** (image or PDF). You have 2 minutes."
        )

        def attachment_check(m):
            return (
                m.author == interaction.user
                and isinstance(m.channel, discord.DMChannel)
                and m.attachments
            )

        receipt_msg = await bot.wait_for("message", check=attachment_check, timeout=120)
        attachment = receipt_msg.attachments[0]
        file_bytes = await attachment.read()
        file_name = attachment.filename

        timestamp = datetime.now().strftime("%Y%m%d%H%M%S")
        safe_filename = f"{interaction.user.id}_{timestamp}_{file_name}"
        save_path = os.path.join("uploads", safe_filename)
        with open(save_path, "wb") as f:
            f.write(file_bytes)

        data_url = prepare_image_data_url(file_bytes, file_name)

        await dm.send(
            "💬 Please enter your **requested amount and purpose** in one line (e.g., `$136.42 for March compute`):"
        )

        def message_check(m):
            return m.author == interaction.user and isinstance(
                m.channel, discord.DMChannel
            )

        input_msg = await bot.wait_for("message", check=message_check, timeout=90)

        combined_input = input_msg.content.strip()

        extracted_user_input = extract_text_from_combined_input(combined_input)
        reimbursement_amount = (
            extracted_user_input.get("amount", "").replace("$", "").strip()
        )
        reimbursement_reason = extracted_user_input.get("reason", "").strip()

        try:
            if float(reimbursement_amount) > 6000:
                await dm.send(
                    "🚫 The requested amount exceeds the $6000/month limit. Please revise and resubmit."
                )
                return
        except ValueError:
            await dm.send("❌ Could not parse the amount. Please try again.")
            return

        await dm.send("✅ OK! ⏳ Processing your invoice, please wait a sec...")

        extracted_json = parse_receipt_with_vision(
            reimbursement_amount, reimbursement_reason, data_url
        )

        llm_items, llm_total_amount = extract_llm_amount_and_items(extracted_json)
        extracted_json["line_items"] = llm_items
        extracted_json["llm_total_amount"] = llm_total_amount

        invoice_date = extracted_json.get("invoice_date", "")
        invoice_number = extracted_json.get("invoice_number", "")
        invoice_account_id = (
            extracted_json.get("invoice_account_id")
            or extracted_json.get("payer_account_id")
            or extracted_json.get("account_id")
            or ""
        )
        provider = extracted_json.get("provider", "")
        billing_period = extracted_json.get("billing_period", "")
        payment_method = extracted_json.get("payment_method", "")
        tax_amount = extracted_json.get("tax_amount", "")
        total_amount = extracted_json.get("total_amount", "")
        line_items = json.dumps(llm_items)
        extra_data_str = json.dumps(
            {
                k: v
                for k, v in extracted_json.items()
                if k
                not in {
                    "invoice_date",
                    "invoice_number",
                    "provider",
                    "billing_period",
                    "payment_method",
                    "amount",
                    "tax_amount",
                    "total_amount",
                    "line_items",
                    "llm_total_amount",
                }
            }
        )

        try:
            total_amount_val = float(llm_total_amount.replace("$", "").replace(",", ""))
            user_amount_val = float(reimbursement_amount.replace(",", ""))
            match_status = (
                "✅ Match"
                if abs(total_amount_val - user_amount_val) < 0.01
                else "❗Mismatch"
            )
        except Exception:
            match_status = "⚠️ Parsing failed"

        details = {
            "User Input": combined_input,
            "Requested Amount": f"${reimbursement_amount}",
            "Reason": reimbursement_reason,
            "Match Status": match_status,
            "Saved File Path": save_path,
        }

        parsed_data = extracted_json
        parsed_data.pop("line_items", None)
        await dm.send(
            f"✅ Line Items:\n```json\n{json.dumps(llm_items, indent=2)}\n```"
        )
        await dm.send(
            f"✅ Parsed Data:\n```json\n{json.dumps(parsed_data, indent=2)}\n```"
        )
        await dm.send(
            f"✅ Submission Summary:\n```json\n{json.dumps(details, indent=2)}\n```"
        )

        await dm.send(f"🔍 Llama Amount Match Check: {match_status}")

        await insert_expense(
            (
                str(interaction.user.id),
                str(interaction.user),
                combined_input,
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
                extra_data_str,
            )
        )

        await dm.send(
            "🎉 Your expense has been successfully processed and recorded. Thank you!"
        )

    except Exception as e:
        await interaction.user.send(f"❌ Something went wrong: {e}")
        print(f"[Bot Error] {e}")


bot.run(DISCORD_TOKEN)
