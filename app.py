import os
import requests
from flask import Flask, redirect, url_for, session, render_template, request, flash
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


def bot_headers():
    token = os.getenv("DISCORD_TOKEN") or os.getenv("DISCORD_BOT_TOKEN")
    if not token:
        return None
    return {
        "Authorization": f"Bot {token}",
        "User-Agent": "RavexPanel (https://ravex-panel.onrender.com, 1.0)"
    }


def bot_is_in_guild(guild_id):
    """Bot o sunucuda üye mi? GET /guilds/{id} ile kesin kontrol."""
    headers = bot_headers()
    if not headers:
        return False
    try:
        r = requests.get(
            f"https://discord.com/api/v10/guilds/{guild_id}",
            headers=headers,
            timeout=8
        )
        return r.status_code == 200
    except Exception as e:
        print(f"bot_is_in_guild({guild_id}) hata: {e}")
        return False


def get_bot_guilds():
    """Botun bulunduğu sunucu ID'lerini (str) döner."""
    headers = bot_headers()
    if not headers:
        print("get_bot_guilds: DISCORD_TOKEN yok")
        return set()

    try:
        response = requests.get(
            "https://discord.com/api/v10/users/@me/guilds",
            headers=headers,
            timeout=10
        )
        print(f"get_bot_guilds status={response.status_code}")
        if response.status_code == 200:
            data = response.json()
            if isinstance(data, list):
                ids = {str(g["id"]) for g in data if g.get("id")}
                print(f"get_bot_guilds ids={ids}")
                return ids
        print(f"get_bot_guilds body={response.text[:300]}")
    except Exception as e:
        print(f"get_bot_guilds hata: {e}")
    return set()


def get_bot_status():
    """Bot online mı + kaç sunucuda + kullanıcı adı."""
    headers = bot_headers()
    if not headers:
        return {"online": False, "guild_count": 0, "username": None}

    try:
        r = requests.get(
            "https://discord.com/api/v10/users/@me",
            headers=headers,
            timeout=8
        )
        if r.status_code != 200:
            return {"online": False, "guild_count": 0, "username": None}

        data = r.json()
        guild_ids = get_bot_guilds()
        return {
            "online": True,
            "guild_count": len(guild_ids),
            "username": data.get("username"),
        }
    except Exception:
        return {"online": False, "guild_count": 0, "username": None}


def can_manage_guild(guild):
    permissions = int(guild.get("permissions", 0))
    is_owner = bool(guild.get("owner", False))
    is_admin = (permissions & 0x8) == 0x8
    can_manage = (permissions & 0x20) == 0x20
    return is_owner or is_admin or can_manage


