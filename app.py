import os
import json
import requests
from flask import Flask, redirect, url_for, session, render_template, request, flash, get_flashed_messages
from authlib.integrations.flask_client import OAuth
from dotenv import load_dotenv
from pymongo import MongoClient
from werkzeug.middleware.proxy_fix import ProxyFix

load_dotenv()

app = Flask(__name__)
app.secret_key = os.getenv("SECRET_KEY")
app.wsgi_app = ProxyFix(app.wsgi_app, x_for=1, x_proto=1, x_host=1, x_prefix=1)

# MongoDB
MONGO_URI = os.getenv("MONGO_URI")
mongo_client = MongoClient(MONGO_URI) if MONGO_URI else None
mongo_db = mongo_client["ravex"] if mongo_client is not None else None
settings_collection = mongo_db["ayarlar"] if mongo_db is not None else None

# Discord OAuth
oauth = OAuth(app)
discord = oauth.register(
    name="discord",
    client_id=os.getenv("DISCORD_CLIENT_ID"),
    client_secret=os.getenv("DISCORD_CLIENT_SECRET"),
    access_token_url="https://discord.com/api/oauth2/token",
    authorize_url="https://discord.com/api/oauth2/authorize",
    api_base_url="https://discord.com/api/",
    client_kwargs={"scope": "identify guilds"}
)

def load_settings():
    data = {}
    if settings_collection is None:
        return data
    try:
        for doc in settings_collection.find():
            guild_id = str(doc.get("_id", ""))
            if not guild_id:
                continue
            ayarlar = dict(doc)
            ayarlar.pop("_id", None)
            data[guild_id] = ayarlar
    except Exception as e:
        print(f"load_settings hata: {e}")
    return data

def set_guild_setting(guild_id, key, value):
    if settings_collection is None:
        return False
    try:
        if value is None or value == "":
            settings_collection.update_one(
                {"_id": str(guild_id)},
                {"$unset": {key: ""}},
                upsert=True
            )
        else:
            settings_collection.update_one(
                {"_id": str(guild_id)},
                {"$set": {key: value}},
                upsert=True
            )
        return True
    except Exception as e:
        print(f"set_guild_setting hata: {e}")
        return False

def get_bot_guilds():
    token = os.getenv("DISCORD_TOKEN")
    headers = {"Authorization": f"Bot {token}"}
    try:
        response = requests.get("https://discord.com/api/users/@me/guilds", headers=headers)
        if response.status_code == 200:
            return {g["id"] for g in response.json()}
    except Exception as e:
        print(f"get_bot_guilds hata: {e}")
    return set()

def get_user_guilds_split(user_guilds, bot_guild_ids):
    """Botun olduğu ve olmadığı sunucuları ayırır"""
    with_bot = []
    without_bot = []

    for guild in user_guilds:
        guild_id = guild["id"]
        permissions = int(guild.get("permissions", 0))
        is_owner = guild.get("owner", False)
        is_admin = (permissions & 0x8) == 0x8

        # Sadece yönetici / sahip olduğu sunucular
        if not (is_owner or is_admin):
            continue

        info = {
            "id": guild_id,
            "name": guild["name"],
            "icon": guild.get("icon"),
            "owner": is_owner
        }

        if guild_id in bot_guild_ids:
            with_bot.append(info)
        else:
            without_bot.append(info)

    return with_bot, without_bot

def get_guild_roles(guild_id):
    token = os.getenv("DISCORD_TOKEN")
    headers = {"Authorization": f"Bot {token}"}
    try:
        response = requests.get(
            f"https://discord.com/api/guilds/{guild_id}/roles",
            headers=headers
        )
        if response.status_code == 200:
            roles = response.json()
            roles = [r for r in roles if r["name"] != "@everyone"]
            roles.sort(key=lambda r: r["name"].lower())
            return roles
    except Exception as e:
        print(f"get_guild_roles hata: {e}")
    return []

def get_guild_channels(guild_id):
    token = os.getenv("DISCORD_TOKEN")
    headers = {"Authorization": f"Bot {token}"}
    try:
        response = requests.get(
            f"https://discord.com/api/guilds/{guild_id}/channels",
            headers=headers
        )
        if response.status_code == 200:
            channels = response.json()
            text_channels = [c for c in channels if c["type"] == 0]
            text_channels.sort(key=lambda c: c["name"].lower())
            return text_channels
    except Exception as e:
        print(f"get_guild_channels hata: {e}")
    return []

@app.route("/")
def index():
    user = session.get("user")
    client_id = os.getenv("DISCORD_CLIENT_ID")
    guilds = session.get("guilds", [])
    guilds_without_bot = session.get("guilds_without_bot", [])

    return render_template(
        "index.html",
        user=user,
        guilds=guilds,
        guilds_without_bot=guilds_without_bot,
        client_id=client_id
    )

    guilds = session.get("guilds", [])
    guilds_without_bot = session.get("guilds_without_bot", [])
    client_id = os.getenv("DISCORD_CLIENT_ID")

    return render_template(
        "index.html",
        user=user,
        guilds=guilds,
        guilds_without_bot=guilds_without_bot,
        client_id=client_id
    )

