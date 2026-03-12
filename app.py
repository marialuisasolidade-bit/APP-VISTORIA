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
# SUPABASE STORAGE + EXPORT HELPERS
# (Cole aqui logo após safe_name)
# ======================

def guess_ext(filename: str) -> str:
    ext = os.path.splitext(filename or "")[1].lower()
    if ext in [".jpg", ".jpeg", ".png"]:
        return ext
    return ".jpg"


def content_type_from_ext(ext: str) -> str:
    return "image/png" if (ext or "").lower() == ".png" else "image/jpeg"


def upload_bytes_to_storage(object_path: str, data_bytes: bytes, content_type: str) -> str:
    if not SUPABASE_BUCKET or not isinstance(SUPABASE_BUCKET, str):
        raise ValueError("SUPABASE_BUCKET não definido. Verifique Secrets/ENV.")

    object_path = (object_path or "").lstrip("/")

    supabase.storage.from_(SUPABASE_BUCKET).upload(
        path=object_path,
        file=data_bytes,  # BYTES
        file_options={
            "content-type": str(content_type or "image/jpeg"),
            "upsert": "true",
        },
    )

    return supabase.storage.from_(SUPABASE_BUCKET).get_public_url(object_path)


def storage_key_for_facade(company_name: str, work_name: str, block_name: str, ts: str, ext: str) -> str:
    return (
        f"fachadas/"
        f"{safe_name(company_name)}/"
        f"{safe_name(work_name)}/"
        f"{safe_name(block_name)}/"
        f"FACHADA_{safe_name(block_name)}_{ts}{ext}"
    )


def storage_key_for_pathology(company_name: str, work_name: str, visit_choice: str,
                              block_name: str, apt_number: str, pathology_type: str,
                              pid_or_ts: str, ext: str) -> str:
    return (
        f"patologias/"
        f"{safe_name(company_name)}/"
        f"{safe_name(work_name)}/"
        f"{safe_name(visit_choice)}/"
        f"{safe_name(pathology_type)}/"
        f"{safe_name(block_name)}_{safe_name(apt_number)}_{pid_or_ts}{ext}"
    )


def storage_key_from_public_url(public_url: str) -> str | None:
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
# FACHADAS (DB - SUPABASE)
# ======================

def get_block_facade(block_id: int):
    r = supabase.table("block_facades").select("*").eq("block_id", block_id).limit(1).execute()
    if not r.data:
        return None
    x = r.data[0]
    return (x.get("photo_path"), x.get("created_at"))


def upsert_block_facade(block_id: int, photo_url: str):
    # upsert via "block_id" (precisa ter UNIQUE no banco)
    supabase.table("block_facades").upsert(
        {"block_id": block_id, "photo_path": photo_url, "created_at": now_iso()},
        on_conflict="block_id"
    ).execute()


# ======================
# XLSX - 4 colunas (Bloco, Apartamento, Local, Espessura)
# ======================

def build_measures_xlsx(visit_id: int) -> bytes:
    r = supabase.table("inspections").select(
        "id, thickness1, thickness2, thickness3, thickness1_room, thickness2_room, thickness3_room, "
        "apartments(number, blocks(name))"
    ).eq("visit_id", visit_id).execute()

    wb = Workbook()
    ws = wb.active
    ws.title = "Espessuras"
    ws.append(["Bloco", "Apartamento", "Local", "Espessura (mm)"])

    if r.data:
        for row in r.data:
            apt = row.get("apartments") or {}
            apt_number = apt.get("number") or ""
            blk = (apt.get("blocks") or {})
            bloco = blk.get("name") or ""

            # quebra em linhas (uma por medida preenchida)
            if row.get("thickness1") is not None:
                ws.append([bloco, apt_number, row.get("thickness1_room") or "", float(row["thickness1"])])
            if row.get("thickness2") is not None:
                ws.append([bloco, apt_number, row.get("thickness2_room") or "", float(row["thickness2"])])
            if row.get("thickness3") is not None:
                ws.append([bloco, apt_number, row.get("thickness3_room") or "", float(row["thickness3"])])

    bio = BytesIO()
    wb.save(bio)
    return bio.getvalue()


