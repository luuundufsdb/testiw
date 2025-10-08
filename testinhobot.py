import logging
from telegram import Update, InlineKeyboardButton, InlineKeyboardMarkup
from telegram.ext import Application, CommandHandler, ContextTypes, MessageHandler, filters, CallbackQueryHandler
from urllib.parse import quote, unquote
import psycopg2
import psycopg2.extras
from psycopg2 import pool
import os
from flask import Flask
import random
import threading
import time
from datetime import datetime, timedelta
import re
import unicodedata

def normalizar(texto):
    texto = texto.lower()
    texto = ''.join(c for c in unicodedata.normalize('NFD', texto)
                    if unicodedata.category(c) != 'Mn')
    return texto.strip()

# ================== CONFIGURAÇÕES ==================
TOKEN = os.getenv("BOT_TOKEN")
DATABASE_URL = os.getenv("NEON_DATABASE_URL")

POOL = None

ADMIN_IDS = {int(x) for x in os.getenv("ADMINS", "").split(",") if x.strip().isdigit()}
PESO_MAX = {1: 5.0, 2: 10.0, 3: 15.0, 4: 20.0, 5: 25.0, 6: 30.0}
LAST_COMMAND = {}
COOLDOWN = 1

MAX_ATRIBUTOS = 20
MAX_PERICIAS = 40
ATRIBUTOS_LISTA = [
    "Força",
    "Agilidade",
    "Vitalidade",
    "Raciocínio",
    "Equilíbrio",
    "Persuasão"
]
PERICIAS_LISTA = [
    "Luta",
    "Resistência",
    "Furtividade",
    "Pontaria",
    "Reflexo",
    "Sobrevivência",
    "Medicina",
    "Improviso",
    "Exploração",
    "Intuição",
    "Manipulação",
    "Confiança"
]
ATRIBUTOS_NORMAL = {normalizar(a): a for a in ATRIBUTOS_LISTA}
PERICIAS_NORMAL = {normalizar(p): p for p in PERICIAS_LISTA}

EDIT_PENDING = {}
EDIT_TIMERS = {}  # Para timeouts de edição

TRANSFER_PENDING = {}
ABANDON_PENDING = {}
LAST_RELOAD = {}

KIT_BONUS = {
    "kit basico": 1,
    "kit básico": 1,
    "basico": 1,
    "básico": 1,
    "kit intermediario": 2,
    "kit intermediário": 2,
    "intermediario": 2,
    "intermediário": 2,
    "kit avancado": 3,
    "kit avançado": 3,
    "avancado": 3,
    "avançado": 3,
}

CONSUMIVEIS = [
    "comida enlatada", "água", "garrafa d'água", "ração", "barrinha", "barra de cereal"
]

TRAUMAS = [
    "Hipervigilância: não consegue dormir sem vigiar todas as entradas.",
    "Tremor incontrolável nas mãos em situações de estresse.",
    "Mutismo temporário diante de sons altos.",
    "Ataques de pânico ao sentir cheiro de sangue.",
    "Flashbacks paralisantes ao ouvir gritos.",
    "Aversão a ambientes fechados (claustrofobia aguda).",
]

logging.basicConfig(level=logging.INFO)
logger = logging.getLogger(__name__)

# ================== POSTGRESQL ==================
def get_conn():
    conn = POOL.getconn()
    try:
        with conn.cursor() as c:
            c.execute("SELECT 1")
    except psycopg2.Error:
        POOL.putconn(conn, close=True)
        conn = POOL.getconn()
    return conn

def put_conn(conn):
    """Devolve uma conexão para o pool."""
    POOL.putconn(conn)

def init_db():
    conn = None
    try:
        conn = get_conn()
        c = conn.cursor()
        c.execute('''CREATE TABLE IF NOT EXISTS players (
            id BIGINT PRIMARY KEY,
            nome TEXT,
            username TEXT,
            peso_max REAL DEFAULT 0,
            hp INTEGER DEFAULT 0,
            sp INTEGER DEFAULT 0,
            rerolls INTEGER DEFAULT 3,
            hp_max INTEGER DEFAULT 0,
            sp_max INTEGER DEFAULT 0,
            fome INTEGER DEFAULT 0,
            sede INTEGER DEFAULT 0,
            sono INTEGER DEFAULT 0,
            traumas TEXT DEFAULT ''
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS usernames (
            username TEXT PRIMARY KEY,
            user_id BIGINT,
            first_name TEXT,
            last_seen BIGINT
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS atributos (
            player_id BIGINT,
            nome TEXT,
            valor INTEGER DEFAULT 0,
            PRIMARY KEY(player_id,nome)
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS pericias (
            player_id BIGINT,
            nome TEXT,
            valor INTEGER DEFAULT 0,
            PRIMARY KEY(player_id,nome)
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS inventario (
            player_id BIGINT,
            nome TEXT,
            peso REAL,
            quantidade INTEGER DEFAULT 1,
            PRIMARY KEY(player_id,nome)
        )''')
        for alter in [
            "ADD COLUMN IF NOT EXISTS consumivel BOOLEAN DEFAULT FALSE",
            "ADD COLUMN IF NOT EXISTS bonus TEXT DEFAULT '0'",
            "ADD COLUMN IF NOT EXISTS tipo TEXT DEFAULT ''",
            "ADD COLUMN IF NOT EXISTS arma_tipo TEXT DEFAULT ''",
            "ADD COLUMN IF NOT EXISTS arma_bonus TEXT DEFAULT '0'",
            "ADD COLUMN IF NOT EXISTS municao_atual INTEGER DEFAULT 0",
            "ADD COLUMN IF NOT EXISTS municao_max INTEGER DEFAULT 0",
            "ADD COLUMN IF NOT EXISTS armas_compat TEXT DEFAULT ''"
        ]:
            try:
                c.execute(f"ALTER TABLE inventario {alter};")
            except Exception:
                conn.rollback()
        c.execute('''CREATE TABLE IF NOT EXISTS catalogo (
            nome TEXT PRIMARY KEY,
            peso REAL
        )''')
        
        # =========== INÍCIO DA CORREÇÃO ===========
        try:
            # Garante que as colunas de bônus sejam do tipo TEXTO
            c.execute("ALTER TABLE catalogo ALTER COLUMN bonus TYPE TEXT;")
            c.execute("ALTER TABLE catalogo ALTER COLUMN arma_bonus TYPE TEXT;")
        except psycopg2.Error:
            # Ignora o erro se a coluna não existir ou já for do tipo correto
            conn.rollback()
        # =========== FIM DA CORREÇÃO ===========

        for alter in [
            "ADD COLUMN IF NOT EXISTS ultimo_alimento TIMESTAMP DEFAULT NOW()",
            "ADD COLUMN IF NOT EXISTS ultima_agua TIMESTAMP DEFAULT NOW()",
            "ADD COLUMN IF NOT EXISTS ultimo_sono TIMESTAMP DEFAULT NOW()"
        ]:
            try:
                c.execute(f"ALTER TABLE players {alter};")
            except Exception:
                conn.rollback()

        for alter in [
            "ADD COLUMN IF NOT EXISTS consumivel BOOLEAN DEFAULT FALSE",
            "ADD COLUMN IF NOT EXISTS bonus TEXT DEFAULT '0'",
            "ADD COLUMN IF NOT EXISTS tipo TEXT DEFAULT ''",
            "ADD COLUMN IF NOT EXISTS arma_tipo TEXT DEFAULT ''",
            "ADD COLUMN IF NOT EXISTS arma_bonus TEXT DEFAULT '0'",
            "ADD COLUMN IF NOT EXISTS muni_atual INTEGER DEFAULT 0",
            "ADD COLUMN IF NOT EXISTS muni_max INTEGER DEFAULT 0",
            "ADD COLUMN IF NOT EXISTS armas_compat TEXT DEFAULT ''",
            "ADD COLUMN IF NOT EXISTS rest_hunger INTEGER DEFAULT 0",
            "ADD COLUMN IF NOT EXISTS rest_thirst INTEGER DEFAULT 0"
        ]:
            try:
                c.execute(f"ALTER TABLE catalogo {alter};")
            except Exception:
                conn.rollback()

        c.execute('''CREATE TABLE IF NOT EXISTS pending_consumivel (
            user_id BIGINT PRIMARY KEY,
            nome TEXT,
            peso REAL,
            bonus TEXT,
            armas_compat TEXT,
            created_at TIMESTAMP DEFAULT NOW()
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS coma_bonus (
            target_id BIGINT PRIMARY KEY,
            bonus INTEGER DEFAULT 0
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS coma_teste (
            player_id BIGINT PRIMARY KEY,
            ultima_data DATE
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS turnos (
            player_id BIGINT,
            data DATE,
            caracteres INTEGER,
            mencoes TEXT,
            PRIMARY KEY (player_id, data)
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS xp_semana (
            player_id BIGINT,
            semana_inicio DATE,
            xp_total INTEGER DEFAULT 0,
            streak_atual INTEGER DEFAULT 0,
            PRIMARY KEY (player_id, semana_inicio)
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS interacoes_mutuas (
            semana_inicio DATE,
            jogador1 BIGINT,
            jogador2 BIGINT,
            PRIMARY KEY (semana_inicio, jogador1, jogador2)
        )''')
        c.execute('''CREATE TABLE IF NOT EXISTS liberados (
            user_id BIGINT PRIMARY KEY
        )''')
        conn.commit()
    finally:
        if conn:
            put_conn(conn)

def liberar_usuario(user_id: int):
    conn = get_conn()
    c = conn.cursor()
    c.execute("INSERT INTO liberados (user_id) VALUES (%s) ON CONFLICT DO NOTHING", (user_id,))
    conn.commit()
    put_conn(conn)

def desliberar_usuario(user_id: int):
    conn = get_conn()
    c = conn.cursor()
    c.execute("DELETE FROM liberados WHERE user_id=%s", (user_id,))
    conn.commit()
    put_conn(conn)

def is_liberado(uid: int) -> bool:
    if is_admin(uid):
        return True
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT 1 FROM liberados WHERE user_id=%s", (uid,))
    res = c.fetchone()
    put_conn(conn)
    return bool(res)

def acesso_negado(update):
    return update.message.reply_text("🚫 Você precisa ser liberado por um administrador para usar o bot.")

def register_username(user_id: int, username: str | None, first_name: str | None):
    if not username:
        return
    username = username.lower()
    now = int(time.time())
    conn = None
    try:
        conn = get_conn()
        c = conn.cursor()
        c.execute("INSERT INTO usernames(username, user_id, first_name, last_seen) VALUES(%s,%s,%s,%s) ON CONFLICT (username) DO UPDATE SET user_id=%s, first_name=%s, last_seen=%s",
            (username, user_id, first_name or '', now, user_id, first_name or '', now))
        c.execute("UPDATE players SET username=%s WHERE id=%s", (username, user_id))
        conn.commit()
    finally:
        if conn:
            put_conn(conn)

def username_to_id(user_tag: str) -> int | None:
    if not user_tag:
        return None
    if user_tag.startswith('@'):
        uname = user_tag[1:].lower()
    else:
        uname = user_tag.lower()
    conn = None
    try:
        conn = get_conn()
        c = conn.cursor()
        c.execute("SELECT user_id FROM usernames WHERE username=%s", (uname,))
        row = c.fetchone()
        return row[0] if row else None
    finally:
        if conn:
            put_conn(conn)

def get_player(uid):
    conn = None
    try:
        conn = get_conn()
        c = conn.cursor()
        c.execute("SELECT * FROM players WHERE id=%s", (uid,))
        row = c.fetchone()
        if not row:
            return None
        player = {
            "id": row["id"],
            "nome": row["nome"],
            "username": row["username"],
            "peso_max": row["peso_max"],
            "hp": row["hp"],
            "hp_max": row["hp_max"],
            "sp": row["sp"],
            "sp_max": row["sp_max"],
            "rerolls": row["rerolls"],
            "fome": row["fome"],
            "sede": row["sede"],
            "sono": row["sono"],
            "traumas": row.get("traumas", ""),
            "atributos": {},
            "pericias": {},
            "inventario": []
        }
        c.execute("SELECT nome, valor FROM atributos WHERE player_id=%s", (uid,))
        for a, v in c.fetchall():
            player["atributos"][a] = v
        c.execute("SELECT nome, valor FROM pericias WHERE player_id=%s", (uid,))
        for a, v in c.fetchall():
            player["pericias"][a] = v
        c.execute("SELECT nome,peso,quantidade,municao_atual,municao_max FROM inventario WHERE player_id=%s", (uid,))
        for n, p, q, mun_at, mun_max in c.fetchall():
            entry = {"nome": n, "peso": p, "quantidade": q}
            if mun_max is not None:
                entry["municao_atual"] = mun_at
                entry["municao_max"] = mun_max
            player["inventario"].append(entry)
        return player
    finally:
        if conn:
            put_conn(conn)

def resistencia_horas_max(resistencia):
    tabela = {1: 24, 2: 36, 3: 48, 4: 60, 5: 78, 6: 96}
    return tabela.get(max(1, min(6, resistencia)), 24)

def get_horas_sem_recursos(uid):
    conn = None
    try:
        conn = get_conn()
        c = conn.cursor()
        c.execute("SELECT ultimo_alimento, ultima_agua, ultimo_sono FROM players WHERE id=%s", (uid,))
        row = c.fetchone()
    finally:
        if conn:
            put_conn(conn)
    agora = datetime.now()
    if not row:
        return (None, None, None)
    ua, ug, us = row
    horas_sem_comer = (agora - ua).total_seconds()/3600 if ua else None
    horas_sem_beber = (agora - ug).total_seconds()/3600 if ug else None
    horas_sem_dormir = (agora - us).total_seconds()/3600 if us else None
    return (horas_sem_comer, horas_sem_beber, horas_sem_dormir)

