import discord
from discord.ext import commands, tasks
from discord.ui import View, Button
import json, os, time, threading
from datetime import datetime
from http.server import BaseHTTPRequestHandler, HTTPServer
# ---------------- KEEP ALIVE (Render web service) ----------------
def run_server():
    class Handler(BaseHTTPRequestHandler):
        def do_GET(self):
            self.send_response(200)
            self.end_headers()
            self.wfile.write(b"Bot running")
        def log_message(self, *args, **kwargs):
            pass  # silence per-request logs
    port = int(os.getenv("PORT", "10000"))
    server = HTTPServer(("0.0.0.0", port), Handler)
    server.serve_forever()
threading.Thread(target=run_server, daemon=True).start()
# ---------------- TOKEN ----------------
TOKEN = os.getenv("TOKEN")
if not TOKEN:
    raise SystemExit("TOKEN environment variable is not set.")
# ---------------- CONFIG ----------------
TICKET_CATEGORY_IDS = [
1529125690388119622,
1529668023982751794
]
ADMIN_IDS = {
1303496457319350314,
823552875766349825,
1467325360113717394,
743203080773828670,
404758345707290655
}
CLOCK_PANEL_CHANNEL_ID = 1536497611505270845
CLOCK_ROLE_ID = 1536497686746894456
STAFF_ROLE_ID = 1536497686746894456   # paste your @Staff role ID here so !badducklings can catch people who never clocked in
DATA_FILE = "staff_data.json"
MIN_MESSAGE_LENGTH = 2
MESSAGES_PER_TICKET = 5   # counted messages needed in ONE channel to earn a ticket
BP_PER_TICKET = 1         # BP awarded when a ticket is earned
BP_TIER_SIZE = 5          # every this many brownies, earning gets harder
BP_TIER_DECAY = 0.87      # earn rate multiplies by this per tier (lower = harsher curve)
MAX_SESSION_HOURS = 12
AUTO_CLOCKOUT_MINUTES = 30   # no ticket messages for this long while clocked in = auto clockout
# ---------------- BOT ----------------
intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)
# ---------------- ACCESS CONTROL ----------------
class NotAdmin(commands.CheckFailure):
    pass
class NotClockedIn(commands.CheckFailure):
    pass
ADMIN_COMMAND_NAMES = {
    "brownie",
    "resetuser",
    "resetallbp",
    "resetticket",
    "resetalltickets",
    "forceclockin",
    "forceclockout",
    "forceclockoutall",
    "badducklings",
    "adminhelp",
}
ALWAYS_ALLOWED_COMMANDS = {"help"}
@bot.check
async def gatekeeper(ctx):
    if ctx.command is None:
        return True
    name = ctx.command.name
    if name in ALWAYS_ALLOWED_COMMANDS:
        return True
    if name in ADMIN_COMMAND_NAMES:
        if ctx.author.id not in ADMIN_IDS:
            raise NotAdmin()
        return True
    if ctx.author.id in ADMIN_IDS:
        return True
    user = get_user(ctx.author.id)
    if not user["clocked_in"]:
        raise NotClockedIn()
    return True
# ---------------- DATA ----------------
if os.path.exists(DATA_FILE):
    with open(DATA_FILE,"r") as f:
        data=json.load(f)
else:
    data={}
def save_data():
    tmp = DATA_FILE + ".tmp"
    with open(tmp, "w") as f:
        json.dump(data, f, indent=4)
    os.replace(tmp, DATA_FILE)
ROMANTIC_ACTIONS = ("slap", "hug", "kiss", "cuddle", "poke", "tickle")
def _default_user():
    return {
        "bp_week": 0,
        "tickets_week": 0,
        "messages_week": 0,
        "hours_week": 0,
        "clocked_in": False,
        "clock_time": None,
        "last_messages": [],
        "last_credit_time": 0,
        "ticket_logs": [],
        "active_days": [],
        "channel_progress": {},    # channel_id -> counted messages toward a ticket
        "credited_channels": [],   # channels this user already earned a ticket from
        "last_activity": 0,        # last time they sent a message in a ticket channel
        "romantic_counters": {a: {} for a in ROMANTIC_ACTIONS},
    }