# ======================
# ZIP - Patologias
# ======================

def build_pathologies_zip(visit_id: int) -> bytes:
    r = supabase.table("pathology_photos").select(
        "id, pathology_type, photo_path, "
        "inspections(visit_id, apartments(number, blocks(name)))"
    ).execute()

    # filtra apenas as do visit_id (porque o select aninhado vem tudo)
    rows = []
    for p in (r.data or []):
        insp = p.get("inspections") or {}
        if insp.get("visit_id") != visit_id:
            continue
        apt = (insp.get("apartments") or {})
        apto = apt.get("number") or ""
        bloco = (apt.get("blocks") or {}).get("name") or ""
        rows.append((p["id"], p["pathology_type"], p["photo_path"], bloco, apto))

    bio = BytesIO()
    with zipfile.ZipFile(bio, mode="w", compression=zipfile.ZIP_DEFLATED) as z:
        if not rows:
            z.writestr("LEIA-ME.txt", "Nenhuma patologia cadastrada nesta vistoria.")
            return bio.getvalue()

        for pid, ptype, url, bloco, apto in rows:
            folder = safe_name(ptype)
            bloco_safe = safe_name(str(bloco))
            apto_safe = safe_name(str(apto))

            ext = os.path.splitext(url or "")[1].lower()
            if ext not in [".jpg", ".jpeg", ".png"]:
                ext = ".jpg"

            arcname = f"Patologias/{folder}/{bloco_safe}_{apto_safe}_{pid}{ext}"

            try:
                if is_url(url):
                    object_key = storage_key_from_public_url(url)
                    if not object_key:
                        z.writestr(f"Patologias/{folder}/URL_INVALIDA_{pid}.txt", f"URL: {url}")
                    else:
                        file_bytes = supabase.storage.from_(SUPABASE_BUCKET).download(object_key)
                        z.writestr(arcname, file_bytes)
                else:
                    z.writestr(f"Patologias/{folder}/SEM_URL_{pid}.txt", f"Valor: {url}")
            except Exception as e:
                z.writestr(f"Patologias/{folder}/FALHA_{pid}.txt", f"URL: {url}\nErro: {e}")

    return bio.getvalue()


# ======================
# ZIP - Fachadas
# ======================

def build_facades_zip(visit_id: int) -> bytes:
    # pega blocos envolvidos na vistoria
    insp = supabase.table("inspections").select(
        "id, apartments(block_id, blocks(name))"
    ).eq("visit_id", visit_id).execute()

    block_ids = set()
    block_names = {}
    for row in (insp.data or []):
        apt = row.get("apartments") or {}
        bid = apt.get("block_id")
        if bid:
            block_ids.add(bid)
            block_names[bid] = (apt.get("blocks") or {}).get("name") or ""

    if not block_ids:
        bio = BytesIO()
        with zipfile.ZipFile(bio, mode="w", compression=zipfile.ZIP_DEFLATED) as z:
            z.writestr("LEIA-ME.txt", "Nenhum bloco encontrado nesta vistoria.")
        return bio.getvalue()

    fac = supabase.table("block_facades").select("*").in_("block_id", list(block_ids)).execute()

    bio = BytesIO()
    with zipfile.ZipFile(bio, mode="w", compression=zipfile.ZIP_DEFLATED) as z:
        if not fac.data:
            z.writestr("LEIA-ME.txt", "Nenhuma fachada cadastrada para os blocos desta vistoria.")
            return bio.getvalue()

        for f in fac.data:
            bid = f.get("block_id")
            url = f.get("photo_path")
            bloco = block_names.get(bid, str(bid))
            bloco_safe = safe_name(bloco)

            ext = os.path.splitext(url or "")[1].lower()
            if ext not in [".jpg", ".jpeg", ".png"]:
                ext = ".jpg"

            arcname = f"Fachadas/FACHADA_{bloco_safe}{ext}"

            try:
                object_key = storage_key_from_public_url(url) if is_url(url) else None
                if not object_key:
                    z.writestr(f"Fachadas/URL_INVALIDA_{bloco_safe}.txt", f"URL: {url}")
                else:
                    file_bytes = supabase.storage.from_(SUPABASE_BUCKET).download(object_key)
                    z.writestr(arcname, file_bytes)
            except Exception as e:
                z.writestr(f"Fachadas/FALHA_{bloco_safe}.txt", f"URL: {url}\nErro: {e}")

    return bio.getvalue()

