import io
import math
import asyncio
import urllib.parse
import discord
from discord import app_commands
from discord.ext import commands
from PIL import Image


MAP_ZOOM = 8     # base map zoom — each tile ~155km
RADAR_ZOOM = 6   # max zoom level RainViewer radar supports
TILE_SIZE = 256
SCALE = 2 ** (MAP_ZOOM - RADAR_ZOOM)  # 4: one radar tile = 4×4 base-map tiles
OUTPUT_SIZE = 768  # final image size
GRID = 5  # fetch 5×5 tiles then crop to center on the exact query point


def _lat_lon_to_pixel(lat: float, lon: float, zoom: int) -> tuple[float, float]:
    """Global pixel position of (lat, lon) at the given zoom level."""
    lat_rad = math.radians(lat)
    n = 2 ** zoom
    gx = (lon + 180.0) / 360.0 * n * TILE_SIZE
    gy = (1.0 - math.log(math.tan(lat_rad) + 1.0 / math.cos(lat_rad)) / math.pi) / 2.0 * n * TILE_SIZE
    return gx, gy


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

        # 3. Compute center tile and exact sub-tile pixel position of the query point
        gx, gy = _lat_lon_to_pixel(lat, lon, MAP_ZOOM)  # global pixel at MAP_ZOOM
        cx = int(gx // TILE_SIZE)
        cy = int(gy // TILE_SIZE)

        # Fetch a GRID×GRID tile canvas so we have enough room to crop centered on (gx, gy)
        half = GRID // 2  # 2 for a 5×5 grid
        map_offsets = [(dx, dy) for dy in range(-half, half + 1) for dx in range(-half, half + 1)]

        # Radar: find which RADAR_ZOOM tiles cover the full GRID×GRID map extent
        rx_min = (cx - half) // SCALE
        rx_max = (cx + half) // SCALE
        ry_min = (cy - half) // SCALE
        ry_max = (cy + half) // SCALE
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
            canvas_size = TILE_SIZE * GRID  # 1280×1280

            # Stitch base map
            base_img = Image.new('RGBA', (canvas_size, canvas_size), (180, 180, 180, 255))
            for i, (dx, dy) in enumerate(map_offsets):
                if map_results[i]:
                    tile = Image.open(io.BytesIO(map_results[i])).convert('RGBA')
                    base_img.paste(tile, ((dx + half) * TILE_SIZE, (dy + half) * TILE_SIZE))

            # Stitch radar tiles at RADAR_ZOOM, scale up and align with base map canvas
            r_cols = rx_max - rx_min + 1
            r_rows = ry_max - ry_min + 1
            radar_canvas = Image.new('RGBA', (r_cols * TILE_SIZE, r_rows * TILE_SIZE), (0, 0, 0, 0))
            for i, (rx, ry) in enumerate(radar_offsets):
                if radar_results[i]:
                    tile = Image.open(io.BytesIO(radar_results[i])).convert('RGBA')
                    radar_canvas.paste(tile, ((rx - rx_min) * TILE_SIZE, (ry - ry_min) * TILE_SIZE))

            radar_scaled = radar_canvas.resize(
                (r_cols * TILE_SIZE * SCALE, r_rows * TILE_SIZE * SCALE),
                Image.NEAREST
            )

            # Crop radar to align with the base map canvas top-left corner
            crop_x_px = ((cx - half) - rx_min * SCALE) * TILE_SIZE
            crop_y_px = ((cy - half) - ry_min * SCALE) * TILE_SIZE
            radar_overlay = radar_scaled.crop((crop_x_px, crop_y_px, crop_x_px + canvas_size, crop_y_px + canvas_size))

            composited = Image.alpha_composite(base_img, radar_overlay)

            # Crop the large canvas to OUTPUT_SIZE centered on the exact query pixel
            qx = int(gx - (cx - half) * TILE_SIZE)  # query pixel x within canvas
            qy = int(gy - (cy - half) * TILE_SIZE)  # query pixel y within canvas
            half_out = OUTPUT_SIZE // 2
            left = max(0, min(qx - half_out, canvas_size - OUTPUT_SIZE))
            top  = max(0, min(qy - half_out, canvas_size - OUTPUT_SIZE))
            composited = composited.crop((left, top, left + OUTPUT_SIZE, top + OUTPUT_SIZE))

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
