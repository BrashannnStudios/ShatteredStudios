import os
import re
import asyncio
import datetime
from typing import Optional, List, Dict, Any
from threading import Thread

import discord
from discord import app_commands
from discord.ext import commands, tasks
from motor.motor_asyncio import AsyncIOMotorClient
from flask import Flask
from dotenv import load_dotenv

load_dotenv()

# ──────────────────────────────────────────────
# Keep-alive (Render + UptimeRobot)
# ──────────────────────────────────────────────
app = Flask(__name__)

@app.route("/")
def home():
    return "Shattered Icons is online"

def run_flask():
    port = int(os.environ.get("PORT", 8080))
    app.run(host="0.0.0.0", port=port)

Thread(target=run_flask, daemon=True).start()

# ──────────────────────────────────────────────
# Configuración
# ──────────────────────────────────────────────
TOKEN = os.getenv("DISCORD_TOKEN")
MONGO_URI = os.getenv("MONGO_URI")

if not TOKEN or not MONGO_URI:
    raise RuntimeError("Faltan DISCORD_TOKEN o MONGO_URI en las variables de entorno")

SYSTEM_COLOR = 0xcef3f1
EMBED_COLOR = discord.Color(SYSTEM_COLOR)

intents = discord.Intents.default()
intents.members = True
intents.message_content = True
intents.guilds = True
intents.moderation = True

bot = commands.Bot(
    command_prefix="?",
    intents=intents,
    case_insensitive=True,
    help_command=None,
    activity=discord.Activity(type=discord.ActivityType.watching, name="Shattered Icons")
)

# ──────────────────────────────────────────────
# MongoDB
# ──────────────────────────────────────────────
mongo_client = AsyncIOMotorClient(
    MONGO_URI,
    tls=True,
    tlsAllowInvalidCertificates=True,  # Evita el error SSL en Render
    serverSelectionTimeoutMS=30000
)

db = mongo_client["shattered_icons"]

guilds_col = db["guilds"]
warnings_col = db["warnings"]
notes_col = db["notes"]
temp_actions_col = db["temp_actions"]

# ──────────────────────────────────────────────
# Helpers
# ──────────────────────────────────────────────
def parse_duration(time_str: str) -> Optional[int]:
    """Convierte 30s / 5m / 2h / 1d / 1w a segundos. Retorna None si es inválido."""
    if not time_str:
        return None
    match = re.fullmatch(r"(\d+)([smhdw])", time_str.lower().strip())
    if not match:
        return None
    value, unit = match.groups()
    multipliers = {"s": 1, "m": 60, "h": 3600, "d": 86400, "w": 604800}
    return int(value) * multipliers[unit]


def format_timedelta(seconds: int) -> str:
    if seconds < 60:
        return f"{seconds}s"
    elif seconds < 3600:
        return f"{seconds // 60}m"
    elif seconds < 86400:
        return f"{seconds // 3600}h"
    elif seconds < 604800:
        return f"{seconds // 86400}d"
    else:
        return f"{seconds // 604800}w"


async def get_guild_config(guild_id: int) -> Dict[str, Any]:
    doc = await guilds_col.find_one({"_id": guild_id})
    if not doc:
        default = {
            "_id": guild_id,
            "welcome": {
                "channel_id": None,
                "message": "¡Bienvenido {user} a **{server}**!\nAhora somos {membercount} miembros.",
                "color": SYSTEM_COLOR,
                "footer": "Shattered Icons • Disfruta tu estadía",
                "image_url": None,
                "recommended_channels": []
            },
            "logs_channel_id": None,
            "staff_roles": [],
            "admin_roles": []
        }
        await guilds_col.insert_one(default)
        return default
    return doc


async def update_guild_config(guild_id: int, data: Dict[str, Any]):
    await guilds_col.update_one({"_id": guild_id}, {"$set": data}, upsert=True)


