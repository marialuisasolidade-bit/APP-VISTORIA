import os
import re
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
# CONFIG STREAMLIT
# ======================

st.set_page_config(
    page_title="VISTORIA",
    layout="wide",
    initial_sidebar_state="expanded"
)

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
}}

</style>

<div class="infra-topbar">
    {"<img src='data:image/png;base64," + logo_b64 + "'>" if logo_b64 else ""}
    <div class="infra-title">SISTEMA DE REGISTRO DE VISTORIA</div>
    <div class="infra-subtitle">PAREDE DE CONCRETO • CONTROLE TÉCNICO</div>
</div>
""", unsafe_allow_html=True)

# ======================
# SUPABASE
# ======================

load_dotenv()

SUPABASE_URL = st.secrets.get("SUPABASE_URL", os.getenv("SUPABASE_URL"))
SUPABASE_KEY = st.secrets.get("SUPABASE_KEY", os.getenv("SUPABASE_KEY"))
SUPABASE_BUCKET = st.secrets.get("SUPABASE_BUCKET", os.getenv("SUPABASE_BUCKET"))

if not SUPABASE_URL or not SUPABASE_KEY:
    st.error("Supabase não configurado")
    st.stop()

supabase = create_client(SUPABASE_URL, SUPABASE_KEY)


# ======================
# HELPERS
# ======================

def now_iso():
    return datetime.now().isoformat(timespec="seconds")


def safe_float(x):
    try:
        s = str(x).strip().replace(",", ".")
        if s == "":
            return None
        return float(s)
    except:
        return None


def safe_name(text):
    text = (text or "").strip()
    text = unicodedata.normalize("NFKD", text)
    text = text.encode("ascii", "ignore").decode("ascii")
    text = re.sub(r"\s+", "_", text)
    text = re.sub(r"[^A-Za-z0-9_\-]", "", text)
    return text[:80]


# ======================
# EMPRESAS
# ======================

def add_company(name):

    supabase.table("companies").insert({
        "name":name
    }).execute()


def list_companies():

    r = supabase.table("companies").select("*").order("name").execute()

    return [(x["id"],x["name"]) for x in r.data]


# ======================
# OBRAS
# ======================

def add_work(company_id,name):

    supabase.table("works").insert({
        "company_id":company_id,
        "name":name
    }).execute()


def list_works(company_id):

    r = supabase.table("works").select("*").eq(
        "company_id",company_id
    ).order("name").execute()

    return [(x["id"],x["name"]) for x in r.data]


# ======================
# BLOCOS
# ======================

def add_block(work_id,name):

    supabase.table("blocks").insert({
        "work_id":work_id,
        "name":name
    }).execute()


def list_blocks(work_id):

    r = supabase.table("blocks").select("*").eq(
        "work_id",work_id
    ).order("name").execute()

    return [(x["id"],x["name"]) for x in r.data]

# ======================
# APARTAMENTOS
# ======================

def add_apartment(block_id,number):

    supabase.table("apartments").insert({
        "block_id":block_id,
        "number":number
    }).execute()


def list_apartments(block_id):

    r = supabase.table("apartments").select("*").eq(
        "block_id",block_id
    ).order("number").execute()

    return [(x["id"],x["number"]) for x in r.data]


# ======================
# VISITAS
# ======================

def add_visit(work_id,visit_date,title):

    supabase.table("visits").insert({
        "work_id":work_id,
        "visit_date":visit_date,
        "title":title
    }).execute()


def list_visits(work_id):

    r = supabase.table("visits").select("*").eq(
        "work_id",work_id
    ).order("visit_date",desc=True).execute()

    return [(x["id"],x["visit_date"],x["title"]) for x in r.data]


# ======================
# INSPEÇÕES
# ======================

def get_or_create_inspection(visit_id,apartment_id):

    r = supabase.table("inspections").select("*").eq(
        "visit_id",visit_id
    ).eq(
        "apartment_id",apartment_id
    ).execute()

    if r.data:

        x = r.data[0]

        return (
            x["id"],
            "",
            x["thickness1"],
            x["thickness2"],
            x["thickness3"],
            x["thickness1_room"],
            x["thickness2_room"],
            x["thickness3_room"],
            x["notes"]
        )

    r = supabase.table("inspections").insert({
        "visit_id":visit_id,
        "apartment_id":apartment_id
    }).execute()

    x = r.data[0]

    return (x["id"],"",None,None,None,"","","","")


def update_inspection(id,n1,n2,n3,r1,r2,r3,notes):

    supabase.table("inspections").update({
        "thickness1":n1,
        "thickness2":n2,
        "thickness3":n3,
        "thickness1_room":r1,
        "thickness2_room":r2,
        "thickness3_room":r3,
        "notes":notes
    }).eq("id",id).execute()


# ======================
# PATOLOGIAS
# ======================

def add_pathology(inspection_id,type,comment,url):

    supabase.table("pathology_photos").insert({
        "inspection_id":inspection_id,
        "pathology_type":type,
        "comment":comment,
        "photo_path":url
    }).execute()


def list_pathologies(inspection_id):

    r = supabase.table("pathology_photos").select("*").eq(
        "inspection_id",inspection_id
    ).order("id",desc=True).execute()

    return [
        (
            x["id"],
            x["pathology_type"],
            x["comment"],
            x["photo_path"],
            x["created_at"]
        )
        for x in r.data
    ]


# ======================
# GRÁFICO DE PATOLOGIAS
# ======================

def get_pathology_stats(visit_id):

    r = supabase.rpc(
        "count_pathologies_by_visit",
        {"visitid":visit_id}
    ).execute()

    if not r.data:
        return []

    return [(x["pathology_type"],x["total"]) for x in r.data]


# ======================
# MENU
# ======================

menu = st.sidebar.radio(
    "Menu",
    ["Vistoria","Cadastro","Exportações"]
)


# ======================
# CADASTRO
# ======================

if menu == "Cadastro":

    st.header("CADASTRO")

    tab1,tab2 = st.tabs(["Empresas","Obras"])

    with tab1:

        name = st.text_input("Nome da empresa")

        if st.button("Salvar empresa"):

            add_company(name)

            st.success("Empresa cadastrada")
            st.rerun()


    with tab2:

        companies = list_companies()

        company_map = {name:cid for cid,name in companies}

        company_name = st.selectbox(
            "Empresa",
            [""] + list(company_map.keys())
        )

        if company_name:

            work = st.text_input("Nome da obra")

            if st.button("Salvar obra"):

                add_work(
                    company_map[company_name],
                    work
                )

                st.success("Obra cadastrada")
                st.rerun()


# ======================
# VISTORIA
# ======================

elif menu == "Vistoria":

    st.header("VISTORIA")

    companies = list_companies()
    company_map = {name:cid for cid,name in companies}

    company_name = st.selectbox(
        "Empresa",
        [""] + list(company_map.keys())
    )

    if not company_name:
        st.stop()

    company_id = company_map[company_name]


    works = list_works(company_id)
    work_map = {name:wid for wid,name in works}

    work_name = st.selectbox(
        "Obra",
        [""] + list(work_map.keys())
    )

    if not work_name:
        st.stop()

    work_id = work_map[work_name]


    visits = list_visits(work_id)

    visit_labels = []
    visit_map = {}

    for vid,vdate,vtitle in visits:

        label = f"{vdate} - {vtitle}"

        visit_labels.append(label)
        visit_map[label] = vid


    visit_choice = st.selectbox(
        "Vistoria",
        ["Nova vistoria"] + visit_labels
    )


    if visit_choice == "Nova vistoria":

        vdate = st.date_input("Data")
        title = st.text_input("Título")

        if st.button("Criar vistoria"):

            add_visit(work_id,str(vdate),title)

            st.success("Criada")
            st.rerun()

        st.stop()


    visit_id = visit_map[visit_choice]


    blocks = list_blocks(work_id)
    block_map = {name:bid for bid,name in blocks}

    block_name = st.selectbox(
        "Bloco",
        [""] + list(block_map.keys())
    )

    if not block_name:
        st.stop()

    block_id = block_map[block_name]


    apartments = list_apartments(block_id)
    apt_map = {num:aid for aid,num in apartments}

    apt_number = st.selectbox(
        "Apartamento",
        [""] + list(apt_map.keys())
    )

    if not apt_number:
        st.stop()

    apartment_id = apt_map[apt_number]


    insp = get_or_create_inspection(
        visit_id,
        apartment_id
    )

    inspection_id = insp[0]


    st.subheader("Espessuras")

    v1 = st.text_input("Espessura 1")
    v2 = st.text_input("Espessura 2")
    v3 = st.text_input("Espessura 3")

    if st.button("Salvar vistoria"):

        update_inspection(
            inspection_id,
            safe_float(v1),
            safe_float(v2),
            safe_float(v3),
            "",
            "",
            "",
            ""
        )

        st.success("Salvo")


# ======================
# EXPORTAÇÕES
# ======================

else:

    st.header("EXPORTAÇÕES")

    companies = list_companies()
    company_map = {name:cid for cid,name in companies}

    company_name = st.selectbox(
        "Empresa",
        [""] + list(company_map.keys())
    )

    if not company_name:
        st.stop()

    company_id = company_map[company_name]


    works = list_works(company_id)
    work_map = {name:wid for wid,name in works}

    work_name = st.selectbox(
        "Obra",
        [""] + list(work_map.keys())
    )

    if not work_name:
        st.stop()

    work_id = work_map[work_name]


    visits = list_visits(work_id)

    visit_labels = []
    visit_map = {}

    for vid,vdate,vtitle in visits:

        label = f"{vdate} - {vtitle}"

        visit_labels.append(label)
        visit_map[label] = vid


    visit_choice = st.selectbox(
        "Vistoria",
        visit_labels
    )

    visit_id = visit_map[visit_choice]


    stats = get_pathology_stats(visit_id)

    if stats:

        labels = [x[0] for x in stats]
        values = [x[1] for x in stats]

        fig,ax = plt.subplots(figsize=(4,4),dpi=150)

        ax.pie(
            values,
            labels=labels,
            autopct="%1.1f%%",
            startangle=90
        )

        ax.axis("equal")

        st.pyplot(fig)




