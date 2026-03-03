# app.py
import os
import re
import sqlite3
import zipfile
import base64
import unicodedata
from io import BytesIO
from datetime import datetime, date
from urllib.parse import urlparse

import streamlit as st
from openpyxl import Workbook
import matplotlib.pyplot as plt

from dotenv import load_dotenv
from supabase import create_client


# ======================
# Config (Streamlit)
# ======================
st.set_page_config(
    page_title="Vistoria Concreto",
    layout="wide",
    initial_sidebar_state="expanded"
)

# ======================
# ENV + Supabase
# ======================
load_dotenv()

SUPABASE_URL = st.secrets.get("SUPABASE_URL", os.getenv("SUPABASE_URL"))
SUPABASE_KEY = st.secrets.get("SUPABASE_KEY", os.getenv("SUPABASE_KEY"))
SUPABASE_BUCKET = st.secrets.get("SUPABASE_BUCKET", os.getenv("SUPABASE_BUCKET"))

if not SUPABASE_URL or not SUPABASE_KEY or not SUPABASE_BUCKET:
    st.error("Faltam variáveis no .env (ou Secrets): SUPABASE_URL, SUPABASE_KEY, SUPABASE_BUCKET")
    st.stop()

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


# ======================
# Helpers gerais
# ======================
def now_iso():
    return datetime.now().isoformat(timespec="seconds")


def safe_float(x):
    try:
        s = str(x).strip().replace(",", ".")
        if s == "":
            return None
        return float(s)
    except Exception:
        return None


def safe_name(text: str) -> str:
    """
    SAFE para nomes de pasta/arquivo no Storage (sem acentos e caracteres inválidos).
    """
    text = (text or "").strip()
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"\s+", "_", text)
    text = re.sub(r"[^A-Za-z0-9_\-]", "", text)
    return (text[:80] or "X")


# ======================
# Supabase Storage helpers
# ======================
def guess_ext(filename: str) -> str:
    ext = os.path.splitext(filename or "")[1].lower()
    if ext in [".jpg", ".jpeg", ".png"]:
        return ext
    return ".jpg"


def content_type_from_ext(ext: str) -> str:
    return "image/png" if (ext or "").lower() == ".png" else "image/jpeg"


def upload_bytes_to_storage(object_path: str, data_bytes: bytes, content_type: str) -> str:
    """
    Envia bytes para o Supabase Storage e retorna a URL pública.
    """
    supabase.storage.from_(SUPABASE_BUCKET).upload(
        path=object_path,
        file=data_bytes,
        file_options={"content-type": content_type, "upsert": True},
    )
    return supabase.storage.from_(SUPABASE_BUCKET).get_public_url(object_path)


def storage_key_for_pathology(
    company_name: str,
    work_name: str,
    visit_choice: str,
    block_name: str,
    apt_number: str,
    pathology_type: str,
    pid_or_ts: str,
    ext: str
) -> str:
    return (
        f"patologias/"
        f"{safe_name(company_name)}/"
        f"{safe_name(work_name)}/"
        f"{safe_name(visit_choice)}/"
        f"{safe_name(pathology_type)}/"
        f"{safe_name(block_name)}_{safe_name(apt_number)}_{pid_or_ts}{ext}"
    )


def storage_key_for_facade(company_name: str, work_name: str, block_name: str, ts: str, ext: str) -> str:
    return (
        f"fachadas/"
        f"{safe_name(company_name)}/"
        f"{safe_name(work_name)}/"
        f"{safe_name(block_name)}/"
        f"FACHADA_{safe_name(block_name)}_{ts}{ext}"
    )


def storage_key_from_public_url(public_url: str) -> str | None:
    """
    Converte URL pública:
    https://xxx.supabase.co/storage/v1/object/public/BUCKET/pasta/arquivo.jpg
    -> pasta/arquivo.jpg
    """
    if not public_url or not isinstance(public_url, str):
        return None
    if not public_url.startswith("http"):
        return None
    try:
        u = urlparse(public_url)
        marker = f"/storage/v1/object/public/{SUPABASE_BUCKET}/"
        idx = u.path.find(marker)
        if idx == -1:
            return None
        return u.path[idx + len(marker):].lstrip("/")
    except Exception:
        return None


def is_url(s: str) -> bool:
    return isinstance(s, str) and s.startswith("http")


# ======================
# Topo (Logo + Título)
# ======================
def img_to_base64(path: str) -> str:
    with open(path, "rb") as f:
        return base64.b64encode(f.read()).decode("utf-8")


