"""MaTaO-RO web UI: log in with a game account, search the cash shop, add cash points
and buy cash shop items.

Cash points and items are not written to the account directly: the map server keeps
account variables in memory while a player is online and would overwrite the change.
Rows are queued in `web_cashpoints` (top-ups) and `web_cashshop_orders` (purchases)
instead, and npc/custom/web_cashpoints.txt processes them in game: within a few
seconds while the account is online, otherwise at its next login. Bought items are
sent by mail (RODEX) to the chosen character.
"""
import hashlib
import hmac
import json
import os
import secrets
import threading
import time
from datetime import timedelta
from functools import wraps

import pymysql
from flask import (Flask, abort, flash, g, redirect, render_template, request,
                   session, url_for)

DB = dict(
    host=os.environ.get("DB_HOST", "db"),
    port=int(os.environ.get("DB_PORT", "3306")),
    user=os.environ.get("DB_USER", "ragnarok"),
    password=os.environ.get("DB_PASS", "ragnarok"),
    database=os.environ.get("DB_NAME", "ragnarok"),
    charset="utf8mb4",
    autocommit=True,
    connect_timeout=5,
)
USE_MD5 = os.environ.get("USE_MD5_PASSWORDS", "no").lower() in ("yes", "1", "true")
ICON_URL = os.environ.get("ITEM_ICON_URL", "https://static.divine-pride.net/images/items/item/{id}.png")
ITEM_URL = os.environ.get("ITEM_INFO_URL", "https://www.divine-pride.net/database/item/{id}")
SERVER_NAME = os.environ.get("SERVER_TITLE", "MaTaO-RO")
ITEMS_FILE = os.environ.get("ITEMS_FILE", os.path.join(os.path.dirname(__file__), "data", "cashshop.json"))

# The cash shop keeps the balance in a 32-bit integer
MAX_CASH = 2_147_483_647
PAGE_SIZE = 48
MAX_BUY_QUANTITY = 10
LOGIN_WINDOW = 300
LOGIN_MAX_FAILURES = 10

CASH_TABS = {0: "New", 1: "Hot", 2: "Limited", 3: "Rental", 4: "Gear",
             5: "Buff", 6: "Heal", 7: "Other", 8: "Sale"}
CATEGORIES = [("headgear", "Headgear"), ("armor", "Armor"), ("weapon", "Weapon"),
              ("shield", "Shield"), ("garment", "Garment"), ("shoes", "Shoes"),
              ("accessory", "Accessory"), ("costume", "Costume"), ("shadow", "Shadow gear")]
SORTS = {"name": "Name", "price": "Price (low to high)", "price_desc": "Price (high to low)",
         "id": "Item ID", "level": "Equip level"}

CREATE_TABLE = """
CREATE TABLE IF NOT EXISTS `web_cashpoints` (
  `id` int(10) unsigned NOT NULL AUTO_INCREMENT,
  `account_id` int(11) unsigned NOT NULL,
  `amount` int(10) unsigned NOT NULL,
  `requested_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `requested_ip` varchar(45) NOT NULL DEFAULT '',
  `delivered_at` datetime DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `pending` (`delivered_at`, `account_id`)
) ENGINE=InnoDB
"""

CREATE_ORDERS_TABLE = """
CREATE TABLE IF NOT EXISTS `web_cashshop_orders` (
  `id` int(10) unsigned NOT NULL AUTO_INCREMENT,
  `account_id` int(11) unsigned NOT NULL,
  `char_id` int(11) unsigned NOT NULL,
  `item_id` int(10) unsigned NOT NULL,
  `quantity` smallint(5) unsigned NOT NULL,
  `price` int(10) unsigned NOT NULL,
  `total` int(10) unsigned NOT NULL,
  `status` enum('pending','done','failed') NOT NULL DEFAULT 'pending',
  `note` varchar(100) NOT NULL DEFAULT '',
  `requested_at` datetime NOT NULL DEFAULT CURRENT_TIMESTAMP,
  `requested_ip` varchar(45) NOT NULL DEFAULT '',
  `processed_at` datetime DEFAULT NULL,
  PRIMARY KEY (`id`),
  KEY `pending` (`status`, `account_id`)
) ENGINE=InnoDB
"""

