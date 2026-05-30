import discord
from discord.ext import commands, tasks
from discord.ui import View, Button
import json, os, time, random, threading
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
1509683024894103563
]

ADMIN_IDS = {
1303496457319350314,
823552875766349825,
1467325360113717394,
743203080773828670,
404758345707290655
}

CLOCK_PANEL_CHANNEL_ID = 1509746663177060434
CLOCK_ROLE_ID = 1509677187274379386

DATA_FILE = "staff_data.json"

MIN_MESSAGE_LENGTH = 2
TICKET_MESSAGES_PER_COMPLETION = 25
MAX_SESSION_HOURS = 12

# ---------------- BOT ----------------

intents = discord.Intents.all()
bot = commands.Bot(command_prefix="!", intents=intents)

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
        "romantic_counters": {a: 0 for a in ROMANTIC_ACTIONS},
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

    for action in ROMANTIC_ACTIONS:
        user["romantic_counters"].setdefault(action, 0)

    return user

# ---------------- RANK ----------------

def get_rank(bp):

    if bp >= 90:
        return "Big Boss"
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
    user["tickets_week"]+=1

    user["bp_week"]+=0.015

    user["last_messages"].append(content)

    if len(user["last_messages"])>10:
        user["last_messages"].pop(0)

    user["ticket_logs"].append(content)

    if len(user["ticket_logs"])>10:
        user["ticket_logs"].pop(0)

    if user["tickets_week"]%TICKET_MESSAGES_PER_COMPLETION==0:
        user["bp_week"]+=1

    save_data()

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

        user["romantic_counters"][action] += 1
        count = user["romantic_counters"][action]

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

# ---------------- READY ----------------

@bot.event
async def on_ready():

    bot.add_view(ClockPanel())

    # Auto-clock-out anyone who was clocked in across a bot crash or restart
    # so they don't accumulate phantom hours.
    cutoff = time.time() - MAX_SESSION_HOURS * 3600
    changed = False
    for u in data.values():
        if u.get("clocked_in") and (not u.get("clock_time") or u["clock_time"] < cutoff):
            if u.get("clock_time"):
                elapsed = min(time.time() - u["clock_time"], MAX_SESSION_HOURS * 3600)
                u["hours_week"] = u.get("hours_week", 0) + round(elapsed / 3600, 2)
            u["clocked_in"] = False
            u["clock_time"] = None
            changed = True
    if changed:
        save_data()

    if not auto_update_panel.is_running():
        auto_update_panel.start()

    await update_clock_panel()

    print(f"Logged in as {bot.user}")

bot.run(TOKEN)