@app.route("/login")
def login():
    redirect_uri = url_for("callback", _external=True)
    return discord.authorize_redirect(redirect_uri)

@app.route("/callback")
def callback():
    token = discord.authorize_access_token()
    resp = discord.get("users/@me")
    user = resp.json()
    session["user"] = {
        "id": user["id"],
        "username": user["username"],
        "avatar": user.get("avatar"),
        "discriminator": user.get("discriminator", "0")
    }

    guilds_resp = discord.get("users/@me/guilds")
    user_guilds = guilds_resp.json()
    bot_guild_ids = get_bot_guilds()

    with_bot, without_bot = get_user_guilds_split(user_guilds, bot_guild_ids)
    session["guilds"] = with_bot
    session["guilds_without_bot"] = without_bot

    return redirect("/")

@app.route("/logout")
def logout():
    session.clear()
    return redirect("/")

@app.route("/guild/<guild_id>")
def guild_settings(guild_id):
    user = session.get("user")
    if not user:
        return redirect("/")

    guilds = session.get("guilds", [])
    guild = next((g for g in guilds if g["id"] == guild_id), None)
    if not guild:
        return "Bu sunucuyu yönetme yetkin yok.", 403

    settings = load_settings()
    guild_settings = settings.get(guild_id, {})
    roles = get_guild_roles(guild_id)
    channels = get_guild_channels(guild_id)

    return render_template(
        "settings.html",
        user=user,
        guild=guild,
        settings=guild_settings,
        roles=roles,
        channels=channels
    )

@app.route("/guild/<guild_id>/toggle/<key>", methods=["POST"])
def toggle_setting(guild_id, key):
    user = session.get("user")
    if not user:
        return redirect("/")

    guilds = session.get("guilds", [])
    guild = next((g for g in guilds if g["id"] == guild_id), None)
    if not guild:
        return "Yetkin yok.", 403

    allowed_keys = [
        "guard_kanal", "guard_rol", "guard_bot",
        "guard_webhook", "guard_rightclick", "oto_nick"
    ]
    if key not in allowed_keys:
        return "Geçersiz ayar.", 400

    settings = load_settings()
    current = settings.get(guild_id, {}).get(key, False)
    set_guild_setting(guild_id, key, not current)

    flash("Ayar güncellendi.", "success")
    return redirect(f"/guild/{guild_id}")

@app.route("/guild/<guild_id>/update", methods=["POST"])
def update_setting(guild_id):
    user = session.get("user")
    if not user:
        return redirect("/")

    guilds = session.get("guilds", [])
    guild = next((g for g in guilds if g["id"] == guild_id), None)
    if not guild:
        return "Yetkin yok.", 403

    key = request.form.get("key")
    value = request.form.get("value", "").strip()

    allowed_keys = [
        "ust_yetkili_rol", "yetkili_rol", "oto_rol",
        "kayitsiz_rol", "aile_rol", "jail_rol",
        "log_kanali", "ticket_log_kanali",
        "kurallar_kanali", "hosgeldin_kanali", "tuzak_kanali"
    ]
    if key not in allowed_keys:
        return "Geçersiz ayar.", 400

    if value == "":
        set_guild_setting(guild_id, key, None)
    else:
        if not value.isdigit():
            flash("Geçersiz ID.", "success")
            return redirect(f"/guild/{guild_id}")
        set_guild_setting(guild_id, key, int(value))

    flash("Ayar kaydedildi.", "success")
    return redirect(f"/guild/{guild_id}")

@app.route("/guild/<guild_id>/kufur", methods=["POST"])
def kufur_islem(guild_id):
    user = session.get("user")
    if not user:
        return redirect("/")

    guilds = session.get("guilds", [])
    guild = next((g for g in guilds if g["id"] == guild_id), None)
    if not guild:
        return "Yetkin yok.", 403

    action = request.form.get("action")
    kelime = request.form.get("kelime", "").strip().lower()

    VARSAYILAN = ["amk", "aq", "orospu", "piç", "sik", "yarrak", "ibne", "göt"]
    settings = load_settings()
    liste = settings.get(guild_id, {}).get("kufur_listesi", list(VARSAYILAN))

    if action == "ekle" and kelime:
        if kelime not in liste:
            liste.append(kelime)
    elif action == "sil" and kelime:
        if kelime in liste:
            liste.remove(kelime)
    elif action == "sifirla":
        liste = list(VARSAYILAN)

    set_guild_setting(guild_id, "kufur_listesi", liste)
    flash("Küfür listesi güncellendi.", "success")
    return redirect(f"/guild/{guild_id}")

if __name__ == "__main__":
    app.run(debug=True, port=5000)