def get_user(uid):
    uid = str(uid)
    if uid not in data:
        data[uid] = _default_user()
        return data[uid]
    user = data[uid]
    # Migrate legacy keys from older versions of the bot.
    if "romantic_counters" not in user:
        if "romantic_counts" in user:
            user["romantic_counters"] = user.pop("romantic_counts")
        elif "#romantic_counters" in user:
            user["romantic_counters"] = user.pop("#romantic_counters")
    user.pop("#romantic_counters", None)
    user.pop("romantic_counts", None)
    for legacy in ("bp_lastweek", "tickets_lastweek", "messages_lastweek", "hours_lastweek"):
        user.pop(legacy, None)
    # Fill in any fields that newer versions added.
    for key, value in _default_user().items():
        if key not in user:
            user[key] = value
    # Migrate romantic counters from old global-int shape to per-target dict shape.
    for action in ROMANTIC_ACTIONS:
        val = user["romantic_counters"].get(action)
        if not isinstance(val, dict):
            user["romantic_counters"][action] = {}
    return user
# ---------------- RANK ----------------
def get_rank(bp):
    if bp >= 90:
        return "weirdo why you grinding so much"
    elif bp >= 60:
        return "Head Honcho"
    elif bp >= 35:
        return "Senior Staffer"
    elif bp >= 15:
        return "Desk Jockey"
    elif bp >= 5:
        return "Rising Star"
    else:
        return "Coffee Fetcher"
