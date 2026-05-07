import io
import math
import asyncio
import urllib.parse
import discord
from discord import app_commands
from discord.ext import commands
from PIL import Image


MAP_ZOOM = 8     # base map zoom — each tile ~155km, 3×3 grid ≈ 465km wide
RADAR_ZOOM = 6   # max zoom level RainViewer radar supports
TILE_SIZE = 256
SCALE = 2 ** (MAP_ZOOM - RADAR_ZOOM)  # 4: one radar tile = 4×4 base-map tiles


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

        # 1. Geocode via Nominatim; bias to US for bare zip codes
        is_us_zip = location.strip().replace('-', '').isdigit() and len(location.strip()) in (5, 9)
        country_param = "&countrycodes=us" if is_us_zip else ""
        geo_url = f"https://nominatim.openstreetmap.org/search?q={urllib.parse.quote(location)}&format=json&limit=1{country_param}"
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

        # 3. Compute center tiles at each zoom
        cx, cy = _lat_lon_to_tile(lat, lon, MAP_ZOOM)

        # Base map: 3×3 grid at MAP_ZOOM
        map_offsets = [(dx, dy) for dy in range(-1, 2) for dx in range(-1, 2)]

        # Radar: find which RADAR_ZOOM tiles cover the base map extent
        rx_min = (cx - 1) // SCALE
        rx_max = (cx + 1) // SCALE
        ry_min = (cy - 1) // SCALE
        ry_max = (cy + 1) // SCALE
        radar_offsets = [(rx, ry) for ry in range(ry_min, ry_max + 1) for rx in range(rx_min, rx_max + 1)]

        async def fetch(url):
            try:
                async with session.get(url, headers={"User-Agent": "discord-bot/1.0"}) as resp:
                    if resp.status == 200:
                        return await resp.read()
            except Exception:
                pass
            return None

        map_tasks = [fetch(f"https://tile.openstreetmap.org/{MAP_ZOOM}/{cx+dx}/{cy+dy}.png") for dx, dy in map_offsets]
        radar_tasks = [fetch(f"{rv_host}{rv_path}/256/{RADAR_ZOOM}/{rx}/{ry}/2/1_1.png") for rx, ry in radar_offsets]

        map_results, radar_results = await asyncio.gather(
            asyncio.gather(*map_tasks),
            asyncio.gather(*radar_tasks),
        )

        # 4. Build composite in executor
        loop = asyncio.get_event_loop()

        def build_image():
            canvas_size = TILE_SIZE * 3  # 768×768

            # Stitch base map
            base_img = Image.new('RGBA', (canvas_size, canvas_size), (180, 180, 180, 255))
            for i, (dx, dy) in enumerate(map_offsets):
                if map_results[i]:
                    tile = Image.open(io.BytesIO(map_results[i])).convert('RGBA')
                    base_img.paste(tile, ((dx + 1) * TILE_SIZE, (dy + 1) * TILE_SIZE))

            # Stitch radar tiles at RADAR_ZOOM, then scale up and crop to align
            r_cols = rx_max - rx_min + 1
            r_rows = ry_max - ry_min + 1
            radar_canvas = Image.new('RGBA', (r_cols * TILE_SIZE, r_rows * TILE_SIZE), (0, 0, 0, 0))
            for i, (rx, ry) in enumerate(radar_offsets):
                if radar_results[i]:
                    tile = Image.open(io.BytesIO(radar_results[i])).convert('RGBA')
                    radar_canvas.paste(tile, ((rx - rx_min) * TILE_SIZE, (ry - ry_min) * TILE_SIZE))

            # Scale radar up to base map pixel space
            radar_scaled = radar_canvas.resize(
                (r_cols * TILE_SIZE * SCALE, r_rows * TILE_SIZE * SCALE),
                Image.NEAREST
            )

            # Crop radar to align with base map canvas
            # Base map top-left is global pixel (cx-1, cy-1) at MAP_ZOOM
            # Radar top-left is global pixel (rx_min * SCALE, ry_min * SCALE) at MAP_ZOOM
            crop_x = (cx - 1) - rx_min * SCALE
            crop_y = (cy - 1) - ry_min * SCALE
            crop_x_px = crop_x * TILE_SIZE
            crop_y_px = crop_y * TILE_SIZE
            radar_overlay = radar_scaled.crop((crop_x_px, crop_y_px, crop_x_px + canvas_size, crop_y_px + canvas_size))

            composited = Image.alpha_composite(base_img, radar_overlay)
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
