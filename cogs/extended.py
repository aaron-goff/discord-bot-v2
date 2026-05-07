import io
import math
import asyncio
import urllib.parse
import discord
from discord import app_commands
from discord.ext import commands
from PIL import Image


RADAR_ZOOM = 7  # each tile ~310km wide; 3×3 grid covers ~930km
TILE_SIZE = 256


def _lat_lon_to_tile(lat: float, lon: float, zoom: int) -> tuple[int, int]:
    lat_rad = math.radians(lat)
    n = 2 ** zoom
    x = int((lon + 180.0) / 360.0 * n)
    y = int((1.0 - math.log(math.tan(lat_rad) + 1.0 / math.cos(lat_rad)) / math.pi) / 2.0 * n)
    return x, y


class ExtendedSlash(commands.Cog):

    def __init__(self, bot):
        self.bot = bot

    @app_commands.command(name="weather", description="Get the current weather for a location")
    @app_commands.describe(location="City, zip code, or address (e.g. Washington DC, 20001)")
    async def weather(self, interaction: discord.Interaction, location: str):
        await interaction.response.defer()

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

    @app_commands.command(name="radar", description="Show a weather radar map for a location")
    @app_commands.describe(location="City, zip code, or address (e.g. Washington DC, 20001)")
    async def radar(self, interaction: discord.Interaction, location: str):
        await interaction.response.defer()

        session = await self.bot.mlb_client.get_session()

        # 1. Geocode location via Nominatim
        geo_url = f"https://nominatim.openstreetmap.org/search?q={urllib.parse.quote(location)}&format=json&limit=1"
        try:
            async with session.get(geo_url, headers={"User-Agent": "discord-bot/1.0"}) as resp:
                geo_data = await resp.json() if resp.status == 200 else []
        except Exception as e:
            await interaction.followup.send(f"Error geocoding location: {e}")
            return

        if not geo_data:
            await interaction.followup.send(f"Could not find location: **{location}**")
            return

        lat = float(geo_data[0]['lat'])
        lon = float(geo_data[0]['lon'])
        display_name = geo_data[0].get('display_name', location).split(',')[0].strip()

        # 2. Get latest RainViewer radar frame
        try:
            async with session.get("https://api.rainviewer.com/public/weather-maps.json") as resp:
                rv_data = await resp.json() if resp.status == 200 else {}
        except Exception as e:
            await interaction.followup.send(f"Error fetching radar data: {e}")
            return

        past_frames = rv_data.get('radar', {}).get('past', [])
        if not past_frames:
            await interaction.followup.send("Radar data is currently unavailable.")
            return

        rv_host = rv_data['host']
        rv_path = past_frames[-1]['path']

        # 3. Compute center tile and build 3×3 grid
        cx, cy = _lat_lon_to_tile(lat, lon, RADAR_ZOOM)
        grid = 3
        canvas_size = TILE_SIZE * grid

        base_img = Image.new('RGBA', (canvas_size, canvas_size), (200, 200, 200, 255))
        radar_img = Image.new('RGBA', (canvas_size, canvas_size), (0, 0, 0, 0))

        async def fetch_tile(url):
            try:
                async with session.get(url, headers={"User-Agent": "discord-bot/1.0"}) as resp:
                    if resp.status == 200:
                        return await resp.read()
            except Exception:
                pass
            return None

        # Fetch all tiles concurrently
        offsets = [(dx, dy) for dy in range(-1, 2) for dx in range(-1, 2)]
        osm_tasks = [fetch_tile(f"https://tile.openstreetmap.org/{RADAR_ZOOM}/{cx+dx}/{cy+dy}.png") for dx, dy in offsets]
        radar_tasks = [fetch_tile(f"{rv_host}{rv_path}/{RADAR_ZOOM}/{cx+dx}/{cy+dy}/2/1_1.png") for dx, dy in offsets]

        osm_results, radar_results = await asyncio.gather(
            asyncio.gather(*osm_tasks),
            asyncio.gather(*radar_tasks),
        )

        # 4. Composite tiles
        loop = asyncio.get_event_loop()

        def build_image():
            for i, (dx, dy) in enumerate(offsets):
                px = (dx + 1) * TILE_SIZE
                py = (dy + 1) * TILE_SIZE

                if osm_results[i]:
                    tile = Image.open(io.BytesIO(osm_results[i])).convert('RGBA')
                    base_img.paste(tile, (px, py))

                if radar_results[i]:
                    tile = Image.open(io.BytesIO(radar_results[i])).convert('RGBA')
                    radar_img.paste(tile, (px, py))

            composited = Image.alpha_composite(base_img, radar_img)
            buf = io.BytesIO()
            composited.convert('RGB').save(buf, format='JPEG', quality=85)
            buf.seek(0)
            return buf

        buf = await loop.run_in_executor(None, build_image)

        embed = discord.Embed(
            title=f"🌧️ Radar — {display_name}",
            color=discord.Color.blue()
        )
        embed.set_image(url="attachment://radar.jpg")
        embed.set_footer(text="Base map: OpenStreetMap · Radar: RainViewer")

        await interaction.followup.send(embed=embed, file=discord.File(buf, filename="radar.jpg"))


async def setup(bot):
    await bot.add_cog(ExtendedSlash(bot))
