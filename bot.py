import os
import discord
from discord.ext import commands
from discord import app_commands
from dotenv import load_dotenv
from openai import OpenAI

# Load .env values
load_dotenv()
DISCORD_TOKEN = os.getenv("DISCORD_TOKEN")
OPENAI_API_KEY = os.getenv("OPENAI_API_KEY")
DISCORD_SERVER_ID = os.getenv("DISCORD_SERVER_ID")
GUILD_ID = discord.Object(id=DISCORD_SERVER_ID)  # Replace with your real server ID

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

bot = MyBot()

@bot.event
async def on_ready():
    print(f"✅ Bot is online as {bot.user}")

@bot.tree.command(name="submit_expense", description="Privately submit a receipt for LLM-related expenses.")
async def submit_expense(interaction: discord.Interaction):
    await interaction.response.send_message("📩 Please check your DMs to upload your receipt!", ephemeral=True)

    try:
        dm = await interaction.user.create_dm()
        await dm.send("👋 Hi! Please upload your receipt file (image or PDF). You have 2 minutes.")

        def check(m):
            return m.author == interaction.user and isinstance(m.channel, discord.DMChannel) and m.attachments

        msg = await bot.wait_for("message", check=check, timeout=120)
        attachment = msg.attachments[0]
        file_bytes = await attachment.read()

        # Dummy OCR (replace with real OCR later)
        ocr_text = "Invoice for Groq Inference Usage $136.42"

        # Build LLM prompt
        prompt = f"""
        You are an AI reimbursement validator.
        Review this receipt info and respond in JSON:

        Provider: Groq
        Amount: $136.42
        Description: LLM inference compute
        OCR Extracted Text: {ocr_text}

        Respond like:
        {{
          "provider": "Groq",
          "is_valid": true,
          "reason": "This is a standard LLM inference bill."
        }}
        """

        response = client.chat.completions.create(
            model="gpt-4",
            messages=[{"role": "user", "content": prompt}]
        )

        result = response.choices[0].message.content
        await dm.send(f"✅ Expense validated by AI:\n```json\n{result}\n```")

    except Exception as e:
        await interaction.user.send(f"❌ Something went wrong: {e}")

bot.run(DISCORD_TOKEN)
