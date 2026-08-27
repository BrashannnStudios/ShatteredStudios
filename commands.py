import datetime
from typing import Optional, Union

import discord
from discord.ext import commands

# Importamos helpers desde main (se inyectan vía bot)
# Evitamos import circular usando atributos del bot


class ModerationCog(commands.Cog):
    def __init__(self, bot: commands.Bot):
        self.bot = bot

    # ──────────────────────────────────────────────
    # Utilidades internas
    # ──────────────────────────────────────────────
    async def _check_staff(self, ctx: commands.Context) -> bool:
        from main import is_staff
        if not await is_staff(ctx.author):
            emb = discord.Embed(
                title="Acceso denegado",
                description="Necesitas ser Staff o Administrador para usar este comando.",
                color=0xcef3f1
            )
            await ctx.send(embed=emb, delete_after=12)
            return False
        return True

    async def _check_admin(self, ctx: commands.Context) -> bool:
        from main import is_admin
        if not await is_admin(ctx.author):
            emb = discord.Embed(
                title="Acceso denegado",
                description="Necesitas permisos de Administrador para usar este comando.",
                color=0xcef3f1
            )
            await ctx.send(embed=emb, delete_after=12)
            return False
        return True

    def _system_embed(self, title: str, description: str = None) -> discord.Embed:
        emb = discord.Embed(title=title, description=description, color=0xcef3f1)
        emb.timestamp = datetime.datetime.now(datetime.timezone.utc)
        return emb

    # ──────────────────────────────────────────────
    # Moderación
    # ──────────────────────────────────────────────
    @commands.command(name="lock")
    @commands.guild_only()
    async def lock(self, ctx: commands.Context, channel: Optional[discord.TextChannel] = None, time: Optional[str] = None):
        """?lock [canal] [tiempo]"""
        if not await self._check_staff(ctx):
            return
        channel = channel or ctx.channel
        from main import parse_duration, send_log, system_embed

        overwrite = channel.overwrites_for(ctx.guild.default_role)
        overwrite.send_messages = False
        await channel.set_permissions(ctx.guild.default_role, overwrite=overwrite, reason=f"Lock por {ctx.author}")

        emb = self._system_embed("Canal bloqueado", f"{channel.mention} ha sido bloqueado.")
        await ctx.send(embed=emb)

        log_emb = system_embed("Lock", f"**Canal:** {channel.mention}\n**Moderador:** {ctx.author.mention}")
        await send_log(ctx.guild, log_emb)

        if time:
            seconds = parse_duration(time)
            if seconds:
                await discord.utils.sleep_until(datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=seconds))
                overwrite.send_messages = None
                await channel.set_permissions(ctx.guild.default_role, overwrite=overwrite, reason="Lock temporal expirado")
                await channel.send(embed=self._system_embed("Canal desbloqueado", f"{channel.mention} ha sido desbloqueado automáticamente."))

    @commands.command(name="unlock")
    @commands.guild_only()
    async def unlock(self, ctx: commands.Context, channel: Optional[discord.TextChannel] = None):
        """?unlock [canal]"""
        if not await self._check_staff(ctx):
            return
        channel = channel or ctx.channel
        from main import send_log, system_embed

        overwrite = channel.overwrites_for(ctx.guild.default_role)
        overwrite.send_messages = None
        await channel.set_permissions(ctx.guild.default_role, overwrite=overwrite, reason=f"Unlock por {ctx.author}")

        emb = self._system_embed("Canal desbloqueado", f"{channel.mention} ha sido desbloqueado.")
        await ctx.send(embed=emb)

        log_emb = system_embed("Unlock", f"**Canal:** {channel.mention}\n**Moderador:** {ctx.author.mention}")
        await send_log(ctx.guild, log_emb)

    @commands.command(name="ban")
    @commands.guild_only()
    async def ban(self, ctx: commands.Context, user: Union[discord.Member, discord.User, int], *, reason: str = "No especificado"):
        """?ban {user/id} {motivo}"""
        if not await self._check_staff(ctx):
            return
        from main import send_log, send_sanction_dm, system_embed

        if isinstance(user, int):
            try:
                user = await self.bot.fetch_user(user)
            except Exception:
                return await ctx.send(embed=self._system_embed("Error", "Usuario no encontrado."))

        member = user if isinstance(user, discord.Member) else None
        if member and member.top_role >= ctx.author.top_role and ctx.author.id != ctx.guild.owner_id:
            return await ctx.send(embed=self._system_embed("Error", "No puedes banear a alguien con rol igual o superior."))

        if member:
            await send_sanction_dm(member, "Ban permanente", reason)

        await ctx.guild.ban(user, reason=f"{reason} • Por Staff Team", delete_message_days=0)
        emb = self._system_embed("Usuario baneado", f"**Usuario:** {user} (`{user.id}`)\n**Motivo:** {reason}")
        await ctx.send(embed=emb)

        log_emb = system_embed("Ban", f"**Usuario:** {user} (`{user.id}`)\n**Motivo:** {reason}\n**Moderador:** {ctx.author.mention}")
        await send_log(ctx.guild, log_emb)

    @commands.command(name="tempban")
    @commands.guild_only()
    async def tempban(self, ctx: commands.Context, user: Union[discord.Member, discord.User, int], time: str, *, reason: str = "No especificado"):
        """?tempban {user/id} {tiempo} {motivo}"""
        if not await self._check_staff(ctx):
            return
        from main import parse_duration, send_log, send_sanction_dm, system_embed, temp_actions_col, format_timedelta

        seconds = parse_duration(time)
        if not seconds:
            return await ctx.send(embed=self._system_embed("Tiempo inválido", "Usa formatos como `30s`, `5m`, `2h`, `1d`, `1w`."))

        if isinstance(user, int):
            try:
                user = await self.bot.fetch_user(user)
            except Exception:
                return await ctx.send(embed=self._system_embed("Error", "Usuario no encontrado."))

        member = user if isinstance(user, discord.Member) else None
        if member and member.top_role >= ctx.author.top_role and ctx.author.id != ctx.guild.owner_id:
            return await ctx.send(embed=self._system_embed("Error", "No puedes banear a alguien con rol igual o superior."))

        duration_str = format_timedelta(seconds)
        if member:
            await send_sanction_dm(member, "Ban temporal", reason, duration_str)

        await ctx.guild.ban(user, reason=f"{reason} • Tempban {duration_str} • Staff Team", delete_message_days=0)

        expires = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=seconds)
        await temp_actions_col.insert_one({
            "type": "tempban",
            "guild_id": ctx.guild.id,
            "user_id": user.id,
            "expires_at": expires,
            "reason": reason,
            "moderator_id": ctx.author.id
        })

        emb = self._system_embed("Usuario baneado temporalmente", f"**Usuario:** {user} (`{user.id}`)\n**Duración:** {duration_str}\n**Motivo:** {reason}")
        await ctx.send(embed=emb)

        log_emb = system_embed("Tempban", f"**Usuario:** {user} (`{user.id}`)\n**Duración:** {duration_str}\n**Motivo:** {reason}\n**Moderador:** {ctx.author.mention}")
        await send_log(ctx.guild, log_emb)

    @commands.command(name="unban")
    @commands.guild_only()
    async def unban(self, ctx: commands.Context, user_id: int):
        """?unban {user-id}"""
        if not await self._check_staff(ctx):
            return
        from main import send_log, system_embed, temp_actions_col

        try:
            user = await self.bot.fetch_user(user_id)
            await ctx.guild.unban(user, reason=f"Unban por Staff Team")
        except Exception:
            return await ctx.send(embed=self._system_embed("Error", "No se pudo desbanear. ¿Está baneado o el ID es incorrecto?"))

        await temp_actions_col.delete_many({"guild_id": ctx.guild.id, "user_id": user_id, "type": "tempban"})

        emb = self._system_embed("Usuario desbaneado", f"**Usuario:** {user} (`{user.id}`)")
        await ctx.send(embed=emb)

        log_emb = system_embed("Unban", f"**Usuario:** {user} (`{user.id}`)\n**Moderador:** {ctx.author.mention}")
        await send_log(ctx.guild, log_emb)

    @commands.command(name="kick")
    @commands.guild_only()
    async def kick(self, ctx: commands.Context, member: discord.Member, *, reason: str = "No especificado"):
        """?kick {user} {motivo}"""
        if not await self._check_staff(ctx):
            return
        from main import send_log, send_sanction_dm, system_embed

        if member.top_role >= ctx.author.top_role and ctx.author.id != ctx.guild.owner_id:
            return await ctx.send(embed=self._system_embed("Error", "No puedes expulsar a alguien con rol igual o superior."))

        await send_sanction_dm(member, "Expulsión (Kick)", reason)
        await member.kick(reason=f"{reason} • Staff Team")

        emb = self._system_embed("Usuario expulsado", f"**Usuario:** {member} (`{member.id}`)\n**Motivo:** {reason}")
        await ctx.send(embed=emb)

        log_emb = system_embed("Kick", f"**Usuario:** {member} (`{member.id}`)\n**Motivo:** {reason}\n**Moderador:** {ctx.author.mention}")
        await send_log(ctx.guild, log_emb)

    @commands.command(name="mute")
    @commands.guild_only()
    async def mute(self, ctx: commands.Context, member: discord.Member, time: Optional[str] = None, *, reason: str = "No especificado"):
        """?mute {user} [tiempo] {motivo}"""
        if not await self._check_staff(ctx):
            return
        from main import parse_duration, send_log, send_sanction_dm, system_embed, ensure_muted_role, temp_actions_col, format_timedelta

        if member.top_role >= ctx.author.top_role and ctx.author.id != ctx.guild.owner_id:
            return await ctx.send(embed=self._system_embed("Error", "No puedes mutear a alguien con rol igual o superior."))

        muted_role = await ensure_muted_role(ctx.guild)
        if muted_role in member.roles:
            return await ctx.send(embed=self._system_embed("Error", "El usuario ya está muteado."))

        await member.add_roles(muted_role, reason=f"{reason} • Staff Team")

        duration_str = None
        if time:
            seconds = parse_duration(time)
            if seconds:
                duration_str = format_timedelta(seconds)
                expires = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=seconds)
                await temp_actions_col.insert_one({
                    "type": "mute",
                    "guild_id": ctx.guild.id,
                    "user_id": member.id,
                    "expires_at": expires,
                    "reason": reason,
                    "moderator_id": ctx.author.id
                })

        await send_sanction_dm(member, "Mute", reason, duration_str)

        desc = f"**Usuario:** {member.mention}\n**Motivo:** {reason}"
        if duration_str:
            desc += f"\n**Duración:** {duration_str}"
        emb = self._system_embed("Usuario muteado", desc)
        await ctx.send(embed=emb)

        log_emb = system_embed("Mute", f"{desc}\n**Moderador:** {ctx.author.mention}")
        await send_log(ctx.guild, log_emb)

    @commands.command(name="unmute")
    @commands.guild_only()
    async def unmute(self, ctx: commands.Context, member: discord.Member):
        """?unmute {user}"""
        if not await self._check_staff(ctx):
            return
        from main import send_log, system_embed, temp_actions_col

        muted = discord.utils.get(ctx.guild.roles, name="Muted")
        if not muted or muted not in member.roles:
            return await ctx.send(embed=self._system_embed("Error", "El usuario no está muteado."))

        await member.remove_roles(muted, reason=f"Unmute por Staff Team")
        await temp_actions_col.delete_many({"guild_id": ctx.guild.id, "user_id": member.id, "type": "mute"})

        emb = self._system_embed("Usuario desmuteado", f"**Usuario:** {member.mention}")
        await ctx.send(embed=emb)

        log_emb = system_embed("Unmute", f"**Usuario:** {member.mention}\n**Moderador:** {ctx.author.mention}")
        await send_log(ctx.guild, log_emb)

    @commands.command(name="timeout")
    @commands.guild_only()
    async def timeout(self, ctx: commands.Context, member: discord.Member, time: str, *, reason: str = "No especificado"):
        """?timeout {user} {tiempo} {motivo}"""
        if not await self._check_staff(ctx):
            return
        from main import parse_duration, send_log, send_sanction_dm, system_embed, format_timedelta

        seconds = parse_duration(time)
        if not seconds or seconds > 28 * 86400:
            return await ctx.send(embed=self._system_embed("Tiempo inválido", "Máximo 28 días. Formatos: `30s` `5m` `2h` `1d`."))

        if member.top_role >= ctx.author.top_role and ctx.author.id != ctx.guild.owner_id:
            return await ctx.send(embed=self._system_embed("Error", "No puedes aplicar timeout a alguien con rol igual o superior."))

        until = datetime.datetime.now(datetime.timezone.utc) + datetime.timedelta(seconds=seconds)
        await member.timeout(until, reason=f"{reason} • Staff Team")

        duration_str = format_timedelta(seconds)
        await send_sanction_dm(member, "Timeout", reason, duration_str)

        emb = self._system_embed("Timeout aplicado", f"**Usuario:** {member.mention}\n**Duración:** {duration_str}\n**Motivo:** {reason}")
        await ctx.send(embed=emb)

        log_emb = system_embed("Timeout", f"**Usuario:** {member.mention}\n**Duración:** {duration_str}\n**Motivo:** {reason}\n**Moderador:** {ctx.author.mention}")
        await send_log(ctx.guild, log_emb)

    @commands.command(name="warn")
    @commands.guild_only()
    async def warn(self, ctx: commands.Context, member: discord.Member, *, reason: str = "No especificado"):
        """?warn {user} {motivo}"""
        if not await self._check_staff(ctx):
            return
        from main import warnings_col, send_log, send_sanction_dm, system_embed

        # Generamos ID incremental simple por usuario
        count = await warnings_col.count_documents({"guild_id": ctx.guild.id, "user_id": member.id})
        warn_id = count + 1

        await warnings_col.insert_one({
            "guild_id": ctx.guild.id,
            "user_id": member.id,
            "warn_id": warn_id,
            "reason": reason,
            "moderator_id": ctx.author.id,
            "timestamp": datetime.datetime.now(datetime.timezone.utc)
        })

        await send_sanction_dm(member, "Advertencia (Warn)", reason)

        emb = self._system_embed("Advertencia aplicada", f"**Usuario:** {member.mention}\n**ID Warn:** `{warn_id}`\n**Motivo:** {reason}")
        await ctx.send(embed=emb)

        log_emb = system_embed("Warn", f"**Usuario:** {member.mention}\n**ID:** `{warn_id}`\n**Motivo:** {reason}\n**Moderador:** {ctx.author.mention}")
        await send_log(ctx.guild, log_emb)

    @commands.command(name="warnings")
    @commands.guild_only()
    async def warnings(self, ctx: commands.Context, member: discord.Member):
        """?warnings {user}"""
        if not await self._check_staff(ctx):
            return
        from main import warnings_col

        cursor = warnings_col.find({"guild_id": ctx.guild.id, "user_id": member.id}).sort("warn_id", 1)
        warns = [doc async for doc in cursor]

        if not warns:
            return await ctx.send(embed=self._system_embed("Warnings", f"{member.mention} no tiene advertencias."))

        lines = []
        for w in warns:
            ts = w["timestamp"].strftime("%d/%m/%Y %H:%M")
            lines.append(f"**#{w['warn_id']}** — {w['reason']}\n└ {ts}")

        emb = self._system_embed(f"Warnings de {member}", "\n\n".join(lines))
        emb.set_footer(text=f"Total: {len(warns)}")
        await ctx.send(embed=emb)

    @commands.command(name="delwarn")
    @commands.guild_only()
    async def delwarn(self, ctx: commands.Context, member: discord.Member, warn_id: int):
        """?delwarn {user} {warn-id}"""
        if not await self._check_staff(ctx):
            return
        from main import warnings_col, send_log, system_embed

        result = await warnings_col.delete_one({"guild_id": ctx.guild.id, "user_id": member.id, "warn_id": warn_id})
        if result.deleted_count == 0:
            return await ctx.send(embed=self._system_embed("Error", "No se encontró esa advertencia."))

        emb = self._system_embed("Advertencia eliminada", f"**Usuario:** {member.mention}\n**ID Warn:** `{warn_id}`")
        await ctx.send(embed=emb)

        log_emb = system_embed("Delwarn", f"**Usuario:** {member.mention}\n**ID:** `{warn_id}`\n**Moderador:** {ctx.author.mention}")
        await send_log(ctx.guild, log_emb)

    @commands.command(name="editreason")
    @commands.guild_only()
    async def editreason(self, ctx: commands.Context, member: discord.Member, warn_id: int, *, new_reason: str):
        """?editreason {user} {warn-id} {nueva razón}"""
        if not await self._check_staff(ctx):
            return
        from main import warnings_col, send_log, system_embed

        result = await warnings_col.update_one(
            {"guild_id": ctx.guild.id, "user_id": member.id, "warn_id": warn_id},
            {"$set": {"reason": new_reason}}
        )
        if result.modified_count == 0:
            return await ctx.send(embed=self._system_embed("Error", "No se encontró esa advertencia."))

        emb = self._system_embed("Razón actualizada", f"**Usuario:** {member.mention}\n**ID Warn:** `{warn_id}`\n**Nueva razón:** {new_reason}")
        await ctx.send(embed=emb)

        log_emb = system_embed("Editreason", f"**Usuario:** {member.mention}\n**ID:** `{warn_id}`\n**Nueva razón:** {new_reason}\n**Moderador:** {ctx.author.mention}")
        await send_log(ctx.guild, log_emb)

    @commands.command(name="note")
    @commands.guild_only()
    async def note(self, ctx: commands.Context, member: discord.Member, *, note: str):
        """?note {user} {nota}"""
        if not await self._check_staff(ctx):
            return
        from main import notes_col, send_log, system_embed

        count = await notes_col.count_documents({"guild_id": ctx.guild.id, "user_id": member.id})
        note_id = count + 1

        await notes_col.insert_one({
            "guild_id": ctx.guild.id,
            "user_id": member.id,
            "note_id": note_id,
            "content": note,
            "moderator_id": ctx.author.id,
            "timestamp": datetime.datetime.now(datetime.timezone.utc)
        })

        emb = self._system_embed("Nota añadida", f"**Usuario:** {member.mention}\n**ID Nota:** `{note_id}`\n**Nota:** {note}")
        await ctx.send(embed=emb)

        log_emb = system_embed("Note", f"**Usuario:** {member.mention}\n**ID:** `{note_id}`\n**Nota:** {note}\n**Moderador:** {ctx.author.mention}")
        await send_log(ctx.guild, log_emb)

    @commands.command(name="viewnotes")
    @commands.guild_only()
    async def viewnotes(self, ctx: commands.Context, member: discord.Member):
        """?viewnotes {user}"""
        if not await self._check_staff(ctx):
            return
        from main import notes_col

        cursor = notes_col.find({"guild_id": ctx.guild.id, "user_id": member.id}).sort("note_id", 1)
        notes = [doc async for doc in cursor]

        if not notes:
            return await ctx.send(embed=self._system_embed("Notas", f"{member.mention} no tiene notas."))

        lines = []
        for n in notes:
            ts = n["timestamp"].strftime("%d/%m/%Y %H:%M")
            lines.append(f"**#{n['note_id']}** — {n['content']}\n└ {ts}")

        emb = self._system_embed(f"Notas de {member}", "\n\n".join(lines))
        emb.set_footer(text=f"Total: {len(notes)}")
        await ctx.send(embed=emb)

    @commands.command(name="delnote")
    @commands.guild_only()
    async def delnote(self, ctx: commands.Context, member: discord.Member, note_id: int):
        """?delnote {user} {note-id}"""
        if not await self._check_staff(ctx):
            return
        from main import notes_col, send_log, system_embed

        result = await notes_col.delete_one({"guild_id": ctx.guild.id, "user_id": member.id, "note_id": note_id})
        if result.deleted_count == 0:
            return await ctx.send(embed=self._system_embed("Error", "No se encontró esa nota."))

        emb = self._system_embed("Nota eliminada", f"**Usuario:** {member.mention}\n**ID Nota:** `{note_id}`")
        await ctx.send(embed=emb)

        log_emb = system_embed("Delnote", f"**Usuario:** {member.mention}\n**ID:** `{note_id}`\n**Moderador:** {ctx.author.mention}")
        await send_log(ctx.guild, log_emb)

    @commands.command(name="slowmode")
    @commands.guild_only()
    async def slowmode(self, ctx: commands.Context, channel: Optional[discord.TextChannel] = None, time: str = "0s"):
        """?slowmode [canal] {tiempo}"""
        if not await self._check_staff(ctx):
            return
        from main import parse_duration, send_log, system_embed

        channel = channel or ctx.channel
        seconds = parse_duration(time) if time != "0" else 0
        if seconds is None:
            return await ctx.send(embed=self._system_embed("Tiempo inválido", "Usa `30s`, `5m`, etc. o `0s` para desactivar."))

        if seconds > 21600:
            return await ctx.send(embed=self._system_embed("Error", "El máximo de slowmode es 6 horas (21600s)."))

        await channel.edit(slowmode_delay=seconds)
        if seconds == 0:
            msg = f"Slowmode desactivado en {channel.mention}."
        else:
            msg = f"Slowmode de **{time}** activado en {channel.mention}."

        emb = self._system_embed("Slowmode", msg)
        await ctx.send(embed=emb)

        log_emb = system_embed("Slowmode", f"**Canal:** {channel.mention}\n**Delay:** {time}\n**Moderador:** {ctx.author.mention}")
        await send_log(ctx.guild, log_emb)

    @commands.command(name="clear")
    @commands.guild_only()
    async def clear(self, ctx: commands.Context, amount: int):
        """?clear {cantidad}"""
        if not await self._check_staff(ctx):
            return
        from main import send_log, system_embed

        if amount < 1 or amount > 100:
            return await ctx.send(embed=self._system_embed("Error", "La cantidad debe estar entre 1 y 100."))

        deleted = await ctx.channel.purge(limit=amount + 1)  # +1 por el comando
        emb = self._system_embed("Mensajes eliminados", f"Se eliminaron **{len(deleted)-1}** mensajes.")
        await ctx.send(embed=emb, delete_after=8)

        log_emb = system_embed("Clear", f"**Canal:** {ctx.channel.mention}\n**Cantidad:** {len(deleted)-1}\n**Moderador:** {ctx.author.mention}")
        await send_log(ctx.guild, log_emb)

    # ──────────────────────────────────────────────
    # Utilidad
    # ──────────────────────────────────────────────
    @commands.command(name="dm")
    @commands.guild_only()
    async def dm(self, ctx: commands.Context, member: discord.Member, *, message: str):
        """?dm {user} {mensaje}"""
        if not await self._check_staff(ctx):
            return
        from main import send_log, system_embed

        emb = self._system_embed("Mensaje del Staff Team", message)
        emb.set_footer(text="Shattered Icons")
        try:
            await member.send(embed=emb)
            await ctx.send(embed=self._system_embed("DM enviado", f"Mensaje enviado a {member.mention}."))
        except Exception:
            await ctx.send(embed=self._system_embed("Error", "No se pudo enviar el DM (el usuario tiene los DMs cerrados)."))

        log_emb = system_embed("DM", f"**Usuario:** {member.mention}\n**Mensaje:** {message[:200]}\n**Moderador:** {ctx.author.mention}")
        await send_log(ctx.guild, log_emb)

    @commands.command(name="addrole")
    @commands.guild_only()
    async def addrole(self, ctx: commands.Context, member: discord.Member, role: discord.Role):
        """?addrole {user} {rol}"""
        if not await self._check_staff(ctx):
            return
        from main import send_log, system_embed

        if role >= ctx.author.top_role and ctx.author.id != ctx.guild.owner_id:
            return await ctx.send(embed=self._system_embed("Error", "No puedes asignar un rol igual o superior al tuyo."))

        await member.add_roles(role, reason=f"Addrole por Staff Team")
        emb = self._system_embed("Rol añadido", f"**Usuario:** {member.mention}\n**Rol:** {role.mention}")
        await ctx.send(embed=emb)

        log_emb = system_embed("Addrole", f"**Usuario:** {member.mention}\n**Rol:** {role.mention}\n**Moderador:** {ctx.author.mention}")
        await send_log(ctx.guild, log_emb)

    @commands.command(name="removerole")
    @commands.guild_only()
    async def removerole(self, ctx: commands.Context, member: discord.Member, role: discord.Role):
        """?removerole {user} {rol}"""
        if not await self._check_staff(ctx):
            return
        from main import send_log, system_embed

        await member.remove_roles(role, reason=f"Removerole por Staff Team")
        emb = self._system_embed("Rol removido", f"**Usuario:** {member.mention}\n**Rol:** {role.mention}")
        await ctx.send(embed=emb)

        log_emb = system_embed("Removerole", f"**Usuario:** {member.mention}\n**Rol:** {role.mention}\n**Moderador:** {ctx.author.mention}")
        await send_log(ctx.guild, log_emb)

    @commands.command(name="nick")
    @commands.guild_only()
    async def nick(self, ctx: commands.Context, member: discord.Member, *, new_nick: str = None):
        """?nick {user} {nuevo nick}"""
        if not await self._check_staff(ctx):
            return
        from main import send_log, system_embed

        old = member.display_name
        try:
            await member.edit(nick=new_nick)
        except Exception:
            return await ctx.send(embed=self._system_embed("Error", "No pude cambiar el nick (jerarquía o permisos)."))

        emb = self._system_embed("Nick actualizado", f"**Usuario:** {member.mention}\n**Antes:** {old}\n**Ahora:** {new_nick or member.name}")
        await ctx.send(embed=emb)

        log_emb = system_embed("Nick", f"**Usuario:** {member.mention}\n**Nuevo nick:** {new_nick or 'reseteado'}\n**Moderador:** {ctx.author.mention}")
        await send_log(ctx.guild, log_emb)

    @commands.command(name="userinfo")
    @commands.guild_only()
    async def userinfo(self, ctx: commands.Context, member: Optional[discord.Member] = None):
        """?userinfo [user]"""
        member = member or ctx.author
        from main import warnings_col, notes_col

        warn_count = await warnings_col.count_documents({"guild_id": ctx.guild.id, "user_id": member.id})
        note_count = await notes_col.count_documents({"guild_id": ctx.guild.id, "user_id": member.id})

        roles = [r.mention for r in member.roles if r != ctx.guild.default_role][::-1][:15]
        roles_str = " ".join(roles) if roles else "Ninguno"

        emb = self._system_embed(f"Información de {member}")
        emb.set_thumbnail(url=member.display_avatar.url)
        emb.add_field(name="ID", value=f"`{member.id}`", inline=True)
        emb.add_field(name="Nick", value=member.display_name, inline=True)
        emb.add_field(name="Cuenta creada", value=discord.utils.format_dt(member.created_at, "R"), inline=True)
        emb.add_field(name="Se unió", value=discord.utils.format_dt(member.joined_at, "R") if member.joined_at else "Desconocido", inline=True)
        emb.add_field(name="Warnings", value=str(warn_count), inline=True)
        emb.add_field(name="Notas", value=str(note_count), inline=True)
        emb.add_field(name="Roles", value=roles_str, inline=False)
        emb.set_footer(text=f"Solicitado por {ctx.author}")
        await ctx.send(embed=emb)

    @commands.command(name="cmds")
    @commands.guild_only()
    async def cmds(self, ctx: commands.Context):
        """?cmds"""
        emb = self._system_embed(
            "Comandos de Shattered Icons",
            "**Moderación**\n"
            "`?lock` `?unlock` `?ban` `?tempban` `?unban` `?kick`\n"
            "`?mute` `?unmute` `?timeout` `?warn` `?warnings` `?delwarn` `?editreason`\n"
            "`?note` `?viewnotes` `?delnote` `?slowmode` `?clear`\n\n"
            "**Utilidad**\n"
            "`?dm` `?addrole` `?removerole` `?nick` `?userinfo` `?cmds`\n\n"
            "**Configuración (Slash)**\n"
            "`/welcome-setup`  `/bot-setup`\n\n"
            "Formatos de tiempo: `30s` `5m` `2h` `1d` `1w`"
        )
        emb.set_footer(text="Prefijo: ?  •  Case-insensitive")
        await ctx.send(embed=emb)


async def setup(bot: commands.Bot):
    await bot.add_cog(ModerationCog(bot))