# ---------------- BP EARNING CURVE ----------------
def bp_multiplier(bp):
    """Earning slows down every BP_TIER_SIZE brownies you already have."""
    tier = int(max(bp, 0) // BP_TIER_SIZE)
    return BP_TIER_DECAY ** tier
def award_bp(user, amount):
    """All earned BP goes through the difficulty curve. Admin !brownie bypasses this on purpose."""
    user["bp_week"] += amount * bp_multiplier(user.get("bp_week", 0))
# ---------------- CLOCK PANEL ----------------
CLOCK_PANEL_MESSAGE_ID=None
class ClockPanel(View):
    def __init__(self):
        super().__init__(timeout=None)
    @discord.ui.button(label="Clock In",style=discord.ButtonStyle.green,custom_id="workbot:clock_in")
    async def clockin(self,interaction:discord.Interaction,button:Button):
        user=get_user(interaction.user.id)
        if user["clocked_in"]:
            return await interaction.response.send_message("Already clocked in.",ephemeral=True)
        user["clocked_in"]=True
        user["clock_time"]=time.time()
        user["last_activity"]=time.time()
        role=interaction.guild.get_role(CLOCK_ROLE_ID)
        if role:
            await interaction.user.add_roles(role)
        save_data()
        await interaction.response.send_message("Clocked in!",ephemeral=True)
        await update_clock_panel()
    @discord.ui.button(label="Clock Out",style=discord.ButtonStyle.red,custom_id="workbot:clock_out")
    async def clockout(self,interaction:discord.Interaction,button:Button):
        user=get_user(interaction.user.id)
        if not user["clocked_in"]:
            return await interaction.response.send_message("You are not clocked in.",ephemeral=True)
        elapsed=min(time.time()-user["clock_time"], MAX_SESSION_HOURS*3600)
        user["hours_week"]+=round(elapsed/3600,2)
        user["clocked_in"]=False
        user["clock_time"]=None
        role=interaction.guild.get_role(CLOCK_ROLE_ID)
        if role:
            await interaction.user.remove_roles(role)
        save_data()
        await interaction.response.send_message("Clocked out!",ephemeral=True)
        await update_clock_panel()
# ---------------- PANEL UPDATE ----------------
async def update_clock_panel():
    global CLOCK_PANEL_MESSAGE_ID
    channel=bot.get_channel(CLOCK_PANEL_CHANNEL_ID)
    if not channel:
        return
    embed=discord.Embed(
    title="Clock In Panel",
    description="Use the buttons below to clock in or out.",
    color=discord.Color.green()
    )
    clocked=[]
    for uid,u in data.items():
        if u["clocked_in"]:
            member=channel.guild.get_member(int(uid))
            if member:
                clocked.append(member.display_name)
    embed.add_field(
    name="Currently Clocked In",
    value="\n".join(clocked) if clocked else "Nobody clocked in.",
    inline=False
    )
    # First run after a restart: look for an existing panel in the channel and reuse it.
    if not CLOCK_PANEL_MESSAGE_ID:
        try:
            async for old in channel.history(limit=50):
                if (
                    old.author.id == bot.user.id
                    and old.embeds
                    and old.embeds[0].title == "Clock In Panel"
                ):
                    CLOCK_PANEL_MESSAGE_ID = old.id
                    break
        except discord.HTTPException:
            pass
    if CLOCK_PANEL_MESSAGE_ID:
        try:
            msg = await channel.fetch_message(CLOCK_PANEL_MESSAGE_ID)
            await msg.edit(embed=embed, view=ClockPanel())
            return
        except (discord.NotFound, discord.Forbidden, discord.HTTPException) as e:
            print(f"Clock panel edit failed, recreating: {e}")
            CLOCK_PANEL_MESSAGE_ID = None
    msg = await channel.send(embed=embed, view=ClockPanel())
    CLOCK_PANEL_MESSAGE_ID = msg.id
# ---------------- AUTO PANEL UPDATE ----------------
@tasks.loop(minutes=1)
async def auto_update_panel():
    await update_clock_panel()
# ---------------- IDLE AUTO CLOCKOUT ----------------
@tasks.loop(minutes=1)
async def auto_clockout_idle():
    channel = bot.get_channel(CLOCK_PANEL_CHANNEL_ID)
    if not channel:
        return
    guild = channel.guild
    role = guild.get_role(CLOCK_ROLE_ID)
    now = time.time()
    changed = False
    for uid, u in data.items():
        if not u.get("clocked_in"):
            continue
        # idle timer counts from clock-in or their last ticket message, whichever is newer
        last = max(u.get("last_activity", 0) or 0, u.get("clock_time") or 0)
        if now - last < AUTO_CLOCKOUT_MINUTES * 60:
            continue
        if u.get("clock_time"):
            elapsed = min(now - u["clock_time"], MAX_SESSION_HOURS * 3600)
            u["hours_week"] = u.get("hours_week", 0) + round(elapsed / 3600, 2)
        u["clocked_in"] = False
        u["clock_time"] = None
        changed = True
        member = guild.get_member(int(uid))
        if member:
            if role:
                try:
                    await member.remove_roles(role)
                except discord.HTTPException:
                    pass
            try:
                await member.send(
                    f"You were auto clocked out after "
                    f"{AUTO_CLOCKOUT_MINUTES} minutes with no ticket messages."
                )
            except discord.HTTPException:
                pass  # their DMs are closed — they still get clocked out, just no notice
    if changed:
        save_data()
        await update_clock_panel()
# ---------------- MESSAGE TRACKING ----------------
@bot.event
async def on_message(message):
    if message.author.bot:
        return
    await bot.process_commands(message)
    if not message.guild:
        return
    if not message.channel.category:
        return
    if message.channel.category.id not in TICKET_CATEGORY_IDS:
        return
    user=get_user(message.author.id)
    if not user["clocked_in"]:
        return
    user["last_activity"] = time.time()
    content=message.content.strip()
    if len(content)<MIN_MESSAGE_LENGTH:
        return
    if content in user["last_messages"]:
        return
    now=time.time()
    if now-user["last_credit_time"]<10:
        return
    user["last_credit_time"]=now
    user["messages_week"]+=1
    award_bp(user, 0.015)
    user["last_messages"].append(content)
    if len(user["last_messages"])>10:
        user["last_messages"].pop(0)
    user["ticket_logs"].append(content)
    if len(user["ticket_logs"])>10:
        user["ticket_logs"].pop(0)
    # Ticket credit: MESSAGES_PER_TICKET counted messages in ONE channel = 1 ticket.
    # Each channel can only ever pay out once per person.
    ch_key = str(message.channel.id)
    if ch_key not in user["credited_channels"]:
        progress = user["channel_progress"]
        progress[ch_key] = progress.get(ch_key, 0) + 1
        if progress[ch_key] >= MESSAGES_PER_TICKET:
            user["tickets_week"] += 1
            award_bp(user, BP_PER_TICKET)
            user["credited_channels"].append(ch_key)
            del progress[ch_key]
    save_data()
# ---------------- HELP ----------------
bot.remove_command("help")
@bot.command(name="help")
async def help_command(ctx):
    embed = discord.Embed(
        title="Commands",
        description="Stuff you can do while clocked in.",
        color=discord.Color.green()
    )
    embed.add_field(
        name="Clocking in / out",
        value="Use the **Clock In** / **Clock Out** buttons on the panel.",
        inline=False
    )
    embed.add_field(
        name="Your stats",
        value=(
            "`!clockstat [@user]` — full stat sheet\n"
            "`!myrank` — your BP rank\n"
            "`!myhours` — hours worked\n"
            "`!mytickets` — tickets credited\n"
            "`!mymessages` — messages counted"
        ),
        inline=False
    )
    embed.add_field(
        name="Server",
        value=(
            "`!clockedin` — who's currently on the clock\n"
            "`!bpleaderboard` — top BP\n"
            "`!ticketleaderboard` — top tickets\n"
            "`!longesthours` — top hours"
        ),
        inline=False
    )
    embed.add_field(
        name="Fun",
        value="`!slap` `!hug` `!kiss` `!cuddle` `!poke` `!tickle` `@user`",
        inline=False
    )
    await ctx.send(embed=embed)
@bot.command(name="adminhelp")
async def adminhelp_command(ctx):
    embed = discord.Embed(
        title="Admin Commands",
        color=discord.Color.purple()
    )
    embed.add_field(
        name="Brownies",
        value="`!brownie @user <amount>` — add or remove BP",
        inline=False
    )
    embed.add_field(
        name="Reset",
        value=(
            "`!resetuser @user` — wipe all stats for one user\n"
            "`!resetticket @user` — reset their ticket/message count\n"
            "`!resetallbp` — reset BP for everyone\n"
            "`!resetalltickets` — reset tickets/messages for everyone"
        ),
        inline=False
    )
    embed.add_field(
        name="Force clock",
        value=(
            "`!forceclockin @user`\n"
            "`!forceclockout @user`\n"
            "`!forceclockoutall`"
        ),
        inline=False
    )
    embed.add_field(
        name="Reports",
        value="`!badducklings` — least work this week + who never clocked in",
        inline=False
    )
    await ctx.send(embed=embed)
# ---------------- STAFF COMMANDS ----------------
@bot.command()
async def clockstat(ctx,member:discord.Member=None):
    member=member or ctx.author
    user=get_user(member.id)
    logs="\n".join(user["ticket_logs"][-10:]) or "No logs yet."
    embed=discord.Embed(title=f"{member.display_name} Clock Stats")
    embed.add_field(
    name="Stats",
    value=f"BP: {round(user['bp_week'],2)}\nTickets: {user['tickets_week']}\nMessages: {user['messages_week']}\nHours: {round(user['hours_week'],2)}",
    inline=False
    )
    embed.add_field(name="Rank",value=get_rank(user["bp_week"]))
    embed.add_field(name="Last 10 Ticket Logs",value=logs,inline=False)
    await ctx.send(embed=embed)
@bot.command()
async def clockedin(ctx):
    desc=""
    for uid,u in data.items():
        if u["clocked_in"]:
            member=ctx.guild.get_member(int(uid))
            if member:
                desc+=f"{member.display_name}\n"
    embed=discord.Embed(title="Clocked In Staff",description=desc or "Nobody clocked in.")
    await ctx.send(embed=embed)
@bot.command()
async def mytickets(ctx):
    user=get_user(ctx.author.id)
    await ctx.send(f"You have completed {user['tickets_week']} tickets this week.")
@bot.command()
async def mymessages(ctx):
    user=get_user(ctx.author.id)
    await ctx.send(f"You have sent {user['messages_week']} counted messages.")
@bot.command()
async def myhours(ctx):
    user=get_user(ctx.author.id)
    await ctx.send(f"You have worked {round(user['hours_week'],2)} hours this week.")
@bot.command()
async def myrank(ctx):
    user=get_user(ctx.author.id)
    await ctx.send(f"Your current BP rank is {get_rank(user['bp_week'])}.")
# ---------------- LEADERBOARDS ----------------
@bot.command()
async def bpleaderboard(ctx):
    sorted_users=sorted(data.items(),key=lambda x:x[1]["bp_week"],reverse=True)[:10]
    desc=""
    for i,(uid,user) in enumerate(sorted_users,1):
        member=ctx.guild.get_member(int(uid))
        if member:
            desc+=f"{i}. {member.display_name} - {round(user['bp_week'],2)} BP\n"
    await ctx.send(embed=discord.Embed(title="Top BP This Week",description=desc or "No data."))
@bot.command()
async def ticketleaderboard(ctx):
    sorted_users=sorted(data.items(),key=lambda x:x[1]["tickets_week"],reverse=True)[:10]
    desc=""
    for i,(uid,user) in enumerate(sorted_users,1):
        member=ctx.guild.get_member(int(uid))
        if member:
            desc+=f"{i}. {member.display_name} - {user['tickets_week']} Tickets\n"
    await ctx.send(embed=discord.Embed(title="Top Tickets This Week",description=desc or "No data."))
@bot.command()
async def longesthours(ctx):
    sorted_users=sorted(data.items(),key=lambda x:x[1]["hours_week"],reverse=True)[:10]
    desc=""
    for i,(uid,user) in enumerate(sorted_users,1):
        member=ctx.guild.get_member(int(uid))
        if member:
            desc+=f"{i}. {member.display_name} - {round(user['hours_week'],2)} hrs\n"
    await ctx.send(embed=discord.Embed(title="Top Hours This Week",description=desc or "No data."))
# -------- ADMIN CHECK --------
def is_admin():
    async def predicate(ctx):
        return ctx.author.id in ADMIN_IDS
    return commands.check(predicate)
# -------- ADMIN COMMANDS --------
@bot.command()
@is_admin()
async def brownie(ctx, member: discord.Member, amount: float):
    """Add or remove BP from a member."""
    user = get_user(member.id)
    user["bp_week"] = max(0, user["bp_week"] + amount)
    save_data()
    await ctx.send(f"{member.display_name} now has {round(user['bp_week'],2)} BP.")
@bot.command()
@is_admin()
async def resetuser(ctx, member: discord.Member):
    """Reset all stats for a member"""
    if str(member.id) in data:
        del data[str(member.id)]
    save_data()
    await ctx.send(f"{member.display_name}'s stats have been reset.")
@bot.command()
@is_admin()
async def resetallbp(ctx):
    """Reset BP for everyone"""
    for u in data.values():
        u["bp_week"] = 0
    save_data()
    await ctx.send("All BP has been reset.")
@bot.command()
@is_admin()
async def resetticket(ctx, member: discord.Member):
    """Reset ticket stats for one member"""
    user = get_user(member.id)
    user["tickets_week"] = 0
    user["messages_week"] = 0
    save_data()
    await ctx.send(f"{member.display_name}'s ticket stats reset.")
@bot.command()
@is_admin()
async def resetalltickets(ctx):
    """Reset tickets for everyone"""
    for u in data.values():
        u["tickets_week"] = 0
        u["messages_week"] = 0
    save_data()
    await ctx.send("All ticket stats have been reset.")
@bot.command()
@is_admin()
async def forceclockin(ctx, member: discord.Member):
    """Force a user to clock in"""
    user = get_user(member.id)
    user["clocked_in"] = True
    user["clock_time"] = time.time()
    user["last_activity"] = time.time()
    save_data()
    await ctx.send(f"{member.display_name} has been force clocked in.")
@bot.command()
@is_admin()
async def forceclockout(ctx, member: discord.Member):
    """Force a user to clock out"""
    user = get_user(member.id)
    if user["clocked_in"] and user["clock_time"]:
        elapsed = min(time.time() - user["clock_time"], MAX_SESSION_HOURS * 3600)
        user["hours_week"] += round(elapsed / 3600, 2)
    user["clocked_in"] = False
    user["clock_time"] = None
    save_data()
    await ctx.send(f"{member.display_name} has been force clocked out.")
@bot.command()
@is_admin()
async def forceclockoutall(ctx):
    """Force everyone to clock out"""
    for uid, u in data.items():
        if u["clocked_in"] and u["clock_time"]:
            elapsed = min(time.time() - u["clock_time"], MAX_SESSION_HOURS * 3600)
            u["hours_week"] += round(elapsed / 3600, 2)
        u["clocked_in"] = False
        u["clock_time"] = None
    save_data()
    await ctx.send("Everyone has been force clocked out.")
@bot.command()
@is_admin()
async def badducklings(ctx):
    """Least work this week + staff who never clocked in"""
    staff_role = ctx.guild.get_role(STAFF_ROLE_ID)
    never = []
    workers = []
    if staff_role:
        for member in staff_role.members:
            if member.bot:
                continue
            u = data.get(str(member.id))
            if not u or (
                u.get("hours_week", 0) == 0
                and u.get("messages_week", 0) == 0
                and not u.get("clocked_in")
            ):
                never.append(member.display_name)
            else:
                workers.append((member.display_name, u))
    else:
        # No staff role configured — fall back to people the bot has data on.
        for uid, u in data.items():
            member = ctx.guild.get_member(int(uid))
            if member and not member.bot:
                workers.append((member.display_name, u))
    # least BP first, hours as tiebreaker
    workers.sort(key=lambda x: (x[1].get("bp_week", 0), x[1].get("hours_week", 0)))
    embed = discord.Embed(title="Bad Ducklings Report", color=discord.Color.red())
    desc = ""
    for i, (name, u) in enumerate(workers[:10], 1):
        desc += (
            f"{i}. {name} - {round(u.get('bp_week', 0), 2)} BP | "
            f"{u.get('tickets_week', 0)} tickets | "
            f"{round(u.get('hours_week', 0), 2)} hrs\n"
        )
    embed.add_field(name="Least Work This Week", value=desc or "Nobody has done any work yet.", inline=False)
    if staff_role:
        if never:
            never_text = ", ".join(never)
            if len(never_text) > 1024:
                shown = []
                total = 0
                for n in never:
                    if total + len(n) + 2 > 950:
                        break
                    shown.append(n)
                    total += len(n) + 2
                never_text = ", ".join(shown) + f" ...and {len(never) - len(shown)} more"
        else:
            never_text = "Everyone has clocked in!"
        embed.add_field(name="Never Clocked In", value=never_text, inline=False)
    else:
        embed.set_footer(text="Set STAFF_ROLE_ID in the config to also catch staff who never clocked in.")
    await ctx.send(embed=embed)
# ---------------- ROMANTIC COMMANDS ----------------
ROMANTIC_GIFS={
"slap":"https://media.giphy.com/media/Gf3AUz3eBNbTW/giphy.gif",
"hug":"https://media.giphy.com/media/l2QDM9Jnim1YVILXa/giphy.gif",
"kiss":"https://media.giphy.com/media/G3va31oEEnIkM/giphy.gif",
"cuddle":"https://media.giphy.com/media/od5H3PmEG5EVq/giphy.gif",
"poke":"https://media.giphy.com/media/3o6ZtpxSZbQRRnwCKQ/giphy.gif",
"tickle":"https://media.giphy.com/media/3o6ZtpxSZbQRRnwCKQ/giphy.gif"
}
def _register_romantic(action, gif):
    @bot.command(name=action)
    async def romantic_cmd(ctx, member: discord.Member):
        user = get_user(ctx.author.id)
        target_key = str(member.id)
        counters = user["romantic_counters"][action]
        counters[target_key] = counters.get(target_key, 0) + 1
        count = counters[target_key]
        embed = discord.Embed(
            title=f"{ctx.author.display_name} {action}s {member.display_name}",
            description=f"{ctx.author.mention} has {action}ed {member.mention} **{count} times**"
        )
        embed.set_image(url=gif)
        save_data()
        await ctx.send(embed=embed)
    romantic_cmd.__name__ = f"romantic_{action}"
for _action, _gif in ROMANTIC_GIFS.items():
    _register_romantic(_action, _gif)
# ---------------- ERRORS ----------------
@bot.event
async def on_command_error(ctx, error):
    if isinstance(error, commands.CommandNotFound):
        return
    if isinstance(error, NotAdmin):
        embed = discord.Embed(
            title="You are not admin",
            description="*Ask to become admin (even though u wont become it)*",
            color=discord.Color.red()
        )
        await ctx.send(embed=embed)
        return
    if isinstance(error, NotClockedIn):
        embed = discord.Embed(
            title="Not clocked in",
            description="*clock in to use the commands*",
            color=discord.Color.orange()
        )
        await ctx.send(embed=embed)
        return
    if isinstance(error, commands.NoPrivateMessage):
        await ctx.send("This command only works in a server.")
        return
    if isinstance(error, commands.MissingRequiredArgument):
        await ctx.send(f"Missing argument: `{error.param.name}`")
        return
    if isinstance(error, commands.BadArgument):
        await ctx.send(f"Bad argument: {error}")
        return
    if isinstance(error, commands.CheckFailure):
        return
    print(f"Unhandled error in {ctx.command}: {error!r}")
    raise error
# ---------------- READY ----------------
@bot.event
async def on_ready():
    bot.add_view(ClockPanel())
    # Everyone clocked in before the restart STAYS clocked in.
    # Sessions older than MAX_SESSION_HOURS still get closed out so nobody
    # racks up phantom multi-day hours; everyone else gets a fresh idle
    # timer so the auto-clockout doesn't punish them for bot downtime.
    now = time.time()
    cutoff = now - MAX_SESSION_HOURS * 3600
    changed = False
    for u in data.values():
        if not u.get("clocked_in"):
            continue
        if not u.get("clock_time") or u["clock_time"] < cutoff:
            if u.get("clock_time"):
                elapsed = min(now - u["clock_time"], MAX_SESSION_HOURS * 3600)
                u["hours_week"] = u.get("hours_week", 0) + round(elapsed / 3600, 2)
            u["clocked_in"] = False
            u["clock_time"] = None
        else:
            # survived the restart — fresh idle window starting now
            u["last_activity"] = now
        changed = True
    if changed:
        save_data()
    if not auto_update_panel.is_running():
        auto_update_panel.start()
    if not auto_clockout_idle.is_running():
        auto_clockout_idle.start()
    await update_clock_panel()
    print(f"Logged in as {bot.user}")
bot.run(TOKEN)