LOGO_FILE = "LOGOMARCA.png"
logo_b64 = img_to_base64(LOGO_FILE) if os.path.exists(LOGO_FILE) else ""

st.markdown(f"""
<style>
#MainMenu {{ visibility: hidden; }}
footer {{ visibility: hidden; }}

.block-container {{
    padding-top: 20px;
}}

.infra-topbar {{
    width: 100%;
    background: #FFFFFF;
    padding: 30px 0 20px 0;
    text-align: center;
}}

.infra-topbar img {{
    max-width: 150px;
    width: 80%;
    height: auto;
    object-fit: contain;
}}

.infra-title {{
    margin-top: 18px;
    font-size: 20px;
    font-weight: 800;
    letter-spacing: 1px;
    color: #444444;
}}

.infra-subtitle {{
    margin-top: 6px;
    font-size: 15px;
    font-weight: 600;
    color: #666666;
    letter-spacing: 0.5px;
}}
</style>

<div class="infra-topbar">
    {"<img src='data:image/png;base64," + logo_b64 + "'>" if logo_b64 else ""}
    <div class="infra-title">SISTEMA DE REGISTRO DE VISTORIA</div>
    <div class="infra-subtitle">PAREDE DE CONCRETO • CONTROLE TÉCNICO E DOCUMENTAÇÃO</div>
</div>
""", unsafe_allow_html=True)


# ======================
# Paths / DB (SQLite local)
# ======================
BASE_DIR = os.path.dirname(__file__)
DB_PATH = os.path.join(BASE_DIR, "vistoria.db")


# ======================
# Banco (SQLite)
# ======================
def get_conn():
    conn = sqlite3.connect(DB_PATH, check_same_thread=False)
    conn.execute("PRAGMA foreign_keys = ON;")
    return conn


def init_db():
    conn = get_conn()
    cur = conn.cursor()

    cur.execute("""
        CREATE TABLE IF NOT EXISTS companies (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            name TEXT NOT NULL UNIQUE
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS works (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            company_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            UNIQUE(company_id, name),
            FOREIGN KEY(company_id) REFERENCES companies(id) ON DELETE CASCADE
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS blocks (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            work_id INTEGER NOT NULL,
            name TEXT NOT NULL,
            UNIQUE(work_id, name),
            FOREIGN KEY(work_id) REFERENCES works(id) ON DELETE CASCADE
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS block_facades (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            block_id INTEGER NOT NULL UNIQUE,
            photo_path TEXT NOT NULL,  -- agora será URL
            created_at TEXT NOT NULL,
            FOREIGN KEY(block_id) REFERENCES blocks(id) ON DELETE CASCADE
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS apartments (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            block_id INTEGER NOT NULL,
            number TEXT NOT NULL,
            UNIQUE(block_id, number),
            FOREIGN KEY(block_id) REFERENCES blocks(id) ON DELETE CASCADE
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS visits (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            work_id INTEGER NOT NULL,
            visit_date TEXT NOT NULL,
            title TEXT NOT NULL DEFAULT '',
            created_at TEXT NOT NULL,
            UNIQUE(work_id, visit_date, title),
            FOREIGN KEY(work_id) REFERENCES works(id) ON DELETE CASCADE
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS inspections (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            visit_id INTEGER NOT NULL,
            apartment_id INTEGER NOT NULL,
            created_at TEXT NOT NULL,
            thickness1 REAL,
            thickness2 REAL,
            thickness3 REAL,
            thickness1_room TEXT,
            thickness2_room TEXT,
            thickness3_room TEXT,
            notes TEXT,
            UNIQUE(visit_id, apartment_id),
            FOREIGN KEY(visit_id) REFERENCES visits(id) ON DELETE CASCADE,
            FOREIGN KEY(apartment_id) REFERENCES apartments(id) ON DELETE CASCADE
        )
    """)

    cur.execute("""
        CREATE TABLE IF NOT EXISTS pathology_photos (
            id INTEGER PRIMARY KEY AUTOINCREMENT,
            inspection_id INTEGER NOT NULL,
            pathology_type TEXT NOT NULL,
            comment TEXT,
            photo_path TEXT NOT NULL, -- agora será URL
            created_at TEXT NOT NULL,
            FOREIGN KEY(inspection_id) REFERENCES inspections(id) ON DELETE CASCADE
        )
    """)

    conn.commit()
    conn.close()