app = Flask(__name__)
app.config.update(
    # Without SECRET_KEY sessions only survive until the container restarts
    SECRET_KEY=os.environ.get("SECRET_KEY") or secrets.token_hex(32),
    SESSION_COOKIE_HTTPONLY=True,
    SESSION_COOKIE_SAMESITE="Lax",
    SESSION_COOKIE_SECURE=os.environ.get("COOKIE_SECURE", "no").lower() in ("yes", "1", "true"),
    PERMANENT_SESSION_LIFETIME=timedelta(hours=8),
)

with open(ITEMS_FILE, encoding="utf-8") as f:
    ITEMS = json.load(f)
for it in ITEMS:
    it["search"] = f"{it['name']} {it['aegis']} {it['id']}".lower()
ITEM_BY_KEY = {(it["tab"], it["id"]): it for it in ITEMS}
ITEM_NAMES = {it["id"]: it["name"] for it in ITEMS}

_failures = {}
_failures_lock = threading.Lock()


def db():
    if "db" not in g:
        g.db = pymysql.connect(**DB)
    return g.db


@app.teardown_appcontext
def close_db(_exc):
    conn = g.pop("db", None)
    if conn is not None:
        conn.close()


def query(sql, args=(), one=False):
    with db().cursor() as cur:
        cur.execute(sql, args)
        return cur.fetchone() if one else cur.fetchall()


def ensure_table():
    for attempt in range(30):
        try:
            conn = pymysql.connect(**DB)
            with conn.cursor() as cur:
                cur.execute(CREATE_TABLE)
                cur.execute(CREATE_ORDERS_TABLE)
            conn.close()
            return
        except pymysql.MySQLError:
            time.sleep(2)
    raise RuntimeError("database is not reachable")


def csrf_token():
    if "csrf" not in session:
        session["csrf"] = secrets.token_urlsafe(32)
    return session["csrf"]


def check_csrf():
    sent = request.form.get("csrf", "")
    if not sent or not hmac.compare_digest(sent, session.get("csrf", "")):
        abort(400)


@app.context_processor
def template_globals():
    return dict(csrf_token=csrf_token, server_name=SERVER_NAME, user=session.get("userid"),
                icon_url=ICON_URL, item_url=ITEM_URL, cash_tabs=CASH_TABS, item_names=ITEM_NAMES)


def login_required(view):
    @wraps(view)
    def wrapper(*args, **kwargs):
        if "account_id" not in session:
            return redirect(url_for("login", next=request.full_path))
        return view(*args, **kwargs)
    return wrapper


def too_many_failures(ip):
    now = time.time()
    with _failures_lock:
        recent = [t for t in _failures.get(ip, []) if now - t < LOGIN_WINDOW]
        _failures[ip] = recent
        return len(recent) >= LOGIN_MAX_FAILURES


def record_failure(ip):
    with _failures_lock:
        _failures.setdefault(ip, []).append(time.time())


def password_matches(given, stored):
    if USE_MD5:
        given = hashlib.md5(given.encode("utf-8")).hexdigest()
        return hmac.compare_digest(given.lower(), stored.lower())
    return hmac.compare_digest(given.encode("utf-8"), stored.encode("utf-8"))