def replace_vars(text: str, member: discord.Member, guild: discord.Guild) -> str:
    if not text:
        return ""
    return (
        text.replace("{user}", member.mention)
        .replace("{username}", str(member))
        .replace("{server}", guild.name)
        .replace("{membercount}", str(guild.member_count))
    )


def system_embed(title: str, description: str = None, **kwargs) -> discord.Embed:
    emb = discord.Embed(title=title, description=description, color=EMBED_COLOR, **kwargs)
    emb.timestamp = datetime.datetime.now(datetime.timezone.utc)
    return emb


async def send_log(guild: discord.Guild, embed: discord.Embed):
    config = await get_guild_config(guild.id)
    channel_id = config.get("logs_channel_id")
    if not channel_id:
        return
    channel = guild.get_channel(channel_id)
    if channel:
        try:
            await channel.send(embed=embed)
        except Exception:
            pass


async def send_sanction_dm(member: discord.Member, action: str, reason: str, duration: str = None):
    embed = system_embed(
        title=f"Has recibido una sanción • {action}",
        description=f"**Motivo:** {reason or 'No especificado'}"
    )
    if duration:
        embed.add_field(name="Duración", value=duration, inline=False)
    embed.set_footer(text="Staff Team • Shattered Icons")
    try:
        await member.send(embed=embed)
    except Exception:
        pass


async def is_staff(member: discord.Member) -> bool:
    if member.guild_permissions.administrator or member.id == member.guild.owner_id:
        return True
    config = await get_guild_config(member.guild.id)
    staff = set(config.get("staff_roles", []) + config.get("admin_roles", []))
    return any(r.id in staff for r in member.roles)


async def is_admin(member: discord.Member) -> bool:
    if member.guild_permissions.administrator or member.id == member.guild.owner_id:
        return True
    config = await get_guild_config(member.guild.id)
    admin = set(config.get("admin_roles", []))
    return any(r.id in admin for r in member.roles)


async def ensure_muted_role(guild: discord.Guild) -> discord.Role:
    role = discord.utils.get(guild.roles, name="Muted")
    if role:
        return role
    role = await guild.create_role(name="Muted", reason="Rol de mute automático - Shattered Icons")
    # Intentamos denegar send_messages en canales de texto existentes (best-effort)
    for channel in guild.text_channels:
        try:
            await channel.set_permissions(role, send_messages=False, add_reactions=False, speak=False)
        except Exception:
            pass
    return role


