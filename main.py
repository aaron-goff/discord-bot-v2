import asyncio
import os
import discord
from discord.ext import commands
from core.mlb_client import MLBClient
from dotenv import load_dotenv

# Modern Discord bots require explicit Intents
intents = discord.Intents.default()
intents.message_content = True  # Enable if you also want it to read regular messages eventually

class ModernNatsBot(commands.Bot):
    def __init__(self):
        # A command_prefix is still required by the superclass, but we'll be using slash commands
        super().__init__(command_prefix='!', intents=intents)
        self.mlb_client = MLBClient()

    async def setup_hook(self):
        # Load our new modern cog
        await self.load_extension('cogs.mlb')

        # Load the live game monitor (no-hitters, big HRs)
        await self.load_extension('cogs.monitor')

        # Load optional extended commands (weather, etc.) if enabled
        if os.getenv("EXTENDED_COMMANDS", "").lower() in ("1", "true", "yes"):
            await self.load_extension('cogs.extended')
            print("Extended commands enabled.")
        
        # Sync slash commands in the background so startup isn't blocked
        async def _sync():
            try:
                synced = await self.tree.sync()
                print(f"Slash commands synced globally! ({len(synced)} commands)")
            except Exception as e:
                print(f"Command sync error: {e}")
        asyncio.create_task(_sync())
        
    async def close(self):
        # Cleanly close the aiohttp session when the bot shuts down
        await self.mlb_client.close()
        await super().close()

bot = ModernNatsBot()

@bot.event
async def on_ready():
    print(f'Logged in as {bot.user.name} (ID: {bot.user.id})')
    print('------')

if __name__ == "__main__":
    # Load environment variables from a .env file if it exists
    load_dotenv()
    
    discord_token = os.getenv("DISCORD_TOKEN")
    if not discord_token:
        raise ValueError("No DISCORD_TOKEN found in environment variables. Make sure you have a .env file setup!")

    bot.run(discord_token)