def registrar_consumo(uid, tipo):
    now = datetime.now()
    conn = None
    try:
        conn = get_conn()
        c = conn.cursor()
        if tipo == "comida":
            c.execute("UPDATE players SET ultimo_alimento=%s WHERE id=%s", (now, uid))
        elif tipo == "bebida":
            c.execute("UPDATE players SET ultima_agua=%s WHERE id=%s", (now, uid))
        elif tipo == "sono":
            c.execute("UPDATE players SET ultimo_sono=%s WHERE id=%s", (now, uid))
        conn.commit()
    finally:
        if conn:
            put_conn(conn)

def vitalidade_para_hp(v):
    return [10, 15, 20, 25, 30, 35, 40][max(0, min(6, v))]

def equilibrio_para_sp(v):
    return [10, 15, 20, 25, 30, 35, 40][max(0, min(6, v))]

def faixa_status(val, tipo="fome"):
    if val < 25:
        return {"fome": "saciado", "sede": "hidratado", "sono": "descansado"}[tipo]
    elif val < 50:
        return "leve"
    elif val < 75:
        return "moderada"
    elif val < 90:
        return "grave"
    else:
        return "crítica"

def update_necessidades(uid, fome_delta=0, sede_delta=0, sono_delta=0):
    conn = None
    try:
        conn = get_conn()
        c = conn.cursor()
        c.execute("SELECT fome, sede, sono FROM players WHERE id=%s", (uid,))
        row = c.fetchone()
        if not row:
            return
        fome, sede, sono = row
        fome = min(100, max(0, fome + fome_delta))
        sede = min(100, max(0, sede + sede_delta))
        sono = min(100, max(0, sono + sono_delta))
        c.execute("UPDATE players SET fome=%s, sede=%s, sono=%s WHERE id=%s", (fome, sede, sono, uid))
        conn.commit()
    finally:
        if conn:
            put_conn(conn)

def create_player(uid, nome, username=None):
    conn = None
    try:
        conn = get_conn()
        c = conn.cursor()
        c.execute(
            "INSERT INTO players(id, nome, username, hp, sp, hp_max, sp_max) VALUES(%s, %s, %s, %s, %s, %s, %s) "
            "ON CONFLICT DO NOTHING",
            (uid, nome, (username or None), 0, 0, 0, 0)
        )
        for a in ATRIBUTOS_LISTA:
            c.execute("INSERT INTO atributos(player_id, nome, valor) VALUES(%s, %s, %s) ON CONFLICT DO NOTHING", (uid, a, 0))
        for p in PERICIAS_LISTA:
            c.execute("INSERT INTO pericias(player_id, nome, valor) VALUES(%s, %s, %s) ON CONFLICT DO NOTHING", (uid, p, 0))
        conn.commit()
    finally:
        if conn:
            put_conn(conn)

def update_player_field(uid, field, value):
    conn = None
    try:
        conn = get_conn()
        c = conn.cursor()
        c.execute(f"UPDATE players SET {field}=%s WHERE id=%s", (value, uid))
        conn.commit()
    finally:
        if conn:
            put_conn(conn)

def update_atributo(uid, nome, valor):
    conn = None
    try:
        conn = get_conn()
        c = conn.cursor()
        c.execute("UPDATE atributos SET valor=%s WHERE player_id=%s AND nome=%s", (valor, uid, nome))
        conn.commit()
    finally:
        if conn:
            put_conn(conn)

def update_pericia(uid, nome, valor):
    conn = None
    try:
        conn = get_conn()
        c = conn.cursor()
        c.execute("UPDATE pericias SET valor=%s WHERE player_id=%s AND nome=%s", (valor, uid, nome))
        conn.commit()
    finally:
        if conn:
            put_conn(conn)

def atualizar_necessidades_por_tempo(uid):
    player = get_player(uid)
    resistencia = player["pericias"].get("Resistência", 1)
    max_horas = resistencia_horas_max(resistencia)
    horas_sem_comer, horas_sem_beber, horas_sem_dormir = get_horas_sem_recursos(uid)
    fome = player.get("fome", 0)
    sede = player.get("sede", 0)
    sono = player.get("sono", 0)

    if horas_sem_comer is not None:
        if horas_sem_comer >= max_horas:
            fome = 100
        else:
            fome = int(100 * (horas_sem_comer / max_horas))
        update_player_field(uid, "fome", fome)
    if horas_sem_beber is not None:
        if horas_sem_beber >= max_horas:
            sede = 100
        else:
            sede = int(100 * (horas_sem_beber / max_horas))
        update_player_field(uid, "sede", sede)
    if horas_sem_dormir is not None:
        if horas_sem_dormir >= max_horas:
            sono = 100
        else:
            sono = int(100 * (horas_sem_dormir / max_horas))
        update_player_field(uid, "sono", sono)

def update_inventario(uid, item):
    conn = None
    try:
        conn = get_conn()
        c = conn.cursor()
        c.execute("SELECT quantidade FROM inventario WHERE player_id=%s AND LOWER(nome)=LOWER(%s)", (uid, item['nome']))
        row = c.fetchone()
        if row:
            c.execute("UPDATE inventario SET quantidade=%s, peso=%s WHERE player_id=%s AND LOWER(nome)=LOWER(%s)",
                      (item['quantidade'], item['peso'], uid, item['nome']))
        else:
            c.execute("INSERT INTO inventario(player_id, nome, peso, quantidade) VALUES (%s, %s, %s, %s)",
                      (uid, item['nome'], item['peso'], item['quantidade']))
        conn.commit()
    finally:
        if conn:
            put_conn(conn)
    
def buscar_item_inventario(uid, nome_procurado):
    conn = None
    try:
        conn = get_conn()
        c = conn.cursor()
        c.execute("SELECT nome, peso, quantidade FROM inventario WHERE player_id=%s", (uid,))
        rows = c.fetchall()
    finally:
        if conn:
            put_conn(conn)
    for nome, peso, quantidade in rows:
        if normalizar(nome) == normalizar(nome_procurado):
            return nome, peso, quantidade
    return None, None, None

def adjust_item_quantity(uid, item_nome, delta):
    conn = None
    try:
        conn = get_conn()
        c = conn.cursor()
        c.execute("SELECT quantidade, peso FROM inventario WHERE player_id=%s AND LOWER(nome)=LOWER(%s)", (uid, item_nome))
        row = c.fetchone()
        if not row:
            return False
        qtd, peso = row
        nova = qtd + delta
        if nova <= 0:
            c.execute("DELETE FROM inventario WHERE player_id=%s AND LOWER(nome)=LOWER(%s)", (uid, item_nome))
        else:
            c.execute("UPDATE inventario SET quantidade=%s WHERE player_id=%s AND LOWER(nome)=LOWER(%s)", (nova, uid, item_nome))
        conn.commit()
        return True
    finally:
        if conn:
            put_conn(conn)

def get_catalog_item(nome: str):
    conn = None
    try:
        conn = get_conn()
        c = conn.cursor()
        c.execute("SELECT nome, peso, consumivel, bonus, tipo, arma_tipo, arma_bonus, muni_atual, muni_max, armas_compat, rest_hunger, rest_thirst FROM catalogo WHERE LOWER(nome)=LOWER(%s)", (nome,))
        row = c.fetchone()
    finally:
        if conn:
            put_conn(conn)
    if not row:
        return None
    return {
        "nome": row[0], "peso": row[1], "consumivel": row[2], "bonus": row[3], "tipo": row[4],
        "arma_tipo": row[5], "arma_bonus": row[6], "muni_atual": row[7], "muni_max": row[8], "armas_compat": row[9],
        "rest_hunger": row[10], "rest_thirst": row[11]
    }

def add_catalog_item(nome: str, peso: float, consumivel: bool = False, bonus: str = '0', tipo: str = '', arma_tipo: str = '', arma_bonus: str = '0', muni_atual: int = 0, muni_max: int = 0, armas_compat: str = '', rest_hunger: int = 0, rest_thirst: int = 0):
    conn = None
    try:
        conn = get_conn()
        c = conn.cursor()
        c.execute(
            "INSERT INTO catalogo(nome,peso,consumivel,bonus,tipo,arma_tipo,arma_bonus,muni_atual,muni_max,armas_compat,rest_hunger,rest_thirst) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s) "
            "ON CONFLICT (nome) DO UPDATE SET peso=%s, consumivel=%s, bonus=%s, tipo=%s, arma_tipo=%s, arma_bonus=%s, muni_atual=%s, muni_max=%s, armas_compat=%s, rest_hunger=%s, rest_thirst=%s",
            (nome, peso, consumivel, bonus, tipo, arma_tipo, arma_bonus, muni_atual, muni_max, armas_compat, rest_hunger, rest_thirst,
             peso, consumivel, bonus, tipo, arma_tipo, arma_bonus, muni_atual, muni_max, armas_compat, rest_hunger, rest_thirst)
        )
        conn.commit()
    finally:
        if conn:
            put_conn(conn)

def del_catalog_item(nome: str) -> bool:
    conn = None
    try:
        conn = get_conn()
        c = conn.cursor()
        c.execute("DELETE FROM catalogo WHERE LOWER(nome)=LOWER(%s)", (nome,))
        deleted = c.rowcount
        conn.commit()
        return deleted > 0
    finally:
        if conn:
            put_conn(conn)

def list_catalog():
    conn = None
    try:
        conn = get_conn()
        c = conn.cursor()
        c.execute("SELECT nome,peso,consumivel,bonus,tipo,arma_tipo,arma_bonus,muni_atual,muni_max,armas_compat FROM catalogo ORDER BY nome COLLATE \"C\"")
        data = c.fetchall()
        return data
    finally:
        if conn:
            put_conn(conn)
    
def add_weapon_to_inventory(uid, nome, peso, quantidade, municao_atual, municao_max):
    conn = None
    try:
        conn = get_conn()
        c = conn.cursor()
        c.execute("""
            INSERT INTO inventario (player_id, nome, peso, quantidade, municao_atual, municao_max)
            VALUES (%s, %s, %s, %s, %s, %s)
            ON CONFLICT (player_id, nome) DO UPDATE
            SET quantidade = inventario.quantidade + %s,
                peso = %s,
                municao_atual = %s,
                municao_max = %s
        """, (uid, nome, peso, quantidade, municao_atual, municao_max, quantidade, peso, municao_atual, municao_max))
        conn.commit()
    finally:
        if conn:
            put_conn(conn)

def update_weapon_ammo(uid, nome, nova_municao):
    conn = None
    try:
        conn = get_conn()
        c = conn.cursor()
        c.execute("""
            UPDATE inventario
            SET municao_atual = %s
            WHERE player_id = %s AND nome = %s
        """, (nova_municao, uid, nome))
        conn.commit()
    finally:
        if conn:
            put_conn(conn)

def is_consumivel_catalogo(nome: str):
    item = get_catalog_item(nome)
    return item and item.get("consumivel")

def remove_item(uid, item_nome):
    conn = None
    try:
        conn = get_conn()
        c = conn.cursor()
        c.execute("DELETE FROM inventario WHERE player_id=%s AND LOWER(nome)=LOWER(%s)", (uid, item_nome))
        conn.commit()
    finally:
        if conn:
            put_conn(conn)

def peso_total(player):
    return sum(i['peso'] * i.get('quantidade', 1) for i in player.get("inventario", []))

def penalidade(player):
    return peso_total(player) > player["peso_max"]

def penalidade_sobrecarga(player):
    excesso = peso_total(player) - player["peso_max"]
    if excesso <= 0:
        return 0
    if excesso <= 5:
        return -1
    elif excesso <= 10:
        return -2
    else:
        return -3

def anti_spam(user_id):
    now = time.time()
    if user_id in LAST_COMMAND and now - LAST_COMMAND[user_id] < COOLDOWN:
        return False
    LAST_COMMAND[user_id] = now
    return True

def parse_dice_notation(notation):
    if not isinstance(notation, str):
        return None
    match = re.match(r'(\d+)d(\d+)', notation.lower())
    if match:
        return int(match.group(1)), int(match.group(2))
    return None

def parse_roll_expr(expr):
    import re
    expr = expr.replace(" ", "")
    m = re.match(r"^(\d*)d(\d+)(\+(\d+))?$", expr)
    if not m:
        return None
    qtd = int(m.group(1)) if m.group(1) else 1
    lados = int(m.group(2))
    bonus = int(m.group(4)) if m.group(4) else 0
    if lados not in (4, 6, 8, 10, 12, 20) or qtd > 5 or bonus > 10:
        return None
    return qtd, lados, bonus

def roll_dados(qtd=1, lados=20):
    return [random.randint(1, lados) for _ in range(qtd)]

def resultado_roll(valor_total):
    if valor_total <= 4:
        return "Fracasso crítico"
    elif valor_total <= 9:
        return "Fracasso"
    elif valor_total <= 14:
        return "Normal"
    elif valor_total <= 19:
        return "Sucesso"
    else:
        return "Sucesso crítico"

def parse_float_br(s: str) -> float | None:
    s = s.strip().lower().replace("kg", "").strip()
    s = s.replace(",", ".")
    try:
        v = float(s)
        return v if v > 0 else None
    except:
        return None

def ensure_peso_max_by_forca(uid: int):
    conn = None
    try:
        conn = get_conn()
        c = conn.cursor()
        c.execute("SELECT valor FROM atributos WHERE player_id=%s AND nome='Força'", (uid,))
        row = c.fetchone()
        if row:
            valor_forca = max(1, min(6, int(row[0])))
            novo = PESO_MAX.get(valor_forca, 0)
            c.execute("UPDATE players SET peso_max=%s WHERE id=%s", (novo, uid))
            conn.commit()
    finally:
        if conn:
            put_conn(conn)

def add_coma_bonus(target_id: int, delta: int):
    conn = None
    try:
        conn = get_conn()
        c = conn.cursor()
        c.execute("INSERT INTO coma_bonus(target_id, bonus) VALUES(%s,0) ON CONFLICT (target_id) DO NOTHING", (target_id,))
        c.execute("UPDATE coma_bonus SET bonus = bonus + %s WHERE target_id=%s", (delta, target_id))
        conn.commit()
    finally:
        if conn:
            put_conn(conn)