# ──────────────────────────────────────────────
# Views - Welcome Setup
# ──────────────────────────────────────────────
class WelcomeSetupView(discord.ui.View):
    def __init__(self, author: discord.Member, config: dict):
        super().__init__(timeout=600)
        self.author = author
        self.config = config.get("welcome", {}).copy()
        self.guild_id = author.guild.id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author.id:
            await interaction.response.send_message("Solo quien ejecutó el comando puede usar este panel.", ephemeral=True)
            return False
        return True

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        channel_types=[discord.ChannelType.text],
        placeholder="Selecciona el canal de bienvenida",
        min_values=0,
        max_values=1,
        row=0
    )
    async def channel_select(self, interaction: discord.Interaction, select: discord.ui.ChannelSelect):
        if select.values:
            self.config["channel_id"] = select.values[0].id
        else:
            self.config["channel_id"] = None
        await interaction.response.defer()

    @discord.ui.button(label="Mensaje", style=discord.ButtonStyle.primary, row=1)
    async def message_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = WelcomeTextModal(self, "message", "Mensaje del embed", self.config.get("message", ""))
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Footer", style=discord.ButtonStyle.primary, row=1)
    async def footer_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = WelcomeTextModal(self, "footer", "Footer del embed", self.config.get("footer", ""))
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Imagen URL", style=discord.ButtonStyle.primary, row=1)
    async def image_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = WelcomeTextModal(self, "image_url", "URL de la imagen", self.config.get("image_url") or "")
        await interaction.response.send_modal(modal)

    @discord.ui.button(label="Color HEX", style=discord.ButtonStyle.secondary, row=1)
    async def color_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        modal = WelcomeTextModal(self, "color", "Color HEX (ej: #cef3f1)", f"#{self.config.get('color', SYSTEM_COLOR):06x}")
        await interaction.response.send_modal(modal)

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        channel_types=[discord.ChannelType.text],
        placeholder="Canales recomendados (multi)",
        min_values=0,
        max_values=10,
        row=2
    )
    async def recommended_select(self, interaction: discord.Interaction, select: discord.ui.ChannelSelect):
        self.config["recommended_channels"] = [c.id for c in select.values]
        await interaction.response.defer()

    @discord.ui.button(label="Vista previa", style=discord.ButtonStyle.secondary, row=3)
    async def preview_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        emb = self._build_preview(interaction.user, interaction.guild)
        await interaction.response.send_message(embed=emb, ephemeral=True)

    @discord.ui.button(label="Guardar", style=discord.ButtonStyle.success, row=3)
    async def save_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await update_guild_config(self.guild_id, {"welcome": self.config})
        await interaction.response.send_message("✅ Configuración de bienvenida guardada correctamente.", ephemeral=True)
        self.stop()

    def _build_preview(self, member: discord.Member, guild: discord.Guild) -> discord.Embed:
        msg = replace_vars(self.config.get("message", ""), member, guild)
        footer = replace_vars(self.config.get("footer", ""), member, guild)
        color = self.config.get("color", SYSTEM_COLOR)
        try:
            color = int(str(color).lstrip("#"), 16)
        except Exception:
            color = SYSTEM_COLOR

        emb = discord.Embed(description=msg, color=color)
        emb.set_author(name=str(member), icon_url=member.display_avatar.url)
        if footer:
            emb.set_footer(text=footer)
        if self.config.get("image_url"):
            emb.set_image(url=self.config["image_url"])

        rec = self.config.get("recommended_channels", [])
        if rec:
            channels = [f"<#{cid}>" for cid in rec]
            emb.add_field(name="Canales recomendados", value="\n".join(channels), inline=False)
        return emb


class WelcomeTextModal(discord.ui.Modal):
    def __init__(self, parent_view: WelcomeSetupView, key: str, title: str, default: str):
        super().__init__(title=title[:45])
        self.parent_view = parent_view
        self.key = key
        self.input = discord.ui.TextInput(
            label=title,
            default=default[:4000] if default else "",
            style=discord.TextStyle.paragraph if key == "message" else discord.TextStyle.short,
            required=False,
            max_length=2000 if key == "message" else 256
        )
        self.add_item(self.input)

    async def on_submit(self, interaction: discord.Interaction):
        value = self.input.value.strip()
        if self.key == "color":
            try:
                value = int(value.lstrip("#"), 16)
            except Exception:
                await interaction.response.send_message("Color HEX inválido. Se mantuvo el anterior.", ephemeral=True)
                return
        self.parent_view.config[self.key] = value if value else None
        await interaction.response.send_message(f"✅ `{self.key}` actualizado.", ephemeral=True)