@app.route("/login", methods=["GET", "POST"])
def login():
    if request.method == "POST":
        check_csrf()
        ip = request.remote_addr or ""
        if too_many_failures(ip):
            flash("Too many failed logins. Try again in a few minutes.", "error")
            return render_template("login.html"), 429
        userid = request.form.get("userid", "").strip()
        password = request.form.get("password", "")
        row = query("SELECT `account_id`, `userid`, `user_pass`, `sex`, `state`, `unban_time` "
                    "FROM `login` WHERE `userid` = %s", (userid,), one=True) if userid else None
        if (not row or row[3] == "S" or not password_matches(password, row[2])):
            record_failure(ip)
            flash("Wrong account name or password.", "error")
            return render_template("login.html", userid=userid), 401
        if row[4] != 0 or row[5] > time.time():
            flash("This account is blocked.", "error")
            return render_template("login.html", userid=userid), 403
        session.clear()
        session.permanent = True
        session["account_id"] = row[0]
        session["userid"] = row[1]
        target = request.args.get("next", "")
        # Only follow local redirects
        if not target.startswith("/") or target.startswith("//"):
            target = url_for("shop")
        return redirect(target)
    if "account_id" in session:
        return redirect(url_for("shop"))
    return render_template("login.html")


@app.route("/logout", methods=["POST"])
def logout():
    check_csrf()
    session.clear()
    return redirect(url_for("login"))


def search_items(q, category, tab, sort):
    words = q.lower().split()
    found = [it for it in ITEMS
             if all(w in it["search"] for w in words)
             and (not category or category in it["categories"])
             and (tab is None or it["tab"] == tab)]
    keys = {
        "name": lambda it: (it["name"].lower(), it["id"]),
        "price": lambda it: (it["price"], it["name"].lower()),
        "price_desc": lambda it: (-it["price"], it["name"].lower()),
        "id": lambda it: it["id"],
        "level": lambda it: (it["level"], it["name"].lower()),
    }
    return sorted(found, key=keys.get(sort, keys["name"]))