def fetch_all(query, params=()):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(query, params)
    rows = cur.fetchall()
    conn.close()
    return rows


def fetch_one(query, params=()):
    rows = fetch_all(query, params)
    return rows[0] if rows else None


# ======================
# CRUD Empresas/Obras
# ======================
def add_company(name: str):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("INSERT INTO companies (name) VALUES (?)", (name,))
    conn.commit()
    conn.close()


def list_companies():
    return fetch_all("SELECT id, name FROM companies ORDER BY name ASC")


def add_work(company_id: int, work_name: str):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("INSERT INTO works (company_id, name) VALUES (?, ?)", (company_id, work_name))
    conn.commit()
    conn.close()


def list_works(company_id: int):
    return fetch_all("SELECT id, name FROM works WHERE company_id=? ORDER BY name ASC", (company_id,))


# ======================
# CRUD Blocos/Aptos
# ======================
def add_block(work_id: int, block_name: str):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("INSERT INTO blocks (work_id, name) VALUES (?, ?)", (work_id, block_name))
    conn.commit()
    conn.close()


def list_blocks(work_id: int):
    return fetch_all("SELECT id, name FROM blocks WHERE work_id=? ORDER BY name ASC", (work_id,))


def add_apartment(block_id: int, number: str):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("INSERT INTO apartments (block_id, number) VALUES (?, ?)", (block_id, number))
    conn.commit()
    conn.close()


def list_apartments(block_id: int):
    return fetch_all("SELECT id, number FROM apartments WHERE block_id=? ORDER BY number ASC", (block_id,))


# ======================
# Fachadas (por bloco) - URL
# ======================
def get_block_facade(block_id: int):
    return fetch_one("""
        SELECT photo_path, created_at
        FROM block_facades
        WHERE block_id=?
        LIMIT 1
    """, (block_id,))


def upsert_block_facade(block_id: int, photo_url: str):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO block_facades (block_id, photo_path, created_at)
        VALUES (?, ?, ?)
        ON CONFLICT(block_id) DO UPDATE SET
            photo_path=excluded.photo_path,
            created_at=excluded.created_at
    """, (block_id, photo_url, now_iso()))
    conn.commit()
    conn.close()


# ======================
# Visits (Vistoria por Data)
# ======================
def add_visit(work_id: int, visit_date_str: str, title: str):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO visits (work_id, visit_date, title, created_at) VALUES (?, ?, ?, ?)",
        (work_id, visit_date_str, (title or "").strip(), now_iso())
    )
    conn.commit()
    conn.close()


def list_visits(work_id: int):
    return fetch_all("""
        SELECT id, visit_date, title
        FROM visits
        WHERE work_id=?
        ORDER BY visit_date DESC, id DESC
    """, (work_id,))


# ======================
# Inspections (por visita e apartamento)
# ======================
def get_or_create_inspection(visit_id: int, apartment_id: int):
    row = fetch_one("""
        SELECT id, created_at,
               thickness1, thickness2, thickness3,
               thickness1_room, thickness2_room, thickness3_room,
               notes
        FROM inspections
        WHERE visit_id=? AND apartment_id=?
        LIMIT 1
    """, (visit_id, apartment_id))

    if row:
        return row

    conn = get_conn()
    cur = conn.cursor()
    cur.execute(
        "INSERT INTO inspections (visit_id, apartment_id, created_at, notes) VALUES (?, ?, ?, ?)",
        (visit_id, apartment_id, now_iso(), "")
    )
    conn.commit()
    new_id = cur.lastrowid
    conn.close()

    return (new_id, now_iso(), None, None, None, "", "", "", "")


def update_inspection(inspection_id: int, n1, n2, n3, r1, r2, r3, notes: str):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        UPDATE inspections
        SET thickness1=?, thickness2=?, thickness3=?,
            thickness1_room=?, thickness2_room=?, thickness3_room=?,
            notes=?
        WHERE id=?
    """, (n1, n2, n3, r1, r2, r3, notes, inspection_id))
    conn.commit()
    conn.close()