def pop_coma_bonus(target_id: int) -> int:
    conn = None
    try:
        conn = get_conn()
        c = conn.cursor()
        c.execute("SELECT bonus FROM coma_bonus WHERE target_id=%s", (target_id,))
        row = c.fetchone()
        bonus = row[0] if row else 0
        c.execute("DELETE FROM coma_bonus WHERE target_id=%s", (target_id,))
        conn.commit()
        return bonus
    finally:
        if conn:
            put_conn(conn)

def registrar_teste_coma(uid: int) -> bool:
    hoje = datetime.now().date()
    conn = None
    try:
        conn = get_conn()
        c = conn.cursor()
        c.execute("SELECT ultima_data FROM coma_teste WHERE player_id=%s", (uid,))
        row = c.fetchone()
        if row and row[0] == hoje:
            return False
        c.execute("INSERT INTO coma_teste(player_id, ultima_data) VALUES(%s,%s) ON CONFLICT (player_id) DO UPDATE SET ultima_data=%s", (uid, hoje, hoje))
        conn.commit()
        return True
    finally:
        if conn:
            put_conn(conn)

def reset_coma_teste():
    while True:
        now = datetime.now()
        next_reset = now.replace(hour=6, minute=0, second=0, microsecond=0)
        if now >= next_reset:
            next_reset += timedelta(days=1)
        wait_seconds = (next_reset - now).total_seconds()
        time.sleep(wait_seconds)
        conn = None
        try:
            conn = get_conn()
            c = conn.cursor()
            c.execute("DELETE FROM coma_teste")
            conn.commit()
            logger.info("🧊 Resetei testes de coma!")
        finally:
            if conn:
                put_conn(conn)

def parse_nome_quantidade(args):
    if len(args) >= 2 and args[-2].lower() == 'x' and args[-1].isdigit():
        qtd = int(args[-1])
        nome = " ".join(args[:-2])
    elif len(args) >= 1 and (args[-1].startswith('x') and args[-1][1:].isdigit()):
        qtd = int(args[-1][1:])
        nome = " ".join(args[:-1])
    elif len(args) >= 1 and args[-1].isdigit():
        qtd = int(args[-1])
        nome = " ".join(args[:-1])
    else:
        qtd = 1
        nome = " ".join(args)
    return nome.strip(), qtd

def reset_diario_rerolls():
    while True:
        try:
            now = datetime.now()
            next_reset = now.replace(hour=6, minute=0, second=0, microsecond=0)
            if now >= next_reset:
                next_reset += timedelta(days=1)
            wait_seconds = (next_reset - now).total_seconds()
            time.sleep(wait_seconds)
            conn = None
            try:
                conn = get_conn()
                c = conn.cursor()
                c.execute("UPDATE players SET rerolls=3")
                conn.commit()
                logger.info("🔄 Rerolls diários resetados!")
            finally:
                if conn:
                    put_conn(conn)
        except Exception as e:
            logger.error(f"Erro no reset de rerolls: {e}")
            time.sleep(60)

def is_admin(uid: int) -> bool:
    return uid in ADMIN_IDS

def mention(user):
    if user.username:
        return f"@{user.username}"
    return user.first_name or "Jogador"

def cleanup_expired_transfers():
    while True:
        try:
            now = time.time()
            expired_keys = []
            for key, transfer in TRANSFER_PENDING.items():
                if now > transfer.get('expires', now):
                    expired_keys.append(key)
            
            for key in expired_keys:
                TRANSFER_PENDING.pop(key, None)
                
            time.sleep(300)
        except Exception as e:
            logger.error(f"Erro na limpeza de transferências: {e}")
            time.sleep(60)

def semana_atual():
    hoje = datetime.now()
    segunda = hoje - timedelta(days=hoje.weekday())
    return segunda.date()

def xp_por_caracteres(n):
    if n < 500:
        return 0
    elif n < 1000:
        return 10
    elif n < 1500:
        return 15
    elif n < 2000:
        return 20
    elif n <= 2500:
        return 25
    elif n <= 3000:
        return 30
    elif n <= 3500:
        return 35
    elif n <= 4096:
        return 40
    else:
        return 40

def ranking_semanal(context=None):
    semana = semana_atual()
    conn = None
    try:
        conn = get_conn()
        c = conn.cursor()
        c.execute("SELECT player_id, xp_total FROM xp_semana WHERE semana_inicio=%s ORDER BY xp_total DESC LIMIT 3", (semana,))
        top = c.fetchall()
        players = {pid: get_player(pid) for pid, _ in top}
        lines = ["🏆 Ranking Final da Semana:"]
        medals = ['🥇', '🥈', '🥉']
        for idx, (pid, xp) in enumerate(top):
            nome = players[pid]['nome'] if players.get(pid) else f"ID:{pid}"
            lines.append(f"{medals[idx]} <b>{nome}</b> – XP: {xp}")
        texto = "\n".join(lines)

        if context:
            for admin_id in ADMIN_IDS:
                try:
                    context.bot.send_message(admin_id, texto, parse_mode='HTML')
                except Exception as e:
                    logger.error(f"Falha ao enviar ranking para admin {admin_id}: {e}")

        c.execute("DELETE FROM xp_semana WHERE semana_inicio=%s", (semana,))
        conn.commit()
    finally:
        if conn:
            put_conn(conn)

def thread_reset_xp():
    while True:
        now = datetime.now()
        proxima = now.replace(hour=6, minute=0, second=0, microsecond=0)
        while proxima.weekday() != 0:
            proxima += timedelta(days=1)
        if now >= proxima:
            proxima += timedelta(days=7)
        wait = (proxima - now).total_seconds()
        time.sleep(wait)
        ranking_semanal()

# ================== COMANDOS ==================