# ──────────────────────────────────────────────
# Views - Bot Setup
# ──────────────────────────────────────────────
class BotSetupView(discord.ui.View):
    def __init__(self, author: discord.Member, config: dict):
        super().__init__(timeout=600)
        self.author = author
        self.config = {
            "logs_channel_id": config.get("logs_channel_id"),
            "staff_roles": config.get("staff_roles", []).copy(),
            "admin_roles": config.get("admin_roles", []).copy()
        }
        self.guild_id = author.guild.id

    async def interaction_check(self, interaction: discord.Interaction) -> bool:
        if interaction.user.id != self.author.id:
            await interaction.response.send_message("Solo quien ejecutó el comando puede usar este panel.", ephemeral=True)
            return False
        return True

    @discord.ui.select(
        cls=discord.ui.ChannelSelect,
        channel_types=[discord.ChannelType.text],
        placeholder="Canal de logs",
        min_values=0,
        max_values=1,
        row=0
    )
    async def logs_select(self, interaction: discord.Interaction, select: discord.ui.ChannelSelect):
        self.config["logs_channel_id"] = select.values[0].id if select.values else None
        await interaction.response.defer()

    @discord.ui.select(
        cls=discord.ui.RoleSelect,
        placeholder="Roles de Staff (multi)",
        min_values=0,
        max_values=10,
        row=1
    )
    async def staff_select(self, interaction: discord.Interaction, select: discord.ui.RoleSelect):
        self.config["staff_roles"] = [r.id for r in select.values]
        await interaction.response.defer()

    @discord.ui.select(
        cls=discord.ui.RoleSelect,
        placeholder="Roles de Admin / Owner (multi)",
        min_values=0,
        max_values=10,
        row=2
    )
    async def admin_select(self, interaction: discord.Interaction, select: discord.ui.RoleSelect):
        self.config["admin_roles"] = [r.id for r in select.values]
        await interaction.response.defer()

    @discord.ui.button(label="Guardar configuración", style=discord.ButtonStyle.success, row=3)
    async def save_btn(self, interaction: discord.Interaction, button: discord.ui.Button):
        await update_guild_config(self.guild_id, self.config)
        await interaction.response.send_message("✅ Configuración del bot guardada correctamente.", ephemeral=True)
        self.stop()


# ──────────────────────────────────────────────
# Slash Commands
# ──────────────────────────────────────────────
@bot.tree.command(name="welcome-setup", description="Configura el sistema de bienvenidas")
@app_commands.guild_only()
async def welcome_setup(interaction: discord.Interaction):
    if not await is_admin(interaction.user):
        return await interaction.response.send_message("No tienes permisos de administrador para usar este comando.", ephemeral=True)

    config = await get_guild_config(interaction.guild.id)
    view = WelcomeSetupView(interaction.user, config)

    emb = system_embed(
        "Panel de Bienvenidas • Shattered Icons",
        "Configura el canal, mensaje, color, footer, imagen y canales recomendados.\n"
        "Variables disponibles: `{user}` `{username}` `{server}` `{membercount}`\n\n"
        "Los cambios se guardan al pulsar **Guardar**."
    )
    await interaction.response.send_message(embed=emb, view=view, ephemeral=True)


@bot.tree.command(name="bot-setup", description="Configura logs, roles de staff y administradores")
@app_commands.guild_only()
async def bot_setup(interaction: discord.Interaction):
    if not await is_admin(interaction.user):
        return await interaction.response.send_message("No tienes permisos de administrador para usar este comando.", ephemeral=True)

    config = await get_guild_config(interaction.guild.id)
    view = BotSetupView(interaction.user, config)

    emb = system_embed(
        "Panel de Configuración del Bot • Shattered Icons",
        "Selecciona el canal de logs y los roles de Staff / Admin.\n"
        "Los cambios se guardan al pulsar **Guardar configuración**."
    )
    await interaction.response.send_message(embed=emb, view=view, ephemeral=True)


# ──────────────────────────────────────────────
# Events
# ──────────────────────────────────────────────
@bot.event
async def on_ready():
    print(f"Logged in as {bot.user} (ID: {bot.user.id})")
    try:
        synced = await bot.tree.sync()
        print(f"Slash commands sincronizados: {len(synced)}")
    except Exception as e:
        print(f"Error syncing commands: {e}")

    if not presence_task.is_running():
        presence_task.start()
    if not temp_actions_checker.is_running():
        temp_actions_checker.start()