# ======================
# Patologias (URL)
# ======================
def add_pathology(inspection_id: int, pathology_type: str, comment: str, photo_url: str):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        INSERT INTO pathology_photos
        (inspection_id, pathology_type, comment, photo_path, created_at)
        VALUES (?, ?, ?, ?, ?)
    """, (inspection_id, pathology_type, comment, photo_url, now_iso()))
    conn.commit()
    conn.close()


def list_pathologies(inspection_id: int):
    return fetch_all("""
        SELECT id, pathology_type, comment, photo_path, created_at
        FROM pathology_photos
        WHERE inspection_id=?
        ORDER BY id DESC
    """, (inspection_id,))


# ======================
# Exportações
# ======================
def build_measures_xlsx(visit_id: int) -> bytes:
    data = fetch_all("""
        SELECT
            b.name AS bloco,
            a.number AS apartamento,
            i.thickness1_room, i.thickness1,
            i.thickness2_room, i.thickness2,
            i.thickness3_room, i.thickness3
        FROM inspections i
        JOIN apartments a ON a.id = i.apartment_id
        JOIN blocks b ON b.id = a.block_id
        WHERE i.visit_id = ?
        ORDER BY b.name ASC, a.number ASC, i.id ASC
    """, (visit_id,))

    wb = Workbook()
    ws = wb.active
    ws.title = "Espessuras"
    ws.append(["Bloco", "Apartamento", "Local", "Espessura (mm)"])

    for bloco, apto, r1, t1, r2, t2, r3, t3 in data:
        if t1 is not None:
            ws.append([bloco, apto, r1 or "", float(t1)])
        if t2 is not None:
            ws.append([bloco, apto, r2 or "", float(t2)])
        if t3 is not None:
            ws.append([bloco, apto, r3 or "", float(t3)])

    bio = BytesIO()
    wb.save(bio)
    return bio.getvalue()


def build_photos_zip(visit_id: int) -> bytes:
    """
    ZIP puxando do Supabase Storage quando photo_path for URL pública.
    Se for caminho local antigo, tenta ler do disco (compatibilidade).
    """
    conn = get_conn()
    cur = conn.cursor()

    # Patologias
    cur.execute("""
        SELECT
            p.id,
            p.pathology_type,
            p.photo_path,
            b.name AS bloco,
            a.number AS apto
        FROM pathology_photos p
        JOIN inspections i ON i.id = p.inspection_id
        JOIN apartments a ON a.id = i.apartment_id
        JOIN blocks b ON b.id = a.block_id
        WHERE i.visit_id = ?
        ORDER BY p.id ASC
    """, (visit_id,))
    pathology_rows = cur.fetchall()

    # Fachadas
    facade_rows = []
    try:
        cur.execute("""
            SELECT DISTINCT
                b.name AS bloco,
                f.photo_path AS facade_path,
                f.created_at
            FROM inspections i
            JOIN apartments a ON a.id = i.apartment_id
            JOIN blocks b ON b.id = a.block_id
            JOIN block_facades f ON f.block_id = b.id
            WHERE i.visit_id = ?
        """, (visit_id,))
        facade_rows = cur.fetchall()
    except Exception:
        facade_rows = []

    conn.close()

    bio = BytesIO()
    with zipfile.ZipFile(bio, mode="w", compression=zipfile.ZIP_DEFLATED) as z:

        # ===== Patologias =====
        if not pathology_rows:
            z.writestr("LEIA-ME.txt", "Nenhuma foto/patologia cadastrada nesta vistoria (data).")
        else:
            for pid, ptype, photo_path, bloco, apto in pathology_rows:
                folder = safe_name(ptype)
                bloco_safe = safe_name(str(bloco))
                apto_safe = safe_name(str(apto))

                ext = os.path.splitext(photo_path or "")[1].lower()
                if ext not in [".jpg", ".jpeg", ".png"]:
                    ext = ".jpg"

                filename = f"{bloco_safe}_{apto_safe}_{pid}{ext}"
                arcname = f"Patologias/{folder}/{filename}"

                try:
                    if is_url(photo_path):
                        object_key = storage_key_from_public_url(photo_path)
                        if not object_key:
                            z.writestr(f"Patologias/{folder}/URL_INVALIDA_{pid}.txt", f"URL: {photo_path}")
                        else:
                            file_bytes = supabase.storage.from_(SUPABASE_BUCKET).download(object_key)
                            z.writestr(arcname, file_bytes)
                    else:
                        # compatibilidade com caminho local antigo
                        if photo_path and os.path.exists(photo_path):
                            z.write(photo_path, arcname=arcname)
                        else:
                            z.writestr(
                                f"Patologias/{folder}/FOTO_NAO_ENCONTRADA_{bloco_safe}_{apto_safe}_{pid}.txt",
                                f"Arquivo não encontrado: {photo_path}"
                            )
                except Exception as e:
                    z.writestr(
                        f"Patologias/{folder}/FALHA_DOWNLOAD_{pid}.txt",
                        f"Path/URL: {photo_path}\nErro: {e}"
                    )

        # ===== Fachadas =====
        if facade_rows:
            for bloco, facade_path, created_at in facade_rows:
                bloco_safe = safe_name(str(bloco))

                ext = os.path.splitext(facade_path or "")[1].lower()
                if ext not in [".jpg", ".jpeg", ".png"]:
                    ext = ".jpg"

                arcname = f"Fachadas/FACHADA_{bloco_safe}{ext}"

                try:
                    if is_url(facade_path):
                        object_key = storage_key_from_public_url(facade_path)
                        if not object_key:
                            z.writestr(f"Fachadas/URL_INVALIDA_{bloco_safe}.txt", f"URL: {facade_path}")
                        else:
                            file_bytes = supabase.storage.from_(SUPABASE_BUCKET).download(object_key)
                            z.writestr(arcname, file_bytes)
                    else:
                        if facade_path and os.path.exists(facade_path):
                            z.write(facade_path, arcname=arcname)
                        else:
                            z.writestr(
                                f"Fachadas/FACHADA_NAO_ENCONTRADA_{bloco_safe}.txt",
                                f"Arquivo não encontrado: {facade_path}"
                            )
                except Exception as e:
                    z.writestr(
                        f"Fachadas/FALHA_DOWNLOAD_{bloco_safe}.txt",
                        f"Path/URL: {facade_path}\nErro: {e}"
                    )

    return bio.getvalue()


def get_pathology_stats(visit_id: int):
    conn = get_conn()
    cur = conn.cursor()
    cur.execute("""
        SELECT
            p.pathology_type,
            COUNT(*) AS total
        FROM pathology_photos p
        JOIN inspections i ON i.id = p.inspection_id
        WHERE i.visit_id = ?
        GROUP BY p.pathology_type
        ORDER BY total DESC
    """, (visit_id,))
    rows = cur.fetchall()
    conn.close()
    return rows


# Inicializa DB
init_db()


# ======================
# Menu
# ======================
menu = st.sidebar.radio("Menu", ["Vistoria", "Cadastro", "Exportações"], index=0)


# ======================
# Vistoria
# ======================
if menu == "Vistoria":
    st.header("VISTORIA PAREDE DE CONCRETO")

    # Empresa
    companies = list_companies()
    company_map = {name: cid for cid, name in companies}
    company_name = st.selectbox("Empresa", ["(selecionar)"] + list(company_map.keys()), key="vis_company")
    company_id = company_map.get(company_name)

    if not company_id:
        st.info("Selecione uma empresa (cadastre em Cadastro → Empresas).")
        st.stop()

    # Obra
    works = list_works(company_id)
    work_map = {name: wid for wid, name in works}
    work_name = st.selectbox("Obra", ["(selecionar)"] + list(work_map.keys()), key="vis_work")
    work_id = work_map.get(work_name)

    if not work_id:
        st.info("Selecione uma obra (cadastre em Cadastro → Obras).")
        st.stop()

    st.divider()

    # Vistoria por data
    st.subheader("VISTORIA (DATA)")
    visits = list_visits(work_id)

    visit_labels = []
    visit_map = {}
    for vid, vdate, vtitle in visits:
        label = f"{vdate}" if not vtitle else f"{vdate} - {vtitle}"
        visit_labels.append(label)
        visit_map[label] = vid

    visit_choice = st.selectbox(
        "Selecione a vistoria (data)",
        ["(nova vistoria)"] + visit_labels,
        key="visit_choice"
    )

    if visit_choice == "(nova vistoria)":
        new_date = st.date_input("Data da vistoria", value=date.today(), key="new_visit_date")
        new_title = st.text_input("Título (opcional)", placeholder="Ex.: Vistoria 01 / Torre 2", key="new_visit_title")

        if st.button("Criar vistoria por data"):
            try:
                add_visit(work_id, str(new_date), new_title)
                st.success("Vistoria criada!")
                st.rerun()
            except sqlite3.IntegrityError:
                st.warning("Já existe uma vistoria com essa data e esse título nesta obra.")
        st.stop()

    visit_id = visit_map[visit_choice]
    st.success(f"Selecionado: {company_name} • {work_name} • {visit_choice}")

    st.divider()

    # Bloco
    blocks = list_blocks(work_id)
    block_map = {name: bid for bid, name in blocks}
    block_choice = st.selectbox("Bloco", ["(selecionar)", "+ Novo bloco"] + list(block_map.keys()), key="vis_block")

    if block_choice == "+ Novo bloco":
        nb = st.text_input("Nome do novo bloco", placeholder="Ex.: Bloco A", key="new_block")
        if st.button("Criar bloco"):
            bname = (nb or "").strip()
            if not bname:
                st.warning("Digite o nome do bloco.")
            else:
                try:
                    add_block(work_id, bname)
                    st.success("Bloco criado!")
                    st.rerun()
                except sqlite3.IntegrityError:
                    st.warning("Esse bloco já existe.")
        st.stop()

    if block_choice == "(selecionar)":
        st.info("Selecione um bloco ou crie um novo.")
        st.stop()

    block_id = block_map[block_choice]
    block_name = block_choice

    # ======================
    # Fachada do Bloco (Storage)
    # ======================
    st.subheader("Fachada do Bloco")

    facade_row = get_block_facade(block_id)
    if facade_row:
        facade_url, facade_created = facade_row
        st.caption(f"Última foto cadastrada: {facade_created}")
        st.image(facade_url, width=520)

    facade_file = st.file_uploader(
        "Enviar/Atualizar foto da fachada do bloco",
        type=["jpg", "jpeg", "png"],
        accept_multiple_files=False,
        key=f"facade_{block_id}"
    )

    if st.button("Salvar foto da fachada", key=f"save_facade_{block_id}"):
        if not facade_file:
            st.error("Envie uma foto para salvar.")
        else:
            img_bytes = facade_file.getbuffer().tobytes()
            ext = guess_ext(facade_file.name)
            ctype = content_type_from_ext(ext)
            ts = str(int(datetime.now().timestamp() * 1000))

            object_path = storage_key_for_facade(company_name, work_name, block_name, ts, ext)
            public_url = upload_bytes_to_storage(object_path, img_bytes, ctype)

            upsert_block_facade(block_id, public_url)

            st.success("Foto da fachada salva com sucesso!")
            st.rerun()

    st.divider()

    # Apartamento
    apts = list_apartments(block_id)
    apt_map = {num: aid for aid, num in apts}
    apt_choice = st.selectbox("Apartamento", ["(selecionar)", "+ Novo apartamento"] + list(apt_map.keys()), key="vis_apt")

    if apt_choice == "+ Novo apartamento":
        na = st.text_input("Número do novo apartamento", placeholder="Ex.: 302", key="new_apt")
        if st.button("Criar apartamento"):
            anum = (na or "").strip()
            if not anum:
                st.warning("Digite o número do apartamento.")
            else:
                try:
                    add_apartment(block_id, anum)
                    st.success("Apartamento criado!")
                    st.rerun()
                except sqlite3.IntegrityError:
                    st.warning("Esse apartamento já existe.")
        st.stop()

    if apt_choice == "(selecionar)":
        st.info("Selecione um apartamento ou crie um novo.")
        st.stop()

    apartment_id = apt_map[apt_choice]
    apt_number = apt_choice

    # Inspeção
    insp = get_or_create_inspection(visit_id, apartment_id)
    inspection_id, created_at, t1, t2, t3, r1, r2, r3, notes = insp

    st.success(f"Contexto: {company_name} • {work_name} • {visit_choice} • {block_name} • Apto {apt_number}")
    st.caption(f"Inspeção ID: {inspection_id} | Criada em: {created_at}")
    st.divider()

    ROOMS = [
        "Sala", "Cozinha", "Banheiro", "Basculante", "Quarto Solteiro", "Quarto Casal",
        "Cozinha - Janela", "Varanda", "Quarto Solteiro - Janela", "Quarto Casal - Janela", "Outro"
    ]

    def room_index(value):
        return ROOMS.index(value) if value in ROOMS else 0

    def fix_room(selected, other):
        if selected == "Outro":
            return (other or "").strip() or "Outro"
        return (selected or "").strip()

    col1, col2 = st.columns(2)

    with col1:
        st.subheader("Espessuras (mm) + Local")

        with st.form("form_medidas", clear_on_submit=False):
            v1 = st.text_input("Espessura 1 (mm)", value="" if t1 is None else str(t1))
            loc1_sel = st.selectbox("Local - Espessura 1", ROOMS, index=room_index(r1))

            v2 = st.text_input("Espessura 2 (mm)", value="" if t2 is None else str(t2))
            loc2_sel = st.selectbox("Local - Espessura 2", ROOMS, index=room_index(r2))

            v3 = st.text_input("Espessura 3 (mm)", value="" if t3 is None else str(t3))
            loc3_sel = st.selectbox("Local - Espessura 3", ROOMS, index=room_index(r3))

            other_room = st.text_input("Se escolher 'Outro', escreva aqui", value="", placeholder="Ex.: Hall / Área de serviço")
            notes_in = st.text_area("Observações", value=notes or "", height=120)

            submitted = st.form_submit_button("Salvar vistoria")

        loc1 = fix_room(loc1_sel, other_room)
        loc2 = fix_room(loc2_sel, other_room)
        loc3 = fix_room(loc3_sel, other_room)

        if submitted:
            n1, n2, n3 = safe_float(v1), safe_float(v2), safe_float(v3)
            if n1 is None or n2 is None or n3 is None:
                st.error("Preencha as 3 Espessuras com números (ex.: 110, 112, 111).")
            else:
                update_inspection(inspection_id, n1, n2, n3, loc1, loc2, loc3, notes_in)
                st.success("Vistoria salva!")
                st.rerun()

    with col2:
        st.subheader("Patologias")

        PATHOLOGIES = [
            "Fissura", "Aresta", "Armadura aparente", "Armadura deslocada", "Segregação",
            "Desvio de planicidade", "Juntas de concretagem", "Espaçador deslocado",
            "Espaçador rotacionado", "Eletroduto aparente", "Material contaminado",
            "Forma suja", "Outro"
        ]

        pathology_type = st.selectbox("Tipo de patologia", PATHOLOGIES, key="ptype")
        comment = st.text_input("Comentário (opcional)", placeholder="Ex.: escada para 1° andar", key="pcomment")

        if "uploader_reset" not in st.session_state:
            st.session_state.uploader_reset = 0

        uploaded_files = st.file_uploader(
            "Enviar foto(s)",
            type=["jpg", "jpeg", "png"],
            accept_multiple_files=True,
            key=f"pfiles_{st.session_state.uploader_reset}"
        )

        if st.button("Salvar patologia(s)", key=f"btn_save_path_{inspection_id}"):
            if not uploaded_files:
                st.error("Envie pelo menos 1 foto.")
            else:
                saved_count = 0
                for uf in uploaded_files:
                    img_bytes = uf.getbuffer().tobytes()
                    ext = guess_ext(uf.name)
                    ctype = content_type_from_ext(ext)
                    ts = str(int(datetime.now().timestamp() * 1000))

                    object_path = storage_key_for_pathology(
                        company_name, work_name, visit_choice,
                        block_name, apt_number, pathology_type,
                        ts, ext
                    )

                    public_url = upload_bytes_to_storage(object_path, img_bytes, ctype)
                    add_pathology(inspection_id, pathology_type, comment, public_url)
                    saved_count += 1

                st.success(f"{saved_count} foto(s) salva(s) no Supabase!")
                st.session_state.uploader_reset += 1
                st.rerun()

        st.divider()
        st.subheader("Patologias registradas (este apartamento / esta data)")

        registros = list_pathologies(inspection_id)
        if not registros:
            st.info("Nenhuma patologia cadastrada.")
        else:
            for pid, ptype, pcomment, purl, pdate in registros:
                st.write(f"**{ptype}** - {pcomment or 'Sem comentário'} - {pdate}")
                st.image(purl, width=320)


# ======================
# Cadastro
# ======================
elif menu == "Cadastro":
    st.header("CADASTRO")

    tab1, tab2 = st.tabs(["Empresas", "Obras"])

    with tab1:
        st.subheader("Cadastrar Empresa")
        new_company = st.text_input("Nome da empresa", placeholder="Ex.: Construtora X", key="new_company")

        if st.button("Salvar empresa", key="btn_save_company"):
            name = (new_company or "").strip()
            if not name:
                st.warning("Digite o nome da empresa.")
            else:
                try:
                    add_company(name)
                    st.success("Empresa cadastrada!")
                    st.rerun()
                except sqlite3.IntegrityError:
                    st.warning("Essa empresa já existe.")

    with tab2:
        st.subheader("Cadastrar Obra")
        companies = list_companies()
        company_map = {name: cid for cid, name in companies}
        company_name = st.selectbox("Empresa", ["(selecionar)"] + list(company_map.keys()), key="cad_company")
        company_id = company_map.get(company_name)

        if not company_id:
            st.info("Selecione uma empresa para cadastrar uma obra.")
        else:
            new_work = st.text_input("Nome da obra", placeholder="Ex.: Residencial Alfa", key="new_work")
            if st.button("Salvar obra", key="btn_save_work"):
                wname = (new_work or "").strip()
                if not wname:
                    st.warning("Digite o nome da obra.")
                else:
                    try:
                        add_work(company_id, wname)
                        st.success("Obra cadastrada!")
                        st.rerun()
                    except sqlite3.IntegrityError:
                        st.warning("Essa obra já existe para essa empresa.")


# ======================
# Exportações
# ======================
else:
    st.header("EXPORTAÇÕES")
    st.caption("Exportação realizada por vistoria. O ZIP inclui fotos das patologias e fachadas dos blocos (Supabase Storage).")

    # Empresa
    companies = list_companies()
    company_map = {name: cid for cid, name in companies}
    company_name = st.selectbox("Empresa", ["(selecionar)"] + list(company_map.keys()), key="exp_company")
    company_id = company_map.get(company_name)
    if not company_id:
        st.stop()

    # Obra
    works = list_works(company_id)
    work_map = {name: wid for wid, name in works}
    work_name = st.selectbox("Obra", ["(selecionar)"] + list(work_map.keys()), key="exp_work")
    work_id = work_map.get(work_name)
    if not work_id:
        st.stop()

    # Vistoria (data)
    visits = list_visits(work_id)
    if not visits:
        st.info("Nenhuma vistoria (data) cadastrada nesta obra ainda.")
        st.stop()

    visit_labels = []
    visit_map = {}
    for vid, vdate, vtitle in visits:
        label = f"{vdate}" if not vtitle else f"{vdate} - {vtitle}"
        visit_labels.append(label)
        visit_map[label] = vid

    visit_choice = st.selectbox("Vistoria (data)", visit_labels, key="exp_visit")
    visit_id = visit_map[visit_choice]

    # ============================
    # GRÁFICO DE PATOLOGIAS
    # ============================
    st.divider()
    st.subheader("Análise da obra")

    stats = get_pathology_stats(visit_id)

    if not stats:
        st.info("Nenhuma patologia registrada nesta vistoria.")
    else:
        labels = [row[0] for row in stats]
        values = [row[1] for row in stats]
        total = sum(values)

        st.caption(f"Total de registros (fotos): {total}")

        fig, ax = plt.subplots(figsize=(4.2, 4.2), dpi=160)

        wedges, texts, autotexts = ax.pie(
            values,
            labels=labels,
            autopct="%1.1f%%",
            startangle=90,
            textprops={"fontsize": 7}
        )

        for t in texts:
            t.set_fontsize(7)
        for a in autotexts:
            a.set_fontsize(7)

        ax.axis("equal")
        plt.tight_layout()
        st.pyplot(fig, use_container_width=False)

    # ============================
    # DOWNLOADS
    # ============================
    st.divider()
    colA, colB = st.columns(2)

    with colA:
        st.subheader("Planilha de espessuras")
        if st.button("Gerar planilha (.xlsx)", key=f"btn_xlsx_{visit_id}"):
            xlsx_bytes = build_measures_xlsx(visit_id)
            fname = f"Espessuras_{safe_name(company_name)}_{safe_name(work_name)}_{safe_name(visit_choice)}_{int(datetime.now().timestamp())}.xlsx"
            st.download_button(
                "Baixar planilha",
                data=xlsx_bytes,
                file_name=fname,
                mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                key=f"dl_xlsx_{visit_id}"
            )

    with colB:
        st.subheader("ZIP - Fotos (Patologias + Fachadas)")
        if st.button("Gerar ZIP de fotos (.zip)", key=f"btn_zip_{visit_id}"):
            try:
                zip_bytes = build_photos_zip(visit_id)
                if not isinstance(zip_bytes, (bytes, bytearray)):
                    st.error("Falha ao gerar ZIP: build_photos_zip não retornou bytes.")
                else:
                    fname = f"fotos_{safe_name(company_name)}_{safe_name(work_name)}_{safe_name(visit_choice)}_{int(datetime.now().timestamp())}.zip"
                    st.download_button(
                        "Baixar ZIP",
                        data=zip_bytes,
                        file_name=fname,
                        mime="application/zip",
                        key=f"dl_zip_{visit_id}"
                    )
            except Exception as e:
                st.error(f"Falha ao gerar ZIP: {e}")

                