# ======================
# EMPRESAS
# ======================

def add_company(name):

    try:
        supabase.table("companies").insert({
            "name": name
        }).execute()

        return True

    except Exception:
        return False


def list_companies():

    r = supabase.table("companies").select("*").order("name").execute()

    return [(x["id"],x["name"]) for x in r.data]


# ======================
# OBRAS
# ======================

def add_work(company_id,name):

    try:
        supabase.table("works").insert({
            "company_id": company_id,
            "name": name
        }).execute()

        return True

    except Exception:
        return False


def list_works(company_id):

    r = supabase.table("works").select("*").eq(
        "company_id",company_id
    ).order("name").execute()

    return [(x["id"],x["name"]) for x in r.data]


# ======================
# BLOCOS
# ======================

def add_block(work_id,name):

    try:
        supabase.table("blocks").insert({
            "work_id": work_id,
            "name": name
        }).execute()

        return True

    except Exception:
        return False


def list_blocks(work_id):

    r = supabase.table("blocks").select("*").eq(
        "work_id",work_id
    ).order("name").execute()

    return [(x["id"],x["name"]) for x in r.data]

# ======================
# APARTAMENTOS
# ======================

def add_apartment(block_id,number):

    try:
        supabase.table("apartments").insert({
            "block_id": block_id,
            "number": number
        }).execute()

        return True

    except Exception:
        return False


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
        st.subheader("Cadastrar Empresa")
        name = st.text_input("Nome da empresa")

        if st.button("Salvar empresa"):

            ok = add_company(name)

            if ok:
                st.success("Empresa cadastrada com sucesso!")
                st.rerun()

            else:
                st.error("Empresa já cadastrada.")
        

    with tab2:

        companies = list_companies()

        company_map = {name:cid for cid,name in companies}

        company_name = st.selectbox(
            "Empresa",
            [""] + list(company_map.keys())
        )

        if company_name:
            st.subheader("Cadastrar Obra")
            st.caption("Selecione uma empresa para cadastrar uma obra.")
            work = st.text_input("Nome da obra")

            
            if st.button("Salvar obra"):
                company_id = company_map[company_name]
                ok = add_work(company_id, work)

                if ok:
                    st.success("Obra cadastrada!")
                    st.rerun()

                else:
                    st.error("Essa obra já existe para essa empresa.")


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
    
    block_choice = st.selectbox(
        "Bloco",
        ["(selecionar)", "+ Novo bloco"] + list(block_map.keys())
    )
    
    # ======================
    # CRIAR NOVO BLOCO
    # ======================
    
    if block_choice == "+ Novo bloco":
    
        new_block = st.text_input(
            "Nome do novo bloco",
            placeholder="Ex.: Bloco A"
        )
    
        if st.button("Criar bloco"):
    
            bname = (new_block or "").strip()
    
            if not bname:
                st.warning("Digite o nome do bloco.")
    
            else:
    
                ok = add_block(work_id, bname)
    
                if ok:
                    st.success("Bloco criado com sucesso!")
                    st.rerun()
    
                else:
                    st.error("Esse bloco já existe nesta obra.")
    
        st.stop()
    
    
    # ======================
    # BLOCO NÃO SELECIONADO
    # ======================
    
    if block_choice == "(selecionar)":
        st.info("Selecione um bloco ou crie um novo.")
        st.stop()
    
    
    block_id = block_map[block_choice]
    block_name = block_choice
    
    # ======================
    # FACHADA DO BLOCO
    # ======================
    st.subheader("Fachada do Bloco")
    
    facade_row = get_block_facade(block_id)
    if facade_row:
        facade_url, facade_created = facade_row
        st.caption(f"Última foto cadastrada: {facade_created}")
        if facade_url:
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
            st.stop()
    
        try:
            # bytes da imagem (mais compatível no Streamlit Cloud)
            img_bytes = facade_file.getvalue()
    
           
            ext = guess_ext(facade_file.name)
            ctype = content_type_from_ext(ext)
            ts = str(int(datetime.now().timestamp() * 1000))
    
            # caminho no bucket (sem caracteres inválidos)
            object_path = storage_key_for_facade(company_name, work_name, block_name, ts, ext)
    
            # upload no Storage -> retorna URL pública
            public_url = upload_bytes_to_storage(object_path, img_bytes, ctype)
    
            # salva URL no SQLite (block_facades)
            upsert_block_facade(block_id, public_url)
    
            st.success("Foto da fachada salva com sucesso!")
            st.rerun()
    
        except Exception as e:
            st.error(f"Erro ao salvar fachada: {e}")
    
    st.divider()
    
    # ======================
    # APARTAMENTOS
    # ======================
    
    apartments = list_apartments(block_id)
    
    apt_map = {num:aid for aid,num in apartments}
    
    apt_choice = st.selectbox(
        "Apartamento",
        ["(selecionar)", "+ Novo apartamento"] + list(apt_map.keys())
    )
    
    # ======================
    # CRIAR NOVO APARTAMENTO
    # ======================
    
    if apt_choice == "+ Novo apartamento":
    
        new_apt = st.text_input(
            "Número do novo apartamento",
            placeholder="Ex.: 302"
        )
    
        if st.button("Criar apartamento"):
    
            anum = (new_apt or "").strip()
    
            if not anum:
                st.warning("Digite o número do apartamento.")
    
            else:
    
                ok = add_apartment(block_id, anum)
    
                if ok:
                    st.success("Apartamento criado com sucesso!")
                    st.rerun()
    
                else:
                    st.error("Esse apartamento já existe neste bloco.")
    
        st.stop()
    
    
    # ======================
    # APARTAMENTO NÃO SELECIONADO
    # ======================
    
    if apt_choice == "(selecionar)":
        st.info("Selecione um apartamento ou crie um novo.")
        st.stop()

    apt_number = apt_choice
    apartment_id = apt_map[apt_choice]
 
    # ======================
    # INSPEÇÃO
    # ======================
    
    insp = get_or_create_inspection(visit_id, apartment_id)
    
    inspection_id, created_at, t1, t2, t3, r1, r2, r3, notes = insp
    
    st.success(f"Contexto: {company_name} • {work_name} • {visit_choice} • {block_name} • Apto {apt_number}")
    st.caption(f"Inspeção ID: {inspection_id} | Criada em: {created_at}")
    
    st.divider()
    
    ROOMS = [
        "Sala",
        "Cozinha",
        "Banheiro",
        "Basculante",
        "Quarto Solteiro",
        "Quarto Casal",
        "Cozinha - Janela",
        "Varanda",
        "Quarto Solteiro - Janela",
        "Quarto Casal - Janela",
        "Outro"
    ]
    
    
    def room_index(value):
        if value in ROOMS:
            return ROOMS.index(value)
        return 0
    
    
    def fix_room(selected, other):
        if selected == "Outro":
            return (other or "").strip() or "Outro"
        return selected
    
    
    col1, col2 = st.columns(2)
    
    # ======================
    # ESPESSURAS
    # ======================
    
    with col1:
    
        st.subheader("Espessuras (mm) + Local")
    
        with st.form("form_medidas", clear_on_submit=False):
    
            v1 = st.text_input(
                "Espessura 1 (mm)",
                value="" if t1 is None else str(t1)
            )
    
            loc1_sel = st.selectbox(
                "Local - Espessura 1",
                ROOMS,
                index=room_index(r1)
            )
    
    
            v2 = st.text_input(
                "Espessura 2 (mm)",
                value="" if t2 is None else str(t2)
            )
    
            loc2_sel = st.selectbox(
                "Local - Espessura 2",
                ROOMS,
                index=room_index(r2)
            )
    
    
            v3 = st.text_input(
                "Espessura 3 (mm)",
                value="" if t3 is None else str(t3)
            )
    
            loc3_sel = st.selectbox(
                "Local - Espessura 3",
                ROOMS,
                index=room_index(r3)
            )
    
    
            other_room = st.text_input(
                "Se escolher 'Outro', escreva aqui",
                placeholder="Ex.: Hall / Área de serviço"
            )
    
    
            notes_in = st.text_area(
                "Observações",
                value=notes or "",
                height=120
            )
    
    
            submitted = st.form_submit_button("Salvar vistoria")
    
    
        loc1 = fix_room(loc1_sel, other_room)
        loc2 = fix_room(loc2_sel, other_room)
        loc3 = fix_room(loc3_sel, other_room)
    
    
        if submitted:
    
            n1 = safe_float(v1)
            n2 = safe_float(v2)
            n3 = safe_float(v3)
    
            if n1 is None or n2 is None or n3 is None:
    
                st.error("Preencha as 3 espessuras com números válidos.")
    
            else:
    
                update_inspection(
                    inspection_id,
                    n1,
                    n2,
                    n3,
                    loc1,
                    loc2,
                    loc3,
                    notes_in
                )
    
                st.success("Vistoria salva com sucesso!")
    
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
    
        # usado para "resetar" o uploader depois de salvar (evita acumular)
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
                st.stop()
    
            try:
                saved_count = 0
                base_ts = str(int(datetime.now().timestamp() * 1000))
    
                for idx, uf in enumerate(uploaded_files, start=1):
                    # bytes (compatível com Streamlit Cloud)
                    img_bytes = uf.getvalue()
    
                    ext = guess_ext(uf.name)
                    ctype = content_type_from_ext(ext)
    
                    # id único por arquivo (evita sobrescrever e evita duplicar nome)
                    photo_id = f"{base_ts}_{idx}"
    
                    object_path = storage_key_for_pathology(
                        company_name, work_name, visit_choice,
                        block_name, apt_number, pathology_type,
                        photo_id, ext
                    )
    
                    public_url = upload_bytes_to_storage(object_path, img_bytes, ctype)
    
                    # grava no SQLite a URL pública
                    add_pathology(inspection_id, pathology_type, comment, public_url)
                    saved_count += 1
    
                st.success(f"{saved_count} foto(s) salva(s) no Supabase!")
                st.session_state.uploader_reset += 1
                st.rerun()
    
            except Exception as e:
                st.error(f"Erro ao salvar patologia(s): {e}")
    
        st.divider()
        st.subheader("Patologias registradas (este apartamento / esta data)")
    
        registros = list_pathologies(inspection_id)
        if not registros:
            st.info("Nenhuma patologia cadastrada.")
        else:
            for pid, ptype, pcomment, purl, pdate in registros:
                st.write(f"**{ptype}** - {pcomment or 'Sem comentário'} - {pdate}")
                if purl:
                    st.image(purl, width=320)