@bot.event
async def on_member_join(member: discord.Member):
    if member.bot:
        return
    config = await get_guild_config(member.guild.id)
    welcome = config.get("welcome", {})
    channel_id = welcome.get("channel_id")
    if not channel_id:
        return
    channel = member.guild.get_channel(channel_id)
    if not channel:
        return

    msg = replace_vars(welcome.get("message", ""), member, member.guild)
    footer = replace_vars(welcome.get("footer", ""), member, member.guild)
    color = welcome.get("color", SYSTEM_COLOR)
    try:
        color = int(str(color).lstrip("#"), 16)
    except Exception:
        color = SYSTEM_COLOR

    emb = discord.Embed(description=msg, color=color)
    emb.set_author(name=str(member), icon_url=member.display_avatar.url)
    if footer:
        emb.set_footer(text=footer)
    if welcome.get("image_url"):
        emb.set_image(url=welcome["image_url"])

    rec = welcome.get("recommended_channels", [])
    if rec:
        channels = [f"<#{cid}>" for cid in rec]
        emb.add_field(name="Canales recomendados", value="\n".join(channels), inline=False)

    try:
        await channel.send(content=member.mention, embed=emb)
    except Exception:
        pass


@bot.event
async def on_command_error(ctx: commands.Context, error: commands.CommandError):
    if isinstance(error, commands.CommandNotFound):
        return

    if isinstance(error, (commands.MissingRequiredArgument, commands.BadArgument, commands.BadUnionArgument)):
        usage = getattr(ctx.command, "usage_example", None) or f"?{ctx.command.qualified_name}"
        emb = system_embed(
            "Uso incorrecto del comando",
            f"**Uso correcto:**\n```{usage}```\n"
            f"Revisa los argumentos e inténtalo de nuevo."
        )
        return await ctx.send(embed=emb, delete_after=20)

    if isinstance(error, commands.MissingPermissions) or isinstance(error, commands.BotMissingPermissions):
        emb = system_embed("Permisos insuficientes", str(error))
        return await ctx.send(embed=emb, delete_after=15)

    if isinstance(error, commands.CheckFailure):
        emb = system_embed("Acceso denegado", "No tienes los permisos necesarios para usar este comando.")
        return await ctx.send(embed=emb, delete_after=15)

    # Error genérico
    emb = system_embed("Error", f"```{str(error)[:1000]}```")
    await ctx.send(embed=emb, delete_after=20)
    raise error  # para logs en consola


# ──────────────────────────────────────────────
# Tasks
# ──────────────────────────────────────────────
PRESENCE_MESSAGES = [
    "Shattered Icons",
    "Dev: Supskevv",
]

@tasks.loop(seconds=10)
async def presence_task():
    if not bot.is_ready():
        return
    msg = PRESENCE_MESSAGES[presence_task.current_loop % len(PRESENCE_MESSAGES)]
    try:
        await bot.change_presence(
            activity=discord.Activity(type=discord.ActivityType.watching, name=msg)
        )
    except Exception:
        pass


@tasks.loop(seconds=30)
async def temp_actions_checker():
    now = datetime.datetime.now(datetime.timezone.utc)
    cursor = temp_actions_col.find({"expires_at": {"$lte": now}})
    async for doc in cursor:
        guild = bot.get_guild(doc["guild_id"])
        if not guild:
            await temp_actions_col.delete_one({"_id": doc["_id"]})
            continue

        try:
            if doc["type"] == "tempban":
                user = await bot.fetch_user(doc["user_id"])
                await guild.unban(user, reason="Tempban expirado • Shattered Icons")
            elif doc["type"] == "mute":
                member = guild.get_member(doc["user_id"])
                if member:
                    muted = discord.utils.get(guild.roles, name="Muted")
                    if muted and muted in member.roles:
                        await member.remove_roles(muted, reason="Mute temporal expirado")
        except Exception:
            pass
        finally:
            await temp_actions_col.delete_one({"_id": doc["_id"]})


# ──────────────────────────────────────────────
# Cargar comandos de prefijo
# ──────────────────────────────────────────────
async def setup_hook():
    from commands import ModerationCog
    await bot.add_cog(ModerationCog(bot))

bot.setup_hook = setup_hook

# ──────────────────────────────────────────────
# Run
# ──────────────────────────────────────────────
if __name__ == "__main__":
    bot.run(TOKEN)