def get_guild_roles(guild_id):
    headers = bot_headers()
    if not headers:
        return []
    try:
        response = requests.get(
            f"https://discord.com/api/v10/guilds/{guild_id}/roles",
            headers=headers,
            timeout=10
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
    headers = bot_headers()
    if not headers:
        return []
    try:
        response = requests.get(
            f"https://discord.com/api/v10/guilds/{guild_id}/channels",
            headers=headers,
            timeout=10
        )
        if response.status_code == 200:
            channels = response.json()
            text_channels = [c for c in channels if c.get("type") == 0]
            text_channels.sort(key=lambda c: c["name"].lower())
            return text_channels
    except Exception as e:
        print(f"get_guild_channels hata: {e}")
    return []


@app.route("/")
def index():
    user = session.get("user")
    client_id = os.getenv("DISCORD_CLIENT_ID")
    bot_status = get_bot_status()

    if not user:
        return render_template(
            "index.html",
            user=None,
            guilds=[],
            guilds_without_bot=[],
            client_id=client_id,
            bot_status=bot_status
        )

    all_guilds = session.get("all_manageable_guilds")
    if not all_guilds:
        all_guilds = session.get("guilds", []) + session.get("guilds_without_bot", [])

    bot_guild_ids = get_bot_guilds()

    with_bot = []
    without_bot = []
    for g in all_guilds:
        gid = str(g["id"])
        in_bot = gid in bot_guild_ids or bot_is_in_guild(gid)
        print(f"guild check: {g.get('name')} id={gid} in_bot={in_bot}")
        if in_bot:
            with_bot.append(g)
        else:
            without_bot.append(g)

    session["guilds"] = with_bot
    session["guilds_without_bot"] = without_bot

    return render_template(
        "index.html",
        user=user,
        guilds=with_bot,
        guilds_without_bot=without_bot,
        client_id=client_id,
        bot_status=bot_status
    )


@app.route("/login")
def login():
    redirect_uri = url_for("callback", _external=True)
    return discord.authorize_redirect(redirect_uri)


@app.route("/callback")
def callback():
    discord.authorize_access_token()
    resp = discord.get("users/@me")
    user = resp.json()
    session["user"] = {
        "id": str(user["id"]),
        "username": user["username"],
        "avatar": user.get("avatar"),
        "discriminator": user.get("discriminator", "0")
    }

    guilds_resp = discord.get("users/@me/guilds")
    user_guilds = guilds_resp.json()
    if not isinstance(user_guilds, list):
        user_guilds = []

    bot_guild_ids = get_bot_guilds()

    with_bot = []
    without_bot = []
    for guild in user_guilds:
        if not can_manage_guild(guild):
            continue

        guild_id = str(guild["id"])
        info = {
            "id": guild_id,
            "name": guild["name"],
            "icon": guild.get("icon"),
            "owner": bool(guild.get("owner", False))
        }
        in_bot = guild_id in bot_guild_ids or bot_is_in_guild(guild_id)
        print(f"callback check: {info['name']} id={guild_id} in_bot={in_bot}")
        if in_bot:
            with_bot.append(info)
        else:
            without_bot.append(info)

    session["guilds"] = with_bot
    session["guilds_without_bot"] = without_bot
    session["all_manageable_guilds"] = with_bot + without_bot
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

    guild_id = str(guild_id)
    guilds = session.get("guilds", [])
    guild = next((g for g in guilds if str(g["id"]) == guild_id), None)
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

    guild_id = str(guild_id)
    guilds = session.get("guilds", [])
    guild = next((g for g in guilds if str(g["id"]) == guild_id), None)
    if not guild:
        return "Yetkin yok.", 403

    allowed_keys = [
        "guard_kanal", "guard_rol", "guard_bot",
        "guard_webhook", "guard_rightclick", "oto_nick",
        "seviye_sistemi", "dogum_gunu_sistemi",
        "filter_kufur", "filter_reklam", "filter_caps", "filter_spam"
    ]
    if key not in allowed_keys:
        return "Geçersiz ayar.", 400

    settings = load_settings()
    default_true = {
        "filter_kufur", "filter_reklam", "filter_caps",
        "filter_spam", "seviye_sistemi"
    }
    current = settings.get(guild_id, {}).get(key, key in default_true)
    set_guild_setting(guild_id, key, not bool(current))

    flash("Ayar güncellendi.", "success")
    return redirect(f"/guild/{guild_id}")


@app.route("/guild/<guild_id>/update", methods=["POST"])
def update_setting(guild_id):
    user = session.get("user")
    if not user:
        return redirect("/")

    guild_id = str(guild_id)
    guilds = session.get("guilds", [])
    guild = next((g for g in guilds if str(g["id"]) == guild_id), None)
    if not guild:
        return "Yetkin yok.", 403

    key = request.form.get("key")
    value = request.form.get("value", "").strip()

    allowed_keys = [
        "ust_yetkili_rol", "yetkili_rol", "oto_rol",
        "kayitsiz_rol", "aile_rol", "jail_rol",
        "log_kanali", "ticket_log_kanali",
        "kurallar_kanali", "hosgeldin_kanali", "tuzak_kanali",
        "dogum_gunu_rol", "dogum_gunu_kanali"
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

    guild_id = str(guild_id)
    guilds = session.get("guilds", [])
    guild = next((g for g in guilds if str(g["id"]) == guild_id), None)
    if not guild:
        return "Yetkin yok.", 403

    action = request.form.get("action")
    kelime = request.form.get("kelime", "").strip().lower()

    VARSAYILAN = ["amk", "aq", "orospu", "piç", "sik", "yarrak", "ibne", "göt"]
    settings = load_settings()
    liste = list(settings.get(guild_id, {}).get("kufur_listesi", list(VARSAYILAN)))

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