# ======================
# EXPORTAÇÕES  (cole NO UGAR do seu bloco "else: st.header('EXPORTAÇÕES') ...")
# ======================
else:    
    st.header("EXPORTAÇÕES")
    st.caption("Baixe a planilha (espessuras) e os ZIPs separados: Patologias e Fachadas.")

    # Empresa
    companies = list_companies()
    company_map = {name: cid for cid, name in companies}

    company_name = st.selectbox("Empresa", ["(selecionar)"] + list(company_map.keys()), key="exp_company")
    if company_name == "(selecionar)":
        st.stop()
    company_id = company_map[company_name]

    # Obra
    works = list_works(company_id)
    work_map = {name: wid for wid, name in works}

    work_name = st.selectbox("Obra", ["(selecionar)"] + list(work_map.keys()), key="exp_work")
    if work_name == "(selecionar)":
        st.stop()
    work_id = work_map[work_name]

    # Vistoria
    visits = list_visits(work_id)
    if not visits:
        st.info("Nenhuma vistoria cadastrada nessa obra ainda.")
        st.stop()

    visit_labels = []
    visit_map = {}
    for vid, vdate, vtitle in visits:
        label = f"{vdate}" if not (vtitle or "").strip() else f"{vdate} - {vtitle}"
        visit_labels.append(label)
        visit_map[label] = vid

    visit_choice = st.selectbox("Vistoria (data)", visit_labels, key="exp_visit")
    visit_id = visit_map[visit_choice]

    # ============================
    # GRÁFICO (Patologias)
    # ============================
    st.divider()
    st.subheader("Análise da vistoria")

    stats = get_pathology_stats(visit_id)
    if not stats:
        st.info("Nenhuma patologia registrada nesta vistoria.")
    else:
        labels = [x[0] for x in stats]
        values = [x[1] for x in stats]
        total = sum(values)
        st.caption(f"Total de registros (fotos): {total}")

        fig, ax = plt.subplots(figsize=(4.2, 4.2), dpi=160)
        wedges, texts, autotexts = ax.pie(
            values,
            labels=labels,
            autopct="%1.1f%%",
            startangle=90,
            textprops={"fontsize": 7},
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
    st.subheader("Downloads")

    colA, colB, colC = st.columns(3)

    # 1) XLSX Espessuras (4 colunas)
    with colA:
        st.caption("Planilha de espessuras")
        if st.button("Gerar planilha (.xlsx)", key=f"btn_xlsx_{visit_id}"):
            try:
                xlsx_bytes = build_measures_xlsx(visit_id)
                fname = (
                    f"Espessuras_{safe_name(company_name)}_"
                    f"{safe_name(work_name)}_{safe_name(visit_choice)}_"
                    f"{int(datetime.now().timestamp())}.xlsx"
                )
                st.download_button(
                    "Baixar planilha",
                    data=xlsx_bytes,
                    file_name=fname,
                    mime="application/vnd.openxmlformats-officedocument.spreadsheetml.sheet",
                    key=f"dl_xlsx_{visit_id}",
                )
            except Exception as e:
                st.error(f"Falha ao gerar planilha: {e}")

    # 2) ZIP Patologias
    with colB:
        st.caption("ZIP - Patologias (fotos)")
        if st.button("Gerar ZIP Patologias", key=f"btn_zip_pat_{visit_id}"):
            try:
                zip_bytes = build_pathologies_zip(visit_id)
                if not isinstance(zip_bytes, (bytes, bytearray)):
                    st.error("Falha: ZIP Patologias não retornou bytes.")
                else:
                    fname = (
                        f"Patologias_{safe_name(company_name)}_"
                        f"{safe_name(work_name)}_{safe_name(visit_choice)}_"
                        f"{int(datetime.now().timestamp())}.zip"
                    )
                    st.download_button(
                        "Baixar ZIP Patologias",
                        data=zip_bytes,
                        file_name=fname,
                        mime="application/zip",
                        key=f"dl_zip_pat_{visit_id}",
                    )
            except Exception as e:
                st.error(f"Falha ao gerar ZIP Patologias: {e}")

    # 3) ZIP Fachadas
    with colC:
        st.caption("ZIP - Fachadas (fotos)")
        if st.button("Gerar ZIP Fachadas", key=f"btn_zip_fac_{visit_id}"):
            try:
                zip_bytes = build_facades_zip(visit_id)
                if not isinstance(zip_bytes, (bytes, bytearray)):
                    st.error("Falha: ZIP Fachadas não retornou bytes.")
                else:
                    fname = (
                        f"Fachadas_{safe_name(company_name)}_"
                        f"{safe_name(work_name)}_{safe_name(visit_choice)}_"
                        f"{int(datetime.now().timestamp())}.zip"
                    )
                    st.download_button(
                        "Baixar ZIP Fachadas",
                        data=zip_bytes,
                        file_name=fname,
                        mime="application/zip",
                        key=f"dl_zip_fac_{visit_id}",
                    )
            except Exception as e:
                st.error(f"Falha ao gerar ZIP Fachadas: {e}")













