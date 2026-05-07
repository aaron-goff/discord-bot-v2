import discord
from discord import app_commands
from discord.ext import commands


class ExtendedSlash(commands.Cog):

    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="weather", description="Get the current weather for a location")
    @app_commands.describe(location="City, zip code, or address (e.g. Washington DC, 20001)")
    async def weather(self, interaction: discord.Interaction, location: str):
        await interaction.response.defer()

        import urllib.parse
        encoded = urllib.parse.quote(location)
        url = f"https://wttr.in/{encoded}?format=j1"

        session = await self.bot.mlb_client.get_session()
        try:
            async with session.get(url, headers={"User-Agent": "discord-bot/1.0"}) as resp:
                if resp.status != 200:
                    await interaction.followup.send(f"Could not fetch weather for **{location}**.")
                    return
                data = await resp.json(content_type=None)
        except Exception as e:
            await interaction.followup.send(f"Error fetching weather: {e}")
            return

        current = data.get("current_condition", [{}])[0]
        area = data.get("nearest_area", [{}])[0]

        area_name = area.get("areaName", [{}])[0].get("value", location)
        region = area.get("region", [{}])[0].get("value", "")
        country = area.get("country", [{}])[0].get("value", "")
        location_str = area_name
        if region and region != area_name:
            location_str += f", {region}"
        if country and country not in ("United States of America", ""):
            location_str += f", {country}"

        desc = current.get("weatherDesc", [{}])[0].get("value", "Unknown")
        temp_f = current.get("temp_F", "?")
        temp_c = current.get("temp_C", "?")
        feels_f = current.get("FeelsLikeF", "?")
        feels_c = current.get("FeelsLikeC", "?")
        humidity = current.get("humidity", "?")
        wind_mph = current.get("windspeedMiles", "?")
        wind_dir = current.get("winddir16Point", "")
        uv = current.get("uvIndex", "?")
        visibility = current.get("visibility", "?")
        precip = current.get("precipInches", "0.0")

        # Pick an emoji based on description
        desc_lower = desc.lower()
        if "thunder" in desc_lower:
            icon = "⛈️"
        elif "snow" in desc_lower or "blizzard" in desc_lower:
            icon = "❄️"
        elif "rain" in desc_lower or "drizzle" in desc_lower or "shower" in desc_lower:
            icon = "🌧️"
        elif "overcast" in desc_lower or "cloudy" in desc_lower:
            icon = "☁️"
        elif "partly" in desc_lower or "mist" in desc_lower or "fog" in desc_lower:
            icon = "⛅"
        elif "sunny" in desc_lower or "clear" in desc_lower:
            icon = "☀️"
        else:
            icon = "🌡️"

        wind_str = f"{wind_mph} mph {wind_dir}".strip()

        embed = discord.Embed(
            title=f"{icon} {desc} — {location_str}",
            color=discord.Color.blue()
        )
        embed.add_field(name="Temperature", value=f"{temp_f}°F / {temp_c}°C", inline=True)
        embed.add_field(name="Feels Like", value=f"{feels_f}°F / {feels_c}°C", inline=True)
        embed.add_field(name="Humidity", value=f"{humidity}%", inline=True)
        embed.add_field(name="Wind", value=wind_str, inline=True)
        embed.add_field(name="UV Index", value=str(uv), inline=True)
        embed.add_field(name="Visibility", value=f"{visibility} mi", inline=True)
        if float(precip) > 0:
            embed.add_field(name="Precipitation", value=f"{precip} in", inline=True)

        await interaction.followup.send(embed=embed)


async def setup(bot):
    await bot.add_cog(ExtendedSlash(bot))
