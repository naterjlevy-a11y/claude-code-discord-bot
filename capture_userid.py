"""One-shot: log in, wait for a message in the target channel, print the author's
user ID, then exit. Used to grab ALLOWED_USER_IDS without Discord Developer Mode."""
import os
import sys
from pathlib import Path

import discord
from dotenv import load_dotenv

load_dotenv()
TOKEN = os.environ.get("DISCORD_TOKEN", "").strip()
CHANNEL_ID = int(os.environ.get("ALLOWED_CHANNEL_IDS", "0").split(",")[0] or 0)

intents = discord.Intents.default()
intents.message_content = True
client = discord.Client(intents=intents)


@client.event
async def on_ready():
    print(f"READY as {client.user}", flush=True)
    ch = client.get_channel(CHANNEL_ID)
    if ch:
        try:
            await ch.send("👋 Setup check — send any message here so I can grab your user ID.")
        except discord.HTTPException as e:
            print(f"send failed: {e}", flush=True)
    else:
        print(f"WARN: channel {CHANNEL_ID} not visible to the bot", flush=True)


@client.event
async def on_message(message: discord.Message):
    if message.author.bot:
        return
    if CHANNEL_ID and message.channel.id != CHANNEL_ID:
        return
    print(f"CAPTURED_USER_ID={message.author.id} NAME={message.author}", flush=True)
    try:
        await message.channel.send(f"✅ Got it — your user ID is `{message.author.id}`. Locking the bot to you now.")
    except discord.HTTPException:
        pass
    await client.close()


if __name__ == "__main__":
    if not TOKEN or not CHANNEL_ID:
        print("Missing token or channel id", flush=True)
        sys.exit(1)
    client.run(TOKEN)