async def start(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not anti_spam(update.effective_user.id):
        await update.message.reply_text("⏳ Ei! Espere um instante antes de usar outro comando.")
        return
    uid = update.effective_user.id
    nome = update.effective_user.first_name
    username = update.effective_user.username
    if not get_player(uid):
        create_player(uid, nome, username)
        register_username(uid, username, nome)
        update_player_field(uid, 'hp_max', 40)
        update_player_field(uid, 'sp_max', 40)
    await update.message.reply_text(
    f"\u200B\n 𐚁  𝗕𝗼𝗮𝘀 𝘃𝗶𝗻𝗱𝗮𝘀, {nome} ! \n\n"
    "Este bot gerencia seus Dados, Ficha, Inventário, Vida e Sanidade, além de diversos outros sistemas que você poderá explorar.\n\n"
    "Use o comando <b>/ficha</b> para visualizar sua ficha atual. "
    "Para editá-la, use o comando <b>/editarficha</b>.\n\n"
    "Outros comandos úteis: <b>/roll</b>, <b>/inventario</b>, <b>/dar</b>, <b>/abandonar</b>, <b>/dano</b>, <b>/cura</b>, <b>/terapia</b>.\n\n"
    " 𝗔𝗽𝗿𝗼𝘃𝗲𝗶𝘁𝗲!\n\u200B",
    parse_mode="HTML"
)

async def liberar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.message.reply_text("❌ Apenas administradores podem liberar jogadores.")
        return
    if not context.args:
        await update.message.reply_text("Uso: /liberar @jogador")
        return
    target_tag = context.args[0]
    target_id = username_to_id(target_tag)
    if not target_id:
        await update.message.reply_text("❌ Jogador não encontrado.")
        return
    liberar_usuario(target_id)
    await update.message.reply_text(f"✅ Jogador {target_tag} liberado para usar o bot.")

async def desliberar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.message.reply_text("❌ Apenas administradores podem desliberar jogadores.")
        return
    if not context.args:
        await update.message.reply_text("Uso: /desliberar @jogador")
        return
    target_tag = context.args[0]
    target_id = username_to_id(target_tag)
    if not target_id:
        await update.message.reply_text("❌ Jogador não encontrado.")
        return
    desliberar_usuario(target_id)
    await update.message.reply_text(f"❌ Jogador {target_tag} removido da lista de liberados.")

async def turno(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_liberado(update.effective_user.id):
        await acesso_negado(update)
        return
    if update.message.chat.type == 'private':
        await update.message.reply_text("Este comando só pode ser usado em grupos!")
        return

    uid = update.effective_user.id
    username = update.effective_user.username
    hoje = datetime.now().date()
    semana = semana_atual()

    texto = update.message.text or ""
    texto_limpo = re.sub(r'^/turno(?:@\w+)?', '', texto, flags=re.IGNORECASE).strip()
    caracteres = len(texto_limpo)

    conn = None
    try:
        conn = get_conn()
        c = conn.cursor()

        c.execute("SELECT 1 FROM turnos WHERE player_id=%s AND data=%s", (uid, hoje))
        if c.fetchone():
            await update.message.reply_text("Você já enviou seu turno hoje! Apenas 1 por dia é contabilizado.")
            return

        if not texto_limpo:
            await update.message.reply_text(
                "ℹ️ Para registrar um turno, use este comando seguido do seu texto.\n\n"
                "Exemplo:\n"
                "<code>/turno O personagem caminhou pela floresta, descrevendo as árvores geladas...</code>\n\n"
                "⚠️ O texto precisa ter no mínimo 499 caracteres para ser contabilizado.",
                parse_mode="HTML"
            )
            return

        if caracteres < 499:
            await update.message.reply_text(
                f"⚠️ Seu turno precisa ter pelo menos 499 caracteres! (Atualmente: {caracteres})\n"
                "Nada foi registrado. Envie novamente com mais conteúdo."
            )
            return

        mencoes = set(re.findall(r"@(\w+)", texto_limpo))
        if username:
            mencoes.discard(username.lower())
        mencoes = list(mencoes)
        if len(mencoes) > 5:
            mencoes = mencoes[:5]
            await update.message.reply_text("⚠️ Só é possível mencionar até 5 jogadores por turno. Apenas os 5 primeiros serão considerados.")
        mencoes_str = ",".join(mencoes) if mencoes else ""

        xp = xp_por_caracteres(caracteres)

        c.execute("SELECT data FROM turnos WHERE player_id=%s AND data >= %s ORDER BY data", (uid, semana))
        dias = [row[0] for row in c.fetchall()]
        streak_atual = 1
        if dias:
            prev = dias[-1]
            if (hoje - prev).days == 1:
                streak_atual = len(dias) + 1
            else:
                streak_atual = 1

        bonus_streak = 0
        if streak_atual == 3:
            bonus_streak = 5
        elif streak_atual == 5:
            bonus_streak = 10
        elif streak_atual == 7:
            bonus_streak = 20

        xp_dia = min(xp + bonus_streak, 25)

        c.execute(
            "INSERT INTO turnos (player_id, data, caracteres, mencoes) VALUES (%s, %s, %s, %s)",
            (uid, hoje, caracteres, mencoes_str)
        )
        c.execute(
            "INSERT INTO xp_semana (player_id, semana_inicio, xp_total, streak_atual) VALUES (%s, %s, %s, %s) "
            "ON CONFLICT (player_id, semana_inicio) DO UPDATE SET xp_total = xp_semana.xp_total + %s, streak_atual = %s",
            (uid, semana, xp_dia, streak_atual, xp_dia, streak_atual)
        )

        interacoes_bonificadas = set()
        for mencionado in mencoes:
            mencionado_id = username_to_id(f"@{mencionado}")
            if mencionado_id and mencionado_id != uid:
                c.execute("SELECT mencoes FROM turnos WHERE player_id=%s AND data=%s", (mencionado_id, hoje))
                row = c.fetchone()
                if row and row[0]:
                    mencoes_do_outra_pessoa = set(row[0].split(","))
                    if username and username.lower() in mencoes_do_outra_pessoa:
                        par = tuple(sorted([uid, mencionado_id]))
                        if par not in interacoes_bonificadas:
                            c.execute("UPDATE xp_semana SET xp_total = xp_total + 5 WHERE player_id=%s AND semana_inicio=%s", (uid, semana))
                            c.execute("UPDATE xp_semana SET xp_total = xp_total + 5 WHERE player_id=%s AND semana_inicio=%s", (mencionado_id, semana))
                            interacoes_bonificadas.add(par)
                            try:
                                await context.bot.send_message(uid, f"🎉 Você e @{mencionado} mencionaram um ao outro no turno de hoje! Ambos ganharam +5 XP de interação mútua.", parse_mode="HTML")
                                await context.bot.send_message(mencionado_id, f"🎉 Você e @{username} mencionaram um ao outro no turno de hoje! Ambos ganharam +5 XP de interação mútua.", parse_mode="HTML")
                            except Exception as e:
                                logger.warning(f"Falha ao enviar mensagem privada de bônus: {e}")

        conn.commit()

        msg = f"Turno registrado!\nCaracteres: {caracteres}\nXP ganho hoje: {xp}"
        if bonus_streak:
            msg += f"\nBônus de streak: +{bonus_streak} XP"
        msg += f"\nStreak atual: {streak_atual} dias"
        await update.message.reply_text(msg)
    finally:
        if conn:
            put_conn(conn)

async def ficha(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_liberado(update.effective_user.id):
        await acesso_negado(update)
        return
    if not anti_spam(update.effective_user.id):
        await update.message.reply_text("⏳ Ei! Espere um instante antes de usar outro comando.")
        return
    uid = update.effective_user.id
    register_username(uid, update.effective_user.username, update.effective_user.first_name)
    player = get_player(uid)
    if not player:
        await update.message.reply_text("Você precisa usar /start primeiro!")
        return
    text = "\u200B\n「  ཀ  𝗗𝗘𝗔𝗗𝗟𝗜𝗡𝗘, ficha.  」​\u200B\n\n ✦︎  𝗔𝘁𝗿𝗶𝗯𝘂𝘁𝗼𝘀  \n"
    for a in ATRIBUTOS_LISTA:
        val = player["atributos"].get(a, 0)
        text += f" — {a}﹕{val}\n"
    text += "\n ✦︎  𝗣𝗲𝗿𝗶𝗰𝗶𝗮𝘀  \n"
    for p in PERICIAS_LISTA:
        val = player["pericias"].get(p, 0)
        text += f" — {p}﹕{val}\n"
    text += f"\n 𖹭  𝗛𝗣  (Vida)  ▸  {player['hp']} / {player['hp_max']}\n 𖦹  𝗦𝗣  (Sanidade)  ▸  {player['sp']} / {player['sp_max']}\n"
    total_peso = peso_total(player)
    sobre = "  ⚠︎  Você está com <b>SOBRECARGA</b>!" if penalidade(player) else ""
    text += f"\n 𖠩  𝗣𝗲𝘀𝗼 𝗧𝗼𝘁𝗮𝗹 ﹕ {total_peso:.1f} / {player['peso_max']}{sobre}\n\n"
    penal = penalidade_sobrecarga(player)
    if penal:
        text += f"⚠︎ Penalidade ativa: {penal} em Força, Agilidade e Furtividade!\n"
    text += "<blockquote>Para editar Atributos e Perícias, utilize o comando /editarficha.</blockquote>\n<blockquote>Para gerenciar seu Inventário, utilize o comando /inventario.</blockquote>\n\u200B"
    await update.message.reply_text(text, parse_mode="HTML")

async def editarficha(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_liberado(update.effective_user.id):
        await acesso_negado(update)
        return
    if not anti_spam(update.effective_user.id):
        await update.message.reply_text("⏳ Ei! Espere um instante antes de usar outro comando.")
        return

    uid = update.effective_user.id
    player = get_player(uid)
    if not player:
        await update.message.reply_text("Use /start primeiro!")
        return

    EDIT_PENDING[uid] = True
    
    if uid in EDIT_TIMERS:
        EDIT_TIMERS[uid].cancel()
    
    def timeout_edit():
        EDIT_PENDING.pop(uid, None)
        EDIT_TIMERS.pop(uid, None)
        logger.info(f"Timeout de edição para usuário {uid}")
    
    EDIT_TIMERS[uid] = threading.Timer(300.0, timeout_edit)
    EDIT_TIMERS[uid].start()
    
    campos_ficha = ""
    for a in ATRIBUTOS_LISTA:
        campos_ficha += f"{a}: \n"
    for p in PERICIAS_LISTA:
        campos_ficha += f"{p}: \n"

    text = (
        "\u200B\nPara editar os pontos em sua ficha, responda em apenas uma mensagem todas as alterações que deseja realizar. Você pode mudar quantos Atributos e Perícias quiser de uma só vez! \n\n"
        " ⤷ <b>EXEMPLO</b>\n\n<blockquote>Força: 3\nImproviso: 2\nMedicina: 1</blockquote>\n\n"
        "TODOS os Atributos e Perícias, é só copiar, colar, preencher e enviar!\n\n"
        f"<pre>{campos_ficha}</pre>\n"
        " ⓘ <b>ATENÇÃO</b>\n\n<blockquote> ▸ Cada Atributo e Perícia deve conter, sem exceção, entre 1 e 6 pontos.</blockquote>\n"
        "<blockquote> ▸ A soma de todos o pontos de Atributos deve totalizar 20</blockquote>\n"
        "<blockquote> ▸ A soma de todos o pontos de Perícia deve totalizar 40.</blockquote>\n"
        "<blockquote> ▸ Você tem 5 minutos para enviar as alterações.</blockquote>\n\u200B"
    )
    await update.message.reply_text(text, parse_mode="HTML")

async def receber_edicao(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid not in EDIT_PENDING:
        return

    player = get_player(uid)
    if not player:
        await update.message.reply_text("Use /start primeiro!")
        return

    text = update.message.text
    EDIT_TEMP = player["atributos"].copy()
    EDIT_TEMP.update(player["pericias"])

    linhas = text.split("\n")
    for linha in linhas:
        if not linha.strip():
            continue
        try:
            key, val = linha.split(":")
            key = normalizar(key)
            val = int(val.strip())
        except:
            await update.message.reply_text(f"❌ Remova esta parte: ({linha}) e envie novamente.")
            return

        if key in ATRIBUTOS_NORMAL:
            key_real = ATRIBUTOS_NORMAL[key]
            if val < 1 or val > 6:
                await update.message.reply_text("❌ Formato inválido! Atributos devem estar entre 1 e 6.")
                return
            soma_atributos = sum(EDIT_TEMP.get(a, 0) for a in ATRIBUTOS_LISTA if a != key_real) + val
            if soma_atributos > MAX_ATRIBUTOS:
                await update.message.reply_text("❌ Total de pontos em atributos excede 20.")
                return
            EDIT_TEMP[key_real] = val

        elif key in PERICIAS_NORMAL:
            key_real = PERICIAS_NORMAL[key]
            if val < 1 or val > 6:
                await update.message.reply_text("❌ Formato inválido! Perícias devem estar entre 1 e 6.")
                return
            soma_pericias = sum(EDIT_TEMP.get(p, 0) for p in PERICIAS_LISTA if p != key_real) + val
            if soma_pericias > MAX_PERICIAS:
                await update.message.reply_text("❌ Total de pontos em perícias excede 40.")
                return
            EDIT_TEMP[key_real] = val

        else:
            await update.message.reply_text(f"❌ Campo não reconhecido: {key}")
            return

    player["atributos"] = {k: EDIT_TEMP[k] for k in ATRIBUTOS_LISTA}
    player["pericias"] = {k: EDIT_TEMP[k] for k in PERICIAS_LISTA}

    for atr in ATRIBUTOS_LISTA:
        update_atributo(uid, atr, player["atributos"][atr])
    for per in PERICIAS_LISTA:
        update_pericia(uid, per, player["pericias"][per])
    ensure_peso_max_by_forca(uid)

    vit = player["atributos"].get("Vitalidade", 0)
    eq = player["atributos"].get("Equilíbrio", 0)
    hp_max = vitalidade_para_hp(vit)
    sp_max = equilibrio_para_sp(eq)
    update_player_field(uid, "hp_max", hp_max)
    update_player_field(uid, "sp_max", sp_max)

    if player["hp"] == 0 or player["hp"] > hp_max:
        update_player_field(uid, "hp", hp_max)
    else:
        update_player_field(uid, "hp", min(player["hp"], hp_max))
    if player["sp"] == 0 or player["sp"] > sp_max:
        update_player_field(uid, "sp", sp_max)
    else:
        update_player_field(uid, "sp", min(player["sp"], sp_max))

    atualizar_necessidades_por_tempo(uid)

    await update.message.reply_text(" ✅ Ficha atualizada com sucesso!")
    
    EDIT_PENDING.pop(uid, None)
    if uid in EDIT_TIMERS:
        EDIT_TIMERS[uid].cancel()
        EDIT_TIMERS.pop(uid, None)
    
async def verficha(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not anti_spam(update.effective_user.id):
        await update.message.reply_text("⏳ Ei! Espere um instante antes de usar outro comando.")
        return
    
    uid = update.effective_user.id
    
    if not is_admin(uid):
        await update.message.reply_text("❌ Apenas administradores podem usar este comando.")
        return
    
    if len(context.args) < 1:
        await update.message.reply_text("Uso: /verficha @jogador")
        return
    
    user_tag = context.args[0]
    target_id = username_to_id(user_tag)
    if not target_id:
        await update.message.reply_text("❌ Jogador não encontrado. Peça para a pessoa usar /start pelo menos uma vez.")
        return
    
    player = get_player(target_id)
    if not player:
        await update.message.reply_text("❌ Jogador não encontrado no sistema.")
        return
    
    text = f"\u200B\n 「  ཀ  𝗗𝗘𝗔𝗗𝗟𝗜𝗡𝗘, ficha de {player['nome']}.  」​\u200B\n\n ✦︎  𝗔𝘁𝗿𝗶𝗯𝘂𝘁𝗼𝘀  \n"
    for a in ATRIBUTOS_LISTA:
        val = player["atributos"].get(a, 0)
        text += f" — {a}﹕{val}\n"
    text += "\n ✦︎  𝗣𝗲𝗿𝗶𝗰𝗶𝗮𝘀  \n"
    for p in PERICIAS_LISTA:
        val = player["pericias"].get(p, 0)
        text += f" — {p}﹕{val}\n"
    text += f"\n 𖹭  𝗛𝗣  (Vida)  ▸  {player['hp']} / {player['hp_max']}\n 𖦹  𝗦𝗣  (Sanidade)  ▸  {player['sp']} / {player['sp_max']}\n"
    total_peso = peso_total(player)
    sobre = "  ⚠︎  Jogador está com <b>SOBRECARGA</b>!" if penalidade(player) else ""
    text += f"\n 𖠩  𝗣𝗲𝘀𝗼 𝗧𝗼𝘁𝗮𝗹 ﹕ {total_peso:.1f} / {player['peso_max']}{sobre}\n"
    
    text += f"\n📊 <b>Info Admin:</b>\n"
    text += f" — ID: {player['id']}\n"
    text += f" — Username: @{player['username'] or 'N/A'}\n"
    text += f" — Rerolls: {player['rerolls']}/3\n\u200B"
    
    await update.message.reply_text(text, parse_mode="HTML")

async def inventario(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_liberado(update.effective_user.id):
        await acesso_negado(update)
        return
    if not anti_spam(update.effective_user.id):
        await update.message.reply_text("⏳ Ei! Espere um instante antes de usar outro comando.")
        return
    uid = update.effective_user.id
    register_username(uid, update.effective_user.username, update.effective_user.first_name)
    player = get_player(uid)
    if not player:
        await update.message.reply_text("Use /start primeiro!")
        return
    lines = [f"\u200B\n「 📦 」 Inventário de {player['nome']}\n"]
    if not player['inventario']:
        lines.append("  Vazio.")
    else:
        for i in sorted(player['inventario'], key=lambda x: normalizar(x['nome'])):
            linha = f"  — {i['nome']} x{i['quantidade']} ({i['peso']:.2f} kg cada)"
            if i.get('municao_max'):
                linha += f" [{i.get('municao_atual', 0)}/{i['municao_max']} balas]"
            lines.append(linha)
    total_peso = peso_total(player)
    lines.append(f"\n  𝗣𝗲𝘀𝗼 𝗧𝗼𝘁𝗮𝗹﹕{total_peso:.1f}/{player['peso_max']} kg\n\u200B")
    if penalidade(player):
        excesso = total_peso - player['peso_max']
        lines.append(f" ⚠︎ {excesso:.1f} kg de <b>SOBRECARGA</b>!")
    penal = penalidade_sobrecarga(player)
    if penal:
        lines.append(f"  ⚠︎ Penalidade ativa: {penal} em Força, Agilidade e Furtividade!\n")
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")

async def itens(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_liberado(update.effective_user.id):
        await acesso_negado(update)
        return
    if not anti_spam(update.effective_user.id):
        await update.message.reply_text("⏳ Ei! Espere um instante antes de usar outro comando.")
        return
    try:
        data = list_catalog()
    except Exception as e:
        await update.message.reply_text("Erro ao acessar o catálogo. Tente novamente ou peça para o admin reiniciar o bot.")
        return
    if not data:
        await update.message.reply_text("\u200B\n ☰  Catálogo\n Vazio.\n Use /additem Nome Peso para adicionar.\n\u200B")
        return
    lines = ["\u200B\n ☰  Catálogo de Itens\n\n"]
    for row in data:
        nome, peso, consumivel, bonus, tipo, arma_tipo, arma_bonus, muni_atual, muni_max, armas_compat = row
        if arma_tipo:
            info = f" ({arma_tipo})"
            if arma_tipo == 'range':
                info += f", {muni_atual}/{muni_max}"
            info += f" (+{arma_bonus})"
        elif consumivel:
            info = f" (consumível)"
            if bonus and bonus != '0':
                info += f" (+{bonus})"
            if tipo:
                info += f" [{tipo}]"
            if tipo == 'municao' and armas_compat:
                info += f" | Armas: {armas_compat}"
        else:
            info = ""
        lines.append(f" — {nome} ({peso:.2f} kg){info}")
    await update.message.reply_text("\n".join(lines))

async def additem(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not anti_spam(update.effective_user.id):
        await update.message.reply_text("⏳ Ei! Espere um instante antes de usar outro comando.")
        return
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.message.reply_text("❌ Apenas administradores podem usar este comando.")
        return
    if len(context.args) < 2:
        await update.message.reply_text("Uso: /additem NomeDoItem Peso")
        return
    nome = " ".join(context.args[:-1])
    peso_str = context.args[-1]
    peso = parse_float_br(peso_str)
    if not peso:
        await update.message.reply_text("❌ Peso inválido. Use algo como 2,5")
        return
    try:
        add_catalog_item(nome, peso)
        await update.message.reply_text(f"✅ Item '{nome}' adicionado ao catálogo com {peso:.2f} kg.")
    except Exception as e:
        await update.message.reply_text("Erro ao adicionar item ao catálogo. Tente novamente.")
    
async def addconsumivel(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not anti_spam(update.effective_user.id):
        await update.message.reply_text("⏳ Ei! Espere um instante antes de usar outro comando.")
        return
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.message.reply_text("❌ Apenas administradores podem usar este comando.")
        return
    if len(context.args) < 2:
        await update.message.reply_text("Uso: /addconsumivel NomeDoItem Peso [bonus_ou_dado] [armas_compat]")
        return

    args = context.args
    peso_idx = -1
    for i in range(len(args) -1, -1, -1):
        if parse_float_br(args[i]):
            peso_idx = i
            break
            
    if peso_idx == -1:
        await update.message.reply_text("❌ Peso inválido. Use um número como 0.5 ou 2,5.")
        return

    nome = " ".join(args[:peso_idx])
    peso = parse_float_br(args[peso_idx])
    
    bonus = '0'
    armas_compat = ''
    
    # Se houver argumentos após o peso
    if len(args) > peso_idx + 1:
        bonus = args[peso_idx + 1] # Pode ser "5" ou "1d6"
        if len(args) > peso_idx + 2:
            armas_compat = " ".join(args[peso_idx + 2:])

    conn = None
    try:
        conn = get_conn()
        c = conn.cursor()
        c.execute('''INSERT INTO pending_consumivel (user_id, nome, peso, bonus, armas_compat)
                     VALUES (%s, %s, %s, %s, %s)
                     ON CONFLICT (user_id) DO UPDATE SET nome=%s, peso=%s, bonus=%s, armas_compat=%s, created_at=NOW()''',
                  (uid, nome, peso, bonus, armas_compat, nome, peso, bonus, armas_compat))
        conn.commit()
    finally:
        if conn:
            put_conn(conn)
            await update.message.reply_text(
                "Esse item consumível é de cura, dano, munição, comida, bebida ou nenhum?\nResponda: cura/dano/municao/comida/bebida/nenhum"
            )

async def receber_tipo_consumivel(update: Update, context: ContextTypes.DEFAULT_TYPE, row=None):
    uid = update.effective_user.id
    if row is None:
        conn = get_conn()
        c = conn.cursor()
        c.execute("SELECT nome, peso, bonus, armas_compat FROM pending_consumivel WHERE user_id=%s", (uid,))
        row = c.fetchone()
        put_conn(conn)
        if not row:
            return
            
    nome, peso, bonus, armas_compat = row
    tipo = update.message.text.strip().lower()

    if tipo not in ("cura", "dano", "nenhum", "municao", "comida", "bebida"):
        await update.message.reply_text("Tipo inválido. Use: cura, dano, municao, comida, bebida ou nenhum.")
        return

    # Validações de bônus
    if tipo == "dano" and not parse_dice_notation(bonus) and bonus != '0':
        await update.message.reply_text(f"❌ Para consumíveis de dano, o bônus '{bonus}' deve ser um dado (ex: 1d6, 2d8).")
        return
    if tipo == "cura" and not bonus.isdigit():
        await update.message.reply_text(f"❌ Para consumíveis de cura, o bônus '{bonus}' deve ser um número inteiro.")
        return

    if tipo == "comida":
        await update.message.reply_text("Quantos pontos de fome esse item reduz? Envie o número.")
        context.user_data['pending_tipo_consumivel'] = ("comida", nome, peso, bonus, armas_compat)
        return
    if tipo == "bebida":
        await update.message.reply_text("Quantos pontos de sede esse item reduz? Envie o número.")
        context.user_data['pending_tipo_consumivel'] = ("bebida", nome, peso, bonus, armas_compat)
        return
        
    try:
        add_catalog_item(nome, peso, consumivel=True, bonus=bonus, tipo=tipo, armas_compat=armas_compat)
        conn = get_conn()
        c = conn.cursor()
        c.execute("DELETE FROM pending_consumivel WHERE user_id=%s", (uid,))
        conn.commit()
        put_conn(conn)
        await update.message.reply_text(f"✅ Consumível '{nome}' adicionado ao catálogo com {peso:.2f} kg. Bônus: {bonus}, Tipo: {tipo}.")
    except Exception as e:
        logger.error(f"Erro ao adicionar consumível: {e}")
        await update.message.reply_text("Erro ao adicionar consumível ao catálogo. Tente novamente.")

async def texto_handler(update: Update, context: ContextTypes.DEFAULT_TYPE):
    uid = update.effective_user.id
    if uid in EDIT_PENDING:
        await receber_edicao(update, context)
        return
    if 'pending_tipo_consumivel' in context.user_data:
        tipo, nome, peso, bonus, armas_compat = context.user_data['pending_tipo_consumivel']
        try:
            valor = int(update.message.text.strip())
        except Exception:
            await update.message.reply_text("Digite apenas o número.")
            return
        if tipo == "comida":
            add_catalog_item(nome, peso, consumivel=True, bonus=bonus, tipo=tipo, armas_compat=armas_compat, rest_hunger=valor)
            await update.message.reply_text(f"Consumível '{nome}' adicionado ao catálogo. Reduz {valor} de fome.")
        elif tipo == "bebida":
            add_catalog_item(nome, peso, consumivel=True, bonus=bonus, tipo=tipo, armas_compat=armas_compat, rest_thirst=valor)
            await update.message.reply_text(f"Consumível '{nome}' adicionado ao catálogo. Reduz {valor} de sede.")
        del context.user_data['pending_tipo_consumivel']
        conn = get_conn()
        c = conn.cursor()
        c.execute("DELETE FROM pending_consumivel WHERE user_id=%s", (uid,))
        conn.commit()
        put_conn(conn)
        return

    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT nome, peso, bonus, armas_compat FROM pending_consumivel WHERE user_id=%s", (uid,))
    row = c.fetchone()
    put_conn(conn)
    if row:
        await receber_tipo_consumivel(update, context, row=row)
        return

async def addarma(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not anti_spam(update.effective_user.id):
        await update.message.reply_text("⏳ Ei! Espere um instante antes de usar outro comando.")
        return
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.message.reply_text("❌ Apenas administradores podem usar este comando.")
        return
    if len(context.args) < 4:
        await update.message.reply_text("Uso: /addarma Nome Peso melee/range Bônus(ex: 1d6) [munição_atual/munição_max (só para range)]")
        return

    args = context.args
    
    try:
        # Lógica para encontrar os argumentos corretamente, mesmo com nomes compostos
        if '/' in args[-1] and args[-3].lower() in ['melee', 'range']: # Arma range com munição
            nome = " ".join(args[:-4])
            peso = parse_float_br(args[-4])
            arma_tipo = args[-3].lower()
            arma_bonus = args[-2].lower() # ex: "1d8"
            muni_atual, muni_max = map(int, args[-1].split('/'))
        elif args[-2].lower() in ['melee', 'range']: # Arma sem munição
            nome = " ".join(args[:-3])
            peso = parse_float_br(args[-3])
            arma_tipo = args[-2].lower()
            arma_bonus = args[-1].lower() # ex: "1d4"
            muni_atual, muni_max = 0, 0
        else:
            raise ValueError("Formato de comando inválido.")

        if peso is None:
            await update.message.reply_text("❌ Peso inválido. Use um número, ex: 3 ou 2,5")
            return
            
        if parse_dice_notation(arma_bonus) is None:
             await update.message.reply_text("❌ Formato do bônus inválido. Use o formato de dado, ex: 1d6, 2d4 etc.")
             return

    except Exception as e:
        logger.error(f"Erro no parsing de /addarma: {e}")
        await update.message.reply_text("Formato inválido. Uso: /addarma Nome Peso melee/range Bônus(dado) [muni/max]")
        return
        
    try:
        add_catalog_item(
            nome, peso, consumivel=False, bonus='0', tipo='', arma_tipo=arma_tipo,
            arma_bonus=arma_bonus, muni_atual=muni_atual, muni_max=muni_max
        )
        await update.message.reply_text(
            f"✅ Arma '{nome}' ({arma_tipo}) adicionada ao catálogo. Bônus: {arma_bonus}" + 
            (f", munição: {muni_atual}/{muni_max}" if arma_tipo == 'range' else "")
        )
    except Exception as e:
        logger.error(f"[ERRO ADDARMA] {e}")
        await update.message.reply_text(f"Erro ao adicionar arma ao catálogo. Detalhe: {e}")

async def delitem(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not anti_spam(update.effective_user.id):
        await update.message.reply_text("⏳ Ei! Espere um instante antes de usar outro comando.")
        return
    uid = update.effective_user.id
    if not is_admin(uid):
        await update.message.reply_text("❌ Apenas administradores podem usar este comando.")
        return
    if len(context.args) < 1:
        await update.message.reply_text("Uso: /delitem NomeDoItem")
        return
    nome = " ".join(context.args)
    ok = del_catalog_item(nome)
    if ok:
        await update.message.reply_text(f"🗑️ Item '{nome}' removido do catálogo.")
    else:
        await update.message.reply_text("❌ Item não encontrado no catálogo.")

# ========================= DAR =========================
async def dar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_liberado(update.effective_user.id):
        await acesso_negado(update)
        return
    if not anti_spam(update.effective_user.id):
        await update.message.reply_text("⏳ Ei! Espere um instante antes de usar outro comando.")
        return
    if len(context.args) < 2:
        await update.message.reply_text("Uso: /dar @jogador Nome do item xquantidade (opcional)")
        return

    uid_from = update.effective_user.id
    register_username(uid_from, update.effective_user.username, update.effective_user.first_name)
    user_tag = context.args[0]
    target_id = username_to_id(user_tag)
    nome, qtd = parse_nome_quantidade(context.args[1:])
    item_input = nome

    if not target_id:
        await update.message.reply_text("❌ Jogador não encontrado. Peça para a pessoa usar /start pelo menos uma vez.")
        return
    if qtd < 1:
        await update.message.reply_text("❌ Quantidade inválida.")
        return
    item_nome, item_peso, qtd_doador = buscar_item_inventario(uid_from, item_input)
    if item_nome:
        if qtd > qtd_doador:
            await update.message.reply_text(f"❌ Quantidade indisponível. Você tem {qtd_doador}x '{item_nome}'.")
            return
    else:
        if is_admin(uid_from):
            item_info = get_catalog_item(item_input)
            if not item_info:
                await update.message.reply_text(f"❌ Item '{item_input}' não encontrado no catálogo.")
                return
            item_nome = item_info["nome"]
            item_peso = item_info["peso"]
        else:
            await update.message.reply_text(f"❌ Você não possui '{item_input}' no seu inventário.")
            return
    target_before = get_player(target_id)
    total_depois_target = peso_total(target_before) + item_peso * qtd
    aviso_sobrecarga = ""
    if total_depois_target > target_before['peso_max']:
        excesso = total_depois_target - target_before['peso_max']
        aviso_sobrecarga = f"  ⚠️ Atenção! {target_before['nome']} ficará com sobrecarga de {excesso:.1f} kg."
    timestamp = int(time.time())
    transfer_key = f"{uid_from}_{timestamp}_{quote(item_nome)}"
    TRANSFER_PENDING[transfer_key] = {
        "item": item_nome,
        "qtd": qtd,
        "doador": uid_from,
        "alvo": target_id,
        "expires": timestamp + 300
    }
    keyboard = [
        [
            InlineKeyboardButton("✅ Confirmar", callback_data=f"confirm_dar_{transfer_key}"),
            InlineKeyboardButton("❌ Cancelar", callback_data=f"cancel_dar_{transfer_key}")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        f"{user_tag}, {update.effective_user.first_name} quer te dar {item_nome} x{qtd}.\n"
        f"{aviso_sobrecarga}\nAceita a transferência?",
        reply_markup=reply_markup
    )

# ========================= CALLBACK DAR =========================
async def transfer_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    await query.answer()
    user_id = query.from_user.id
    data = query.data

    if data.startswith("confirm_dar_"):
        transfer_key = data.replace("confirm_dar_", "")
        transfer = TRANSFER_PENDING.get(transfer_key)
        if not transfer:
            await query.edit_message_text("❌ Transferência não encontrada ou expirada.")
            return
        if transfer['alvo'] != user_id:
            await query.answer("Só quem vai receber pode confirmar!", show_alert=True)
            return
        if user_id not in (transfer['doador'], transfer['alvo']):
            await query.answer("Só quem está envolvido pode cancelar!", show_alert=True)
            return
        if time.time() > transfer['expires']:
            TRANSFER_PENDING.pop(transfer_key, None)
            await query.edit_message_text("❌ Transferência expirada.")
            return

        doador = transfer['doador']
        alvo = transfer['alvo']
        item = transfer['item']
        qtd = transfer['qtd']

        conn = get_conn()
        c = conn.cursor()
        try:
            c.execute(
                "SELECT quantidade, peso, municao_atual, municao_max FROM inventario WHERE player_id=%s AND LOWER(nome)=LOWER(%s)",
                (doador, item)
            )
            row = c.fetchone()

            municao_atual, municao_max = 0, 0
            peso_item = 0

            if row:
                qtd_doador, peso_item, municao_atual, municao_max = row
                nova_qtd_doador = qtd_doador - qtd
                if nova_qtd_doador <= 0:
                    c.execute(
                        "DELETE FROM inventario WHERE player_id=%s AND LOWER(nome)=LOWER(%s)",
                        (doador, item)
                    )
                else:
                    c.execute(
                        "UPDATE inventario SET quantidade=%s WHERE player_id=%s AND LOWER(nome)=LOWER(%s)",
                        (nova_qtd_doador, doador, item)
                    )
            else:
                if is_admin(doador):
                    item_info = get_catalog_item(item)
                    if not item_info:
                        conn.close()
                        await query.edit_message_text("❌ Item não encontrado no catálogo.")
                        TRANSFER_PENDING.pop(transfer_key, None)
                        return
                    peso_item = item_info["peso"]
                    municao_atual = item_info.get("muni_atual", 0)
                    municao_max = item_info.get("muni_max", 0)
                else:
                    conn.close()
                    await query.edit_message_text("❌ Doador não possui o item.")
                    TRANSFER_PENDING.pop(transfer_key, None)
                    return

            c.execute(
                "SELECT quantidade FROM inventario WHERE player_id=%s AND LOWER(nome)=LOWER(%s)",
                (alvo, item)
            )
            row_tgt = c.fetchone()
            item_info = get_catalog_item(item)

            if row_tgt:
                nova_qtd_tgt = row_tgt[0] + qtd
                c.execute(
                    "UPDATE inventario SET quantidade=%s, peso=%s, consumivel=%s, bonus=%s, tipo=%s, arma_tipo=%s, arma_bonus=%s, municao_atual=%s, municao_max=%s, armas_compat=%s WHERE player_id=%s AND LOWER(nome)=LOWER(%s)",
                    (
                        nova_qtd_tgt, item_info["peso"],
                        item_info.get("consumivel", False),
                        item_info.get("bonus", '0'),
                        item_info.get("tipo", ""),
                        item_info.get("arma_tipo", ""),
                        item_info.get("arma_bonus", '0'),
                        municao_atual,
                        municao_max,
                        item_info.get("armas_compat", ""),
                        alvo, item
                    )
                )
            else:
                c.execute(
                    "INSERT INTO inventario(player_id, nome, peso, quantidade, consumivel, bonus, tipo, arma_tipo, arma_bonus, municao_atual, municao_max, armas_compat) VALUES(%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s,%s)",
                    (
                        alvo, item_info["nome"], item_info["peso"], qtd,
                        item_info.get("consumivel", False),
                        item_info.get("bonus", '0'),
                        item_info.get("tipo", ""),
                        item_info.get("arma_tipo", ""),
                        item_info.get("arma_bonus", '0'),
                        municao_atual,
                        municao_max,
                        item_info.get("armas_compat", "")
                    )
                )

            conn.commit()
        except Exception as e:
            conn.rollback()
            conn.close()
            logger.error(f"Erro na transferência: {e}")
            await query.edit_message_text("❌ Ocorreu um erro ao transferir o item.")
            TRANSFER_PENDING.pop(transfer_key, None)
            return
        finally:
            conn.close()

        TRANSFER_PENDING.pop(transfer_key, None)

        giver_after = get_player(doador)
        target_after = get_player(alvo)
        total_giver = peso_total(giver_after)
        total_target = peso_total(target_after)
        excesso = max(0, total_target - target_after['peso_max'])
        aviso_sobrecarga = f"\n  ⚠️ {target_after['nome']} está com sobrecarga de {excesso:.1f} kg!" if excesso else ""

        await query.edit_message_text(
            f"✅ Transferência confirmada! {item} x{qtd} entregue.\n"
            f"📦 {giver_after['nome']}: {total_giver:.1f}/{giver_after['peso_max']} kg\n"
            f"📦 {target_after['nome']}: {total_target:.1f}/{target_after['peso_max']} kg"
            f"{aviso_sobrecarga}"
        )

    elif data.startswith("cancel_dar_"):
        transfer_key = data.replace("cancel_dar_", "")
        transfer = TRANSFER_PENDING.get(transfer_key)
        if not transfer:
            await query.edit_message_text("❌ Transferência não encontrada.")
            return
        if user_id not in (transfer['doador'], transfer['alvo']):
            return
        TRANSFER_PENDING.pop(transfer_key, None)
        await query.edit_message_text("❌ Transferência cancelada.")

# ========================= COMANDO ABANDONAR =========================
async def abandonar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_liberado(update.effective_user.id):
        await acesso_negado(update)
        return
    if not anti_spam(update.effective_user.id):
        await update.message.reply_text("⏳ Ei! Espere um instante antes de usar outro comando.")
        return
    if len(context.args) < 1:
        await update.message.reply_text("Uso: /abandonar Nome do item xquantidade (opcional)")
        return
    uid = update.effective_user.id
    nome, qtd = parse_nome_quantidade(context.args)
    item_nome, item_peso, qtd_inv = buscar_item_inventario(uid, nome)

    if not item_nome:
        await update.message.reply_text(f"❌ Você não possui '{nome}' no seu inventário.")
        return
    if qtd < 1 or qtd > qtd_inv:
        await update.message.reply_text(f"❌ Quantidade inválida. Você tem {qtd_inv} '{item_nome}'.")
        return
    keyboard = [[
        InlineKeyboardButton("✅ Confirmar", callback_data=f"confirm_abandonar_{uid}_{quote(item_nome)}_{qtd}"),
        InlineKeyboardButton("❌ Cancelar", callback_data=f"cancel_abandonar_{uid}")
    ]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        f"⚠️ Você está prestes a abandonar '{item_nome}' x{qtd}. Confirma?",
        reply_markup=reply_markup
    )
    
# ========================= CALLBACK ABANDONAR =========================
async def callback_abandonar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data

    if data.startswith("confirm_abandonar_"):
        parts = data.split("_", 4)
        if len(parts) < 5:
            await query.edit_message_text("❌ Dados inválidos.")
            return
        _, _, uid_str, item_nome, qtd = parts
        uid = int(uid_str)
        item_nome = unquote(item_nome)
        qtd = int(qtd)
        
        if query.from_user.id != uid:
            await query.answer("Só o dono pode confirmar!", show_alert=True)
            return

        conn = None
        try:
            conn = get_conn()
            c = conn.cursor()
            c.execute(
                "SELECT quantidade FROM inventario WHERE player_id=%s AND LOWER(nome)=LOWER(%s)",
                (uid, item_nome)
            )
            row = c.fetchone()
            if not row:
                await query.edit_message_text("❌ Item não encontrado no inventário.")
                return
            qtd_inv = row[0]
            if qtd >= qtd_inv:
                c.execute(
                    "DELETE FROM inventario WHERE player_id=%s AND LOWER(nome)=LOWER(%s)",
                    (uid, item_nome)
                )
            else:
                c.execute(
                    "UPDATE inventario SET quantidade=%s WHERE player_id=%s AND LOWER(nome)=LOWER(%s)",
                    (qtd_inv - qtd, uid, item_nome)
                )
            conn.commit()
        except Exception as e:
            conn.rollback()
            logger.error(f"Erro ao abandonar item: {e}")
            await query.edit_message_text("❌ Erro ao abandonar o item.")
            return
        finally:
            if conn:
                put_conn(conn)

        jogador = get_player(uid)
        total_peso = peso_total(jogador)

        await query.edit_message_text(
            f"✅ '{item_nome}' x{qtd} foi abandonado.\n"
            f"📦 Inventário agora: {total_peso:.1f}/{jogador['peso_max']} kg"
        )

    elif data.startswith("cancel_abandonar_"):
        try:
            uid = int(data.split("_")[-1])
        except ValueError:
            await query.edit_message_text("❌ Dados inválidos.")
            return

        if query.from_user.id != uid:
            await query.answer("Só o dono pode cancelar!", show_alert=True)
            return

        await query.answer()
        await query.edit_message_text("❌ Ação cancelada.")

    else:
        await query.answer("Callback inválido.", show_alert=True)

async def recarregar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_liberado(update.effective_user.id):
        await acesso_negado(update)
        return
    if not anti_spam(update.effective_user.id):
        await update.message.reply_text("⏳ Ei! Espere um instante antes de usar outro comando.")
        return
    if len(context.args) < 1:
        await update.message.reply_text("Uso: /recarregar NomeDaMunição : NomeDaArma xQuantidade")
        return
    uid = update.effective_user.id
    texto = " ".join(context.args)
    m = re.match(r"(.+?)\s*:\s*(.+?)(?:\s+x(\d+)|\s+(\d+))?$", texto)
    if not m:
        await update.message.reply_text("Use: /recarregar NomeDaMunição : NomeDaArma xQuantidade")
        return
    item_municao = m.group(1).strip()
    item_arma = m.group(2).strip()
    qtd_str = m.group(3) or m.group(4)
    qtd = int(qtd_str) if qtd_str else 1

    item_nome, _, qtd_inv = buscar_item_inventario(uid, item_municao)
    if not item_nome or qtd_inv < 1:
        await update.message.reply_text(f"❌ Você não possui '{item_municao}' no seu inventário.")
        return

    arma_nome, _, arma_qtd = buscar_item_inventario(uid, item_arma)
    if not arma_nome or arma_qtd < 1:
        await update.message.reply_text(f"❌ Você não possui '{item_arma}' no seu inventário.")
        return

    cat_mun = get_catalog_item(item_nome)
    cat_arma = get_catalog_item(arma_nome)
    if not cat_mun or not cat_mun.get("consumivel") or cat_mun.get("tipo") != "municao":
        await update.message.reply_text(f"❌ '{item_nome}' não é uma munição válida.")
        return
    if not cat_arma or cat_arma.get("arma_tipo") != "range":
        await update.message.reply_text(f"❌ '{arma_nome}' não é uma arma de fogo válida.")
        return

    armas_compat = [normalizar(a.strip()) for a in (cat_mun.get("armas_compat") or "").split(",")]
    if normalizar(arma_nome) not in armas_compat:
        await update.message.reply_text("❌ Essa munição não é compatível com essa arma.")
        return

    player = get_player(uid)
    arma_obj = None
    for i in player["inventario"]:
        if normalizar(i["nome"]) == normalizar(arma_nome):
            arma_obj = i
            break
    if not arma_obj:
        await update.message.reply_text("❌ Arma não encontrada no inventário.")
        return
    mun_atual = arma_obj.get("municao_atual", 0)
    mun_max = arma_obj.get("municao_max", 0)
    if mun_atual >= mun_max:
        await update.message.reply_text("❌ A arma já está totalmente carregada!")
        return

    recarregar_max = min(qtd, qtd_inv, mun_max - mun_atual)
    if recarregar_max < 1:
        await update.message.reply_text("❌ Não é possível recarregar essa quantidade (verifique munição e espaço).")
        return

    global LAST_RELOAD
    LAST_RELOAD[uid] = (item_nome, arma_nome, recarregar_max)

    keyboard = [
        [
            InlineKeyboardButton("✅ Confirmar", callback_data=f"confirm_recarregar_{uid}"),
            InlineKeyboardButton("❌ Cancelar", callback_data=f"cancel_recarregar_{uid}")
        ]
    ]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text(
        f"Você está prestes a recarregar <b>{arma_nome}</b> com <b>{item_nome}</b>.\n"
        f"Quantidade: <b>{recarregar_max}</b>\n"
        f"Estado atual: <b>{mun_atual}/{mun_max} balas</b>.\n"
        "Confirma?",
        reply_markup=reply_markup,
        parse_mode="HTML"
    )

async def callback_recarregar(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    data = query.data
    uid = query.from_user.id

    global LAST_RELOAD

    if data == f"confirm_recarregar_{uid}":
        reload_data = LAST_RELOAD.get(uid)
        if not reload_data or len(reload_data) != 3:
            await query.edit_message_text("❌ Dados de recarga não encontrados.")
            return
        municao, arma, qtd = reload_data

        conn = get_conn()
        c = conn.cursor()
        c.execute("SELECT quantidade FROM inventario WHERE player_id=%s AND LOWER(nome)=LOWER(%s)", (uid, municao))
        row = c.fetchone()
        if not row or row[0] < qtd:
            conn.close()
            await query.edit_message_text("❌ Munição insuficiente.")
            return
        nova = row[0] - qtd
        if nova <= 0:
            c.execute("DELETE FROM inventario WHERE player_id=%s AND LOWER(nome)=LOWER(%s)", (uid, municao))
        else:
            c.execute("UPDATE inventario SET quantidade=%s WHERE player_id=%s AND LOWER(nome)=LOWER(%s)", (nova, uid, municao))

        c.execute("SELECT municao_atual, municao_max FROM inventario WHERE player_id=%s AND LOWER(nome)=LOWER(%s)", (uid, arma))
        row = c.fetchone()
        if not row:
            conn.close()
            await query.edit_message_text("❌ Arma não encontrada no inventário.")
            return
        mun_atual, mun_max = row
        novo_mun = min(mun_max, mun_atual + qtd)
        c.execute("UPDATE inventario SET municao_atual=%s WHERE player_id=%s AND LOWER(nome)=LOWER(%s)", (novo_mun, uid, arma))
        conn.commit()
        conn.close()
        await query.edit_message_text(f"🔫 <b>{arma}</b> recarregada! [{mun_atual} → {novo_mun}/{mun_max}] balas.", parse_mode="HTML")
        LAST_RELOAD.pop(uid, None)
        return

    elif data == f"cancel_recarregar_{uid}":
        LAST_RELOAD.pop(uid, None)
        await query.edit_message_text("❌ Recarga cancelada.")
        return

async def consumir(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_liberado(update.effective_user.id):
        await acesso_negado(update)
        return
    if not anti_spam(update.effective_user.id):
        await update.message.reply_text("⏳ Ei! Espere um instante antes de usar outro comando.")
        return
    if len(context.args) < 1:
        await update.message.reply_text("Uso: /consumir NomeDoItem xQuantidade (opcional)")
        return
    uid = update.effective_user.id
    nome, qtd = parse_nome_quantidade(context.args)
    item_nome, item_peso, qtd_inv = buscar_item_inventario(uid, nome)
    if not item_nome:
        await update.message.reply_text("❌ Você não possui esse item.")
        return
    if qtd < 1 or qtd > qtd_inv:
        await update.message.reply_text(f"❌ Quantidade inválida. Você tem {qtd_inv} '{item_nome}'.")
        return
    cat_item = get_catalog_item(item_nome)
    if not cat_item or not cat_item.get("consumivel"):
        await update.message.reply_text(f"❌ '{item_nome}' não é um item consumível.")
        return
    efeito = cat_item.get("tipo")
    bonus = cat_item.get("bonus", '0')
    msg = f"🍴 Você consumiu '{item_nome}' x{qtd}."
    
    if efeito == "cura":
        msg += f"\n💚 Recupera {bonus} HP."
    elif efeito == "dano":
        msg += f"\n💥 Causa {bonus} de dano (use /dano para aplicar)."
    elif efeito == "municao":
        msg += "\n⚠️ Use /recarregar para aplicar essa munição."
    if efeito == "comida":
        rest = cat_item.get("rest_hunger", 0) * qtd
        update_necessidades(uid, fome_delta=-rest)
        registrar_consumo(uid, "comida")
        await checar_alerta_necessidades(uid, context.bot)
        msg += f"\n🍽️ Fome reduzida em {rest}."
    elif efeito == "bebida":
        rest = cat_item.get("rest_thirst", 0) * qtd
        update_necessidades(uid, sede_delta=-rest)
        registrar_consumo(uid, "bebida")
        await checar_alerta_necessidades(uid, context.bot)
        msg += f"\n💧 Sede reduzida em {rest}."
    elif efeito == "nenhum":
        msg += "\n(Nenhum efeito direto, apenas roleplay)."
        
    adjust_item_quantity(uid, item_nome, -qtd)
    await update.message.reply_text(msg)

async def dano(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_liberado(update.effective_user.id):
        await acesso_negado(update)
        return
    if not anti_spam(update.effective_user.id):
        await update.message.reply_text("⏳ Ei! Espere um instante antes de usar outro comando.")
        return
    uid = update.effective_user.id
    register_username(uid, update.effective_user.username, update.effective_user.first_name)
    if len(context.args) < 1:
        await update.message.reply_text("Uso: /dano hp|sp [@jogador] [pericia/arma/consumivel]")
        return
    tipo = context.args[0].lower()
    if tipo not in ("hp", "sp", "vida", "sanidade"):
        await update.message.reply_text("Tipo inválido! Use hp/vida ou sp/sanidade.")
        return
    alvo_id = uid
    alvo_tag = mention(update.effective_user)
    bonus_pericia = 0
    bonus_arma = 0
    bonus_arma_str = ""
    bonus_consumivel = 0
    bonus_consumivel_str = ""
    pericia_usada = None
    responder_em_si = True
    args = context.args[1:]
    
    if args and args[0].startswith('@'):
        alvo_tag = args[0]
        t = username_to_id(alvo_tag)
        if t:
            alvo_id = t
            responder_em_si = False
        args = args[1:]
        
    if args:
        extra = " ".join(args)
        item_nome, _, qtd_inv = buscar_item_inventario(uid, extra)
        item_obj = get_catalog_item(item_nome) if item_nome else None
        
        if item_obj:
            if item_obj['arma_tipo']:
                arma_bonus_notation = item_obj['arma_bonus']
                dice_params = parse_dice_notation(arma_bonus_notation)
                if dice_params:
                    bonus_arma_roll = roll_dados(dice_params[0], dice_params[1])
                    bonus_arma = sum(bonus_arma_roll)
                    bonus_arma_str = f" ({arma_bonus_notation}): {bonus_arma_roll} -> {bonus_arma}"
                
                if item_obj['arma_tipo'] == 'melee':
                    pericia_usada = 'Luta'
                    bonus_pericia = get_player(uid)['pericias'].get('Luta', 0)
                elif item_obj['arma_tipo'] == 'range':
                    conn = get_conn()
                    c = conn.cursor()
                    c.execute("SELECT municao_atual FROM inventario WHERE player_id=%s AND LOWER(nome)=LOWER(%s)", (uid, item_obj['nome']))
                    row = c.fetchone()
                    conn.close()
                    if not row or row[0] is None or row[0] <= 0:
                        await update.message.reply_text(f"❌ Você está sem munição na arma '{item_obj['nome']}'!")
                        return
                    update_weapon_ammo(uid, item_obj['nome'], row[0] - 1)
                    pericia_usada = 'Pontaria'
                    bonus_pericia = get_player(uid)['pericias'].get('Pontaria', 0)
                    
            elif item_obj['consumivel'] and item_obj['tipo'] == "dano":
                consumable_bonus_notation = item_obj['bonus']
                dice_params = parse_dice_notation(consumable_bonus_notation)
                if dice_params:
                    bonus_consumivel_roll = roll_dados(dice_params[0], dice_params[1])
                    bonus_consumivel = sum(bonus_consumivel_roll)
                    bonus_consumivel_str = f" ({consumable_bonus_notation}): {bonus_consumivel_roll} -> {bonus_consumivel}"
                    adjust_item_quantity(uid, item_nome, -1) # Consome o item
                else:
                    await update.message.reply_text("❌ Consumível de dano com formato de bônus inválido.")
                    return
            else:
                await update.message.reply_text("❌ Item não pode ser usado para dano.")
                return
        else:
            extra_norm = normalizar(extra)
            if extra_norm in ["forca", "luta", "pontaria"]:
                pericia_usada = ATRIBUTOS_NORMAL.get(extra_norm) or PERICIAS_NORMAL.get(extra_norm)
                bonus_pericia = get_player(uid)['atributos'].get(pericia_usada, 0) if extra_norm == "forca" else get_player(uid)['pericias'].get(pericia_usada, 0)
                
    if responder_em_si:
        texto_acao = f"{mention(update.effective_user)} causou dano em si."
    else:
        texto_acao = f"{mention(update.effective_user)} causou dano em {alvo_tag}"
        
    dado = random.randint(1, 6)
    total = dado + bonus_pericia + bonus_arma + bonus_consumivel
    
    msg = f"{texto_acao}\nRolagem: 1d6 → {dado}\n"
    if pericia_usada:
        msg += f"Bônus de {pericia_usada}: +{bonus_pericia}\n"
    if bonus_arma_str:
        msg += f"Bônus de arma{bonus_arma_str}\n"
    if bonus_consumivel_str:
        msg += f"Bônus de consumível{bonus_consumivel_str}\n"
    msg += f"Total: {total}\n"
    
    alvo_player = get_player(alvo_id)
    if tipo in ("hp", "vida"):
        before = alvo_player['hp']
        after = max(0, before - total)
        update_player_field(alvo_id, 'hp', after)
        msg += f"{alvo_player['nome']}: HP {before} → {after}"
        if after == 0:
            msg += "\n💀 Entrou em coma! Use /inconsciente."
    else:
        before = alvo_player['sp']
        after = max(0, before - total)
        update_player_field(alvo_id, 'sp', after)
        msg += f"{alvo_player['nome']}: SP {before} → {after}"
        if after == 0:
            trauma = random.choice(TRAUMAS)
            msg += f"\n😵 Trauma severo! {trauma}"
            
    await update.message.reply_text(msg)

async def cura(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_liberado(update.effective_user.id):
        await acesso_negado(update)
        return
    if not anti_spam(update.effective_user.id):
        await update.message.reply_text("⏳ Ei! Espere um instante antes de usar outro comando.")
        return
    uid = update.effective_user.id
    register_username(uid, update.effective_user.username, update.effective_user.first_name)
    if len(context.args) < 1:
        await update.message.reply_text("Uso: /cura [@jogador] NomeDoKitOuConsumivel")
        return
    args = context.args
    alvo_id = uid
    alvo_tag = mention(update.effective_user)
    responder_em_si = True
    if args[0].startswith('@'):
        alvo_tag = args[0]
        t = username_to_id(alvo_tag)
        if t:
            alvo_id = t
            responder_em_si = False
        args = args[1:]
    if not args:
        await update.message.reply_text("❌ Falta nome do kit ou consumível.")
        return
    kit_input = " ".join(args).strip()
    kit_nome, _, qtd_inv = buscar_item_inventario(uid, kit_input)
    if not kit_nome or qtd_inv < 1:
        await update.message.reply_text(f"❌ Você não possui '{kit_input}' no inventário.")
        return
    kit_obj = get_catalog_item(kit_nome)
    bonus_kit = 0
    bonus_med = get_player(uid)['pericias'].get('Medicina', 0)
    
    if kit_obj:
        if kit_obj['consumivel'] and kit_obj['tipo'] == "cura":
            try:
                bonus_kit = int(kit_obj['bonus'])
            except ValueError:
                await update.message.reply_text("❌ Item de cura com bônus inválido (não é um número).")
                return
        else:
            await update.message.reply_text("❌ Item inválido para cura.")
            return
    else:
        key = normalizar(kit_nome)
        bonus_kit = KIT_BONUS.get(key)
        if bonus_kit is None:
            await update.message.reply_text("❌ Kit inválido. Use: Kit Básico, Intermediário, Avançado ou um item de cura válido.")
            return

    adjust_item_quantity(uid, kit_nome, -1)

    dado = random.randint(1, 6)
    total = dado + bonus_kit + bonus_med
    alvo = get_player(alvo_id)
    before = alvo['hp']
    after = min(alvo['hp_max'], before + total)
    update_player_field(alvo_id, 'hp', after)
    
    if responder_em_si:
        texto_acao = f"{mention(update.effective_user)} aplicou cura em si mesmo"
    else:
        texto_acao = f"{mention(update.effective_user)} aplicou cura em {alvo_tag}"
        
    msg = (
        f"{texto_acao} com {kit_nome}.\n"
        f"Rolagem: 1d6 → {dado}\n"
        f"Bônus de Medicina: +{bonus_med}\n"
    )
    if bonus_kit:
        msg += f"Bônus de item: +{bonus_kit}\n"
    msg += f"Total: {total}\n"
    msg += f"{alvo['nome']}: HP {before} → {after}"
    await update.message.reply_text(msg)

async def status(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_liberado(update.effective_user.id):
        await acesso_negado(update)
        return
    if not anti_spam(update.effective_user.id):
        await update.message.reply_text("⏳ Ei! Espere um instante antes de usar outro comando.")
        return
    uid = update.effective_user.id

    if context.args and is_admin(uid):
        user_tag = context.args[0]
        target_id = username_to_id(user_tag)
        if not target_id:
            await update.message.reply_text("❌ Jogador não encontrado.")
            return
        atualizar_necessidades_por_tempo(target_id)
        player = get_player(target_id)
    else:
        atualizar_necessidades_por_tempo(uid)
        player = get_player(uid)

    if not player:
        await update.message.reply_text("Use /start primeiro!")
        return

    hp, hp_max = player.get("hp", 0), player.get("hp_max", 0)
    sp, sp_max = player.get("sp", 0), player.get("sp_max", 0)
    fome = player.get("fome", 0)
    sede = player.get("sede", 0)
    sono = player.get("sono", 0)
    traumas = player.get("traumas", "")
    text = f"📝 <b>Status de {player['nome']}</b>\n\n"
    text += f"❤️ Vida: {hp}/{hp_max}\n"
    text += f"🧠 Sanidade: {sp}/{sp_max}\n\n"
    resistencia = player["pericias"].get("Resistência", 1)
    max_horas = resistencia_horas_max(resistencia)
    horas_sem_comer, horas_sem_beber, horas_sem_dormir = get_horas_sem_recursos(player["id"])
    text += f"🍽️ Fome: {faixa_status(fome, 'fome')}"
    if horas_sem_comer is not None:
        text += f" | {horas_sem_comer:.1f}h sem comer (máx {max_horas}h)\n"
    else:
        text += "\n"
    text += f"💧 Sede: {faixa_status(sede, 'sede')}"
    if horas_sem_beber is not None:
        text += f" | {horas_sem_beber:.1f}h sem beber (máx {max_horas}h)\n"
    else:
        text += "\n"
    text += f"💤 Sono: {faixa_status(sono, 'sono')}"
    if horas_sem_dormir is not None:
        text += f" | {horas_sem_dormir:.1f}h sem dormir (máx {max_horas}h)\n"
    else:
        text += "\n"
    text += "\n"
    if traumas:
        text += f"<b>Traumas:</b>\n- {traumas.replace(';', '\\n- ')}\n"
    penal = penalidade_sobrecarga(player)
    if penal:
        text += f"\n<b>Penalidades:</b>\n- Sobrepeso: {penal} em Força, Agilidade\n"
    await update.message.reply_text(text, parse_mode="HTML")

async def terapia(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_liberado(update.effective_user.id):
        await acesso_negado(update)
        return
    if not anti_spam(update.effective_user.id):
        await update.message.reply_text("⏳ Ei! Espere um instante antes de usar outro comando.")
        return
    uid = update.effective_user.id
    register_username(uid, update.effective_user.username, update.effective_user.first_name)
    if len(context.args) < 1:
        await update.message.reply_text("Uso: /terapia @jogador")
        return
    alvo_tag = context.args[0]
    alvo_id = username_to_id(alvo_tag)
    if not alvo_id:
        await update.message.reply_text("❌ Jogador não encontrado. Peça para a pessoa usar /start.")
        return
    if alvo_id == uid:
        await update.message.reply_text("❌ Terapia só pode ser aplicada em outra pessoa.")
        return

    healer = get_player(uid)
    bonus_pers = healer['pericias'].get('Manipulação', 0)
    dado = random.randint(1, 6)
    total = dado + bonus_pers

    alvo = get_player(alvo_id)
    before = alvo['sp']
    after = min(alvo['sp_max'], before + total)
    update_player_field(alvo_id, 'sp', after)

    msg = (
        f"🎲 {mention(update.effective_user)} aplicou uma sessão de terapia em {alvo_tag}!\n"
        f"Rolagem: 1d6 → {dado}\n"
        f"Bônus: +{bonus_pers} (Manipulação)\n"
        f"Total: {total}\n\n"
        f"{alvo['nome']}: SP {before} → {after}"
    )
    await update.message.reply_text(msg)

async def inconsciente(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_liberado(update.effective_user.id):
        await acesso_negado(update)
        return
    if not anti_spam(update.effective_user.id):
        await update.message.reply_text("⏳ Ei! Espere um instante antes de usar outro comando.")
        return

    uid = update.effective_user.id
    register_username(uid, update.effective_user.username, update.effective_user.first_name)
    player = get_player(uid)
    if not player:
        await update.message.reply_text("Use /start primeiro!")
        return
    if player['hp'] > 0:
        await update.message.reply_text("❌ Você não está inconsciente (HP > 0).")
        return

    if not registrar_teste_coma(uid):
        await update.message.reply_text("⚠️ Você já fez um teste de inconsciente hoje. Só é permitido 1 por dia.")
        return

    resistencia = player['pericias'].get('Resistência', 0)
    dado = random.randint(1, 20)
    bonus_ajuda = pop_coma_bonus(uid)
    total = dado + resistencia + bonus_ajuda

    if total <= 5:
        status = "☠️ Morte súbita! O corpo não resistiu, e a escuridão se fechou."
    elif total <= 12:
        status = "💀 Continua inconsciente. O corpo permanece desacordado, lutando por cada respiração."
    elif total <= 19:
        update_player_field(uid, 'hp', 1)
        status = "🌅 Você desperta, fraco e atordoado. HP agora: 1."
    else:
        extra_hp = random.randint(2, 5)
        new_hp = min(player['hp_max'], extra_hp)
        update_player_field(uid, 'hp', new_hp)
        status = f"🌟 Sucesso crítico! Um milagre: você acorda com {new_hp} HP, mais forte que antes!"

    await update.message.reply_text(
        "\n".join([
            "🧊 **Teste de Inconsciente**",
            f"Rolagem: 1d20 → {dado}",
            f"Bônus de Resistência: +{resistencia}",
            f"Bônus de ajuda: +{bonus_ajuda}",
            f"Total final: {total}",
            f"Resultado: {status}",
        ])
    )

async def roll(update: Update, context: ContextTypes.DEFAULT_TYPE, consumir_reroll=False):
    if not is_liberado(update.effective_user.id):
        await acesso_negado(update)
        return
    if not anti_spam(update.effective_user.id) and not consumir_reroll:
        await update.message.reply_text("⏳ Espere um instante antes de usar outro comando.")
        return False

    uid = update.effective_user.id
    register_username(uid, update.effective_user.username, update.effective_user.first_name)
    player = get_player(uid)
    if not player or len(context.args) < 1:
        await update.message.reply_text("Uso: /roll nome_da_pericia_ou_atributo OU /roll d20+2")
        return False

    key = " ".join(context.args)
    key_norm = normalizar(key)

    if re.match(r"^(\d+)?d\d+([+-]\d+)?$", key.lower()):
        parsed = parse_roll_expr(key)
        if not parsed:
            await update.message.reply_text(
                "Rolagem inválida! Use /roll d4, /roll 2d6, /roll d20+2, máx 5 dados, máx bônus +10."
            )
            return False
        qtd, lados, bonus = parsed
        dados = [random.randint(1, lados) for _ in range(qtd)]
        total = sum(dados) + bonus
        await update.message.reply_text(
            f"🎲 /roll {key}\nRolagens: {dados} → {sum(dados)}\nBônus: +{bonus}\nTotal: {total}"
        )
        return True

    bonus = 0
    found = False
    real_key = key
    penal = 0
    if key_norm in ATRIBUTOS_NORMAL:
        real_key = ATRIBUTOS_NORMAL[key_norm]
        bonus += player['atributos'].get(real_key, 0)
        found = True
        if real_key in ("Força", "Agilidade"):
            penal = penalidade_sobrecarga(player)
            bonus += penal
    elif key_norm in PERICIAS_NORMAL:
        real_key = PERICIAS_NORMAL[key_norm]
        bonus += player['pericias'].get(real_key, 0)
        found = True
        if real_key in ("Furtividade", "Reflexo"):
            penal = penalidade_sobrecarga(player)
            bonus += penal
    else:
        await update.message.reply_text(
            "❌ Perícia/atributo não encontrado.\nVeja os nomes válidos em /ficha."
        )
        return False

    dados = roll_dados()
    total = sum(dados) + bonus
    res = resultado_roll(sum(dados))
    penal_msg = f" (Penalidade de sobrecarga: {penal})" if penal else ""
    await update.message.reply_text(
        f"🎲 /roll {real_key}\nRolagens: {dados} → {sum(dados)}\nBônus: +{bonus}{penal_msg}\nTotal: {total} → {res}"
    )
    return True

async def reroll(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_liberado(update.effective_user.id):
        await acesso_negado(update)
        return
    uid = update.effective_user.id
    player = get_player(uid)
    if not player:
        await update.message.reply_text("Use /start primeiro!")
        return

    if player['rerolls'] <= 0:
        await update.message.reply_text("❌ Você não tem rerolls disponíveis hoje!")
        return

    if len(context.args) < 1:
        await update.message.reply_text("Uso: /reroll nome_da_pericia_ou_atributo")
        return

    ok = await roll(update, context, consumir_reroll=True)

    if ok:
        novos_rerolls = player['rerolls'] - 1
        update_player_field(uid, 'rerolls', novos_rerolls)

        await update.message.reply_text(
            f"🔄 Reroll usado! Rerolls restantes: {novos_rerolls}"
        )

async def xp(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_liberado(update.effective_user.id):
        await acesso_negado(update)
        return
    if not anti_spam(update.effective_user.id):
        await update.message.reply_text("⏳ Ei! Espere um instante antes de usar outro comando.")
        return
    uid = update.effective_user.id
    semana = semana_atual()
    conn = get_conn()
    c = conn.cursor()
    c.execute("SELECT xp_total, streak_atual FROM xp_semana WHERE player_id=%s AND semana_inicio=%s", (uid, semana))
    row = c.fetchone()
    xp_total = row[0] if row else 0
    streak = row[1] if row else 0
    c.execute("SELECT data, caracteres, mencoes FROM turnos WHERE player_id=%s AND data >= %s ORDER BY data", (uid, semana))
    dias = c.fetchall()
    lines = [f"📊 <b>Seu XP semanal:</b> {xp_total} XP", f"Streak atual: {streak} dias"]
    for d in dias:
        data, chars, menc = d
        xp_chars = xp_por_caracteres(chars)
        lines.append(f"📅 {data.strftime('%d/%m')}: {xp_chars} XP ({chars} caracteres)" + (f" | Menções: {menc}" if menc else ""))
    await update.message.reply_text("\n".join(lines), parse_mode="HTML")

    keyboard = [[InlineKeyboardButton("Ver ranking semanal 🏆", callback_data="ver_ranking")]]
    reply_markup = InlineKeyboardMarkup(keyboard)
    await update.message.reply_text("Veja o ranking semanal:", reply_markup=reply_markup)

async def button_callback(update: Update, context: ContextTypes.DEFAULT_TYPE):
    query = update.callback_query
    if query.data == "ver_ranking":
        await ranking(update, context)
        await query.answer()

async def ranking(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_liberado(update.effective_user.id):
        await acesso_negado(update)
        return
    if not anti_spam(update.effective_user.id):
        await update.message.reply_text("⏳ Ei! Espere um instante antes de usar outro comando.")
        return
    semana = semana_atual()
    conn = get_conn()
    c = conn.cursor()

    c.execute("""
        SELECT player_id, xp_total, streak_atual
        FROM xp_semana
        WHERE semana_inicio=%s
        ORDER BY xp_total DESC
        LIMIT 10
    """, (semana,))
    top = c.fetchall()

    c.execute("""
        SELECT player_id, xp_total, streak_atual
        FROM xp_semana
        WHERE semana_inicio=%s
        ORDER BY xp_total DESC
    """, (semana,))
    ranking_full = c.fetchall()
    conn.close()

    players = {pid: get_player(pid) for pid, _, _ in ranking_full}

    uid = update.effective_user.id
    lines = ["🏆 <b>Ranking semanal (Top 10)</b>"]
    medals = ['🥇', '🥈', '🥉']

    for idx, (pid, xp, streak) in enumerate(top):
        nome = players[pid]['nome'] if players.get(pid) else f"ID:{pid}"
        medal = medals[idx] if idx < len(medals) else f"{idx+1}."
        highlight = " <b>(Você)</b>" if pid == uid else ""
        lines.append(f"{medal} <b>{nome}</b> — {xp} XP | 🔥 Streak: {streak}d{highlight}")

    if not top:
        lines.append("Ninguém tem XP ainda nesta semana!")

    if uid not in [pid for pid, _, _ in top]:
        for pos, (pid, xp, streak) in enumerate(ranking_full, start=1):
            if pid == uid:
                nome = players[pid]['nome'] if players.get(pid) else f"ID:{pid}"
                lines.append(
                    f"\n➡️ Sua posição: {pos}º — <b>{nome}</b> — {xp} XP | 🔥 Streak: {streak}d"
                )
                break

    text = "\n".join(lines)

    if update.message:
        await update.message.reply_text(text, parse_mode="HTML")
    elif update.callback_query:
        await update.callback_query.message.reply_text(text, parse_mode="HTML")

async def dormir(update: Update, context: ContextTypes.DEFAULT_TYPE):
    if not is_liberado(update.effective_user.id):
        await acesso_negado(update)
        return
    if not anti_spam(update.effective_user.id):
        await update.message.reply_text("⏳ Espere um instante antes de usar outro comando.")
        return
    if len(context.args) < 1:
        await update.message.reply_text("Uso: /dormir [horas]\nExemplo: /dormir 6")
        return
    try:
        horas = int(context.args[0])
        if horas < 1 or horas > 24:
            await update.message.reply_text("Informe um valor de horas entre 1 e 24.")
            return
    except Exception:
        await update.message.reply_text("Digite apenas o número de horas dormidas. Exemplo: /dormir 6")
        return

    uid = update.effective_user.id
    player = get_player(uid)
    if not player:
        await update.message.reply_text("Use /start primeiro!")
        return

    multiplicador = 12
    sono_antes = player.get("sono", 0)
    sono_recuperado = min(sono_antes, horas * multiplicador)
    sono_novo = max(0, sono_antes - sono_recuperado)

    hp_max = player.get("hp_max", 40)
    sp_max = player.get("sp_max", 40)
    hp_antes = player.get("hp", 0)
    sp_antes = player.get("sp", 0)

    sono_total = max(sono_antes, 1)
    proporcao = sono_recuperado / sono_total if sono_antes > 0 else 0

    rec_hp = int(hp_max * 0.2 * proporcao)
    rec_sp = int(sp_max * 0.2 * proporcao)

    hp_novo = min(hp_max, hp_antes + rec_hp)
    sp_novo = min(sp_max, sp_antes + rec_sp)

    update_necessidades(uid, sono_delta=-sono_recuperado, fome_delta=+horas*2, sede_delta=+horas*1)
    registrar_consumo(uid, "sono")
    update_player_field(uid, "hp", hp_novo)
    update_player_field(uid, "sp", sp_novo)

    msg = (
        f"💤 Você dormiu {horas}h."
        f"\n🛏️ Recuperou {sono_recuperado} de sono ({faixa_status(sono_novo, 'sono')})."
        f"\n❤️ HP recuperado: {rec_hp} ({hp_antes} → {hp_novo})"
        f"\n🧠 SP recuperado: {rec_sp} ({sp_antes} → {sp_novo})"
        f"\n🍽️ Fome aumentou em {horas*2}."
        f"\n💧 Sede aumentou em {horas*1}."
    )
    await update.message.reply_text(msg)
    await checar_alerta_necessidades(uid, context.bot)

async def checar_alerta_necessidades(uid, bot):
    player = get_player(uid)
    if not player:
        return
    alertas = []
    if player.get("fome", 0) >= 90:
        alertas.append("⚠️ Sua fome está em estado crítico! Consuma comida o quanto antes.")
    if player.get("sede", 0) >= 90:
        alertas.append("⚠️ Sua sede está em estado crítico! Beba algo o quanto antes.")
    if player.get("sono", 0) >= 90:
        alertas.append("⚠️ Seu sono está em estado crítico! Você precisa dormir urgentemente.")
    if alertas:
        try:
            await bot.send_message(uid, "\n".join(alertas))
        except Exception:
            pass

# ================== FLASK ==================
flask_app = Flask(__name__)

@flask_app.route("/")
def home():
    return "Bot online!"

def run_flask():
    flask_app.run(host="0.0.0.0", port=10000)

# ========== MAIN ==========
def main():
    global POOL
    try:
        POOL = psycopg2.pool.ThreadedConnectionPool(
            minconn=1,
            maxconn=10,
            dsn=DATABASE_URL,
            cursor_factory=psycopg2.extras.DictCursor
        )
        print("✅ Pool de conexões com o banco de dados iniciado com sucesso.")
    except Exception as e:
        logger.error(f"❌ Falha ao iniciar o pool de conexões: {e}")
        return

    init_db()
    threading.Thread(target=run_flask, daemon=True).start()
    threading.Thread(target=reset_diario_rerolls, daemon=True).start()
    threading.Thread(target=cleanup_expired_transfers, daemon=True).start()
    threading.Thread(target=thread_reset_xp, daemon=True).start()
    threading.Thread(target=reset_coma_teste, daemon=True).start()
    app = Application.builder().token(TOKEN).build()
    app.add_handler(CommandHandler("start", start))
    app.add_handler(CommandHandler("ficha", ficha))
    app.add_handler(CommandHandler("verficha", verficha))
    app.add_handler(CommandHandler("inventario", inventario))
    app.add_handler(CommandHandler("itens", itens))
    app.add_handler(CommandHandler("additem", additem))
    app.add_handler(CommandHandler("addarma", addarma))
    app.add_handler(CommandHandler("addconsumivel", addconsumivel))
    app.add_handler(CommandHandler("delitem", delitem))
    app.add_handler(CommandHandler("dar", dar))
    app.add_handler(CallbackQueryHandler(transfer_callback, pattern=r'^(confirm_dar_|cancel_dar_)'))
    app.add_handler(CommandHandler("abandonar", abandonar))
    app.add_handler(CallbackQueryHandler(callback_abandonar, pattern=r'^confirm_abandonar_|^cancel_abandonar_'))
    app.add_handler(CommandHandler("consumir", consumir))
    app.add_handler(CommandHandler("recarregar", recarregar))
    app.add_handler(CallbackQueryHandler(callback_recarregar, pattern=r'^(confirm_recarregar_|cancel_recarregar_)'))
    app.add_handler(CommandHandler("dano", dano))
    app.add_handler(CommandHandler("cura", cura))
    app.add_handler(CommandHandler("terapia", terapia))
    app.add_handler(CommandHandler("inconsciente", inconsciente))
    app.add_handler(CommandHandler("liberar", liberar))
    app.add_handler(CommandHandler("desliberar", desliberar))
    app.add_handler(CommandHandler("roll", roll))
    app.add_handler(CommandHandler("reroll", reroll))
    app.add_handler(CommandHandler("editarficha", editarficha))
    app.add_handler(CommandHandler("turno", turno))
    app.add_handler(CommandHandler("xp", xp))
    app.add_handler(CallbackQueryHandler(button_callback, pattern="^ver_ranking$"))
    app.add_handler(CommandHandler("ranking", ranking))
    app.add_handler(CommandHandler("status", status))
    app.add_handler(CommandHandler("dormir", dormir))
    app.add_handler(MessageHandler(filters.TEXT & (~filters.COMMAND), texto_handler))
    app.run_polling()

if __name__ == "__main__":
    main()