@app.route("/")
@login_required
def shop():
    q = request.args.get("q", "").strip()[:100]
    category = request.args.get("category", "")
    if category not in dict(CATEGORIES):
        category = ""
    tab = request.args.get("tab", "")
    tab = int(tab) if tab.isdigit() and int(tab) in CASH_TABS else None
    sort = request.args.get("sort", "name")
    if sort not in SORTS:
        sort = "name"
    page = request.args.get("page", "1")
    page = max(1, int(page)) if page.isdigit() else 1

    results = search_items(q, category, tab, sort)
    pages = max(1, (len(results) + PAGE_SIZE - 1) // PAGE_SIZE)
    page = min(page, pages)
    shown = results[(page - 1) * PAGE_SIZE: page * PAGE_SIZE]
    used_tabs = sorted({it["tab"] for it in ITEMS})
    return render_template("shop.html", items=shown, total=len(results), page=page, pages=pages,
                           q=q, category=category, tab=tab, sort=sort, categories=CATEGORIES,
                           sorts=SORTS, used_tabs=used_tabs, balance=balance(session["account_id"]))


def balance(account_id):
    stored = query("SELECT `value` FROM `acc_reg_num` WHERE `account_id` = %s "
                   "AND `key` = '#CASHPOINTS' AND `index` = 0", (account_id,), one=True)
    pending = query("SELECT COALESCE(SUM(`amount`), 0) FROM `web_cashpoints` "
                    "WHERE `account_id` = %s AND `delivered_at` IS NULL", (account_id,), one=True)
    reserved = query("SELECT COALESCE(SUM(`total`), 0) FROM `web_cashshop_orders` "
                     "WHERE `account_id` = %s AND `status` = 'pending'", (account_id,), one=True)
    online = query("SELECT COUNT(*) FROM `char` WHERE `account_id` = %s AND `online` = 1",
                   (account_id,), one=True)
    stored = int(stored[0]) if stored else 0
    pending = int(pending[0])
    reserved = int(reserved[0])
    # total: what the account holds once queued top-ups arrive; available: minus open orders
    return dict(stored=stored, pending=pending, reserved=reserved, total=stored + pending,
                available=stored + pending - reserved, online=online[0] > 0)


def characters(account_id):
    rows = query("SELECT `char_id`, `name`, `base_level` FROM `char` "
                 "WHERE `account_id` = %s AND `delete_date` = 0 ORDER BY `char_num`", (account_id,))
    return [dict(char_id=r[0], name=r[1], level=r[2]) for r in rows]


@app.route("/cash", methods=["GET", "POST"])
@login_required
def cash():
    account_id = session["account_id"]
    if request.method == "POST":
        check_csrf()
        raw = request.form.get("amount", "").replace(",", "").strip()
        amount = int(raw) if raw.isdigit() else 0
        current = balance(account_id)
        if amount < 1:
            flash("Enter a whole number of cash points greater than 0.", "error")
        elif current["total"] + amount > MAX_CASH:
            flash(f"The cash shop cannot hold more than {MAX_CASH:,} points. "
                  f"You can add at most {max(0, MAX_CASH - current['total']):,} more.", "error")
        else:
            query("INSERT INTO `web_cashpoints` (`account_id`, `amount`, `requested_ip`) "
                  "VALUES (%s, %s, %s)", (account_id, amount, (request.remote_addr or "")[:45]))
            if current["online"]:
                flash(f"Added {amount:,} cash points. They arrive in game within a few seconds.", "ok")
            else:
                flash(f"Added {amount:,} cash points. They are credited when you next log in.", "ok")
        return redirect(url_for("cash"))
    history = query("SELECT `amount`, `requested_at`, `delivered_at` FROM `web_cashpoints` "
                    "WHERE `account_id` = %s ORDER BY `id` DESC LIMIT 20", (account_id,))
    orders = query("SELECT o.`item_id`, o.`quantity`, o.`total`, o.`status`, o.`note`, "
                   "o.`requested_at`, c.`name` FROM `web_cashshop_orders` o "
                   "LEFT JOIN `char` c ON c.`char_id` = o.`char_id` "
                   "WHERE o.`account_id` = %s ORDER BY o.`id` DESC LIMIT 20", (account_id,))
    current = balance(account_id)
    return render_template("cash.html", balance=current, history=history, orders=orders,
                           max_add=max(0, MAX_CASH - current["total"]))


@app.route("/buy/<int:tab>/<int:item_id>", methods=["GET", "POST"])
@login_required
def buy(tab, item_id):
    item = ITEM_BY_KEY.get((tab, item_id))
    if not item:
        abort(404)
    account_id = session["account_id"]
    chars = characters(account_id)
    if request.method == "POST":
        check_csrf()
        raw_qty = request.form.get("quantity", "1").strip()
        quantity = int(raw_qty) if raw_qty.isdigit() else 0
        raw_char = request.form.get("char_id", "")
        char = next((c for c in chars if str(c["char_id"]) == raw_char), None)
        total = item["price"] * quantity
        current = balance(account_id)
        if not 1 <= quantity <= MAX_BUY_QUANTITY:
            flash(f"Choose a quantity between 1 and {MAX_BUY_QUANTITY}.", "error")
        elif char is None:
            flash("Choose which character should receive the item.", "error")
        elif total > current["available"]:
            flash(f"Not enough cash points: this costs {total:,} and you have "
                  f"{max(0, current['available']):,} available.", "error")
        else:
            query("INSERT INTO `web_cashshop_orders` (`account_id`, `char_id`, `item_id`, `quantity`, "
                  "`price`, `total`, `requested_ip`) VALUES (%s, %s, %s, %s, %s, %s, %s)",
                  (account_id, char["char_id"], item_id, quantity, item["price"], total,
                   (request.remote_addr or "")[:45]))
            when = "within a few seconds" if current["online"] else "when you next log in to the game"
            flash(f"Bought {quantity} x {item['name']} for {total:,} cash points. "
                  f"It will be mailed to {char['name']} {when}.", "ok")
            return redirect(url_for("cash"))
        return redirect(url_for("buy", tab=tab, item_id=item_id))
    return render_template("buy.html", item=item, chars=chars, balance=balance(account_id),
                           max_quantity=MAX_BUY_QUANTITY)


@app.route("/healthz")
def healthz():
    return "ok"


ensure_table()
