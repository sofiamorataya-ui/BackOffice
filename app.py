import re
import requests
import pandas as pd
import streamlit as st
from bs4 import BeautifulSoup

# ============================================================
# MOTUS (FMCSA)
# ============================================================
API = "https://motus.dot.gov/api/carriers/{dot}"
AUTORIDAD_REQUERIDA = "Motor Carrier of Property (Except Household Goods)"
TIPO_DIR_FISICA = "eef9bd53-0da3-4b96-b462-8e2711a009ef"   # Principal Place of Business
TIPO_DIR_CORREO = "34878d0c-cf18-46ce-a23e-60bfcaf558db"   # Mailing Address

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36")
HEADERS = {"accept": "application/json, text/plain, */*", "user-agent": UA,
           "referer": "https://motus.dot.gov/public/search"}


@st.cache_data(ttl=300, show_spinner=False)
def consultar_motus(dot: str) -> dict | None:
    r = requests.get(API.format(dot=dot), headers=HEADERS, timeout=30)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()


def formatear_direccion(loc: dict) -> str:
    calle = " ".join(p for p in [loc.get("addressLine1"), loc.get("addressLine2")] if p)
    return f'{calle}, {loc.get("city")}, {loc.get("state")} {loc.get("zipCode")}'.strip(", ")


def resumen_motus(dot: str) -> dict | None:
    """Extrae de Motus los datos que se validan."""
    data = consultar_motus(dot)
    if not data:
        return None

    estado_dot = (data.get("entityDotNumber") or {}).get("dotNumberStatus", {}).get("dotNumberStatus", "")
    fuera_servicio = data.get("outOfService", False)

    nombres = [n for n in data.get("entityNames", []) if not n.get("disableDate")]
    legal = next((n["entityName"] for n in nombres if n.get("nameType") == "Legal"), "")
    dba = next((n["entityName"] for n in nombres if n.get("nameType") == "DBA"), "")

    locs = [l for l in data.get("locations", []) if not l.get("disableDate")]
    fisica = next((l for l in locs if l.get("addressTypeId") == TIPO_DIR_FISICA), None)
    if fisica is None and locs:            # respaldo si Motus cambiara el tipo
        fisica = next((l for l in locs if l.get("addressTypeId") != TIPO_DIR_CORREO), locs[0])

    autoridad_estado, docket = "No registrada", ""
    for reg in data.get("entityRegistrations", []):
        for roa in reg.get("entityRegistrationOperatingAuthorities", []):
            oa = roa.get("entityOperatingAuthority") or {}
            tipo = (oa.get("operatingAuthorityType") or {}).get("operatingAuthorityType", "")
            if tipo == AUTORIDAD_REQUERIDA and not oa.get("disableDate"):
                autoridad_estado = (oa.get("operatingAuthorityStatus") or {}).get(
                    "operatingAuthorityStatusName", "")
                docket = oa.get("docketNumber", "")

    return {
        "estado_dot": estado_dot,
        "fuera_servicio": fuera_servicio,
        "ok_dot": estado_dot == "Active" and not fuera_servicio,
        "legal": legal,
        "dba": dba,
        "loc_fisica": fisica,
        "direccion": formatear_direccion(fisica) if fisica else "",
        "docket": docket,
        "autoridad": autoridad_estado,
        "ok_auth": autoridad_estado == "Active",
    }


def validar(dot: str) -> dict:
    m = resumen_motus(dot)
    if not m:
        return {"USDOT": dot, "Resultado": "❌ No encontrado"}
    return {
        "USDOT": dot,
        "Estado USDOT": m["estado_dot"] + (" (OUT OF SERVICE)" if m["fuera_servicio"] else ""),
        "Legal Business Name": m["legal"],
        "Principal Place of Business": m["direccion"],
        "MC": m["docket"],
        "Autoridad Property (Except HHG)": m["autoridad"],
        "Resultado": "✅ Válido" if (m["ok_dot"] and m["ok_auth"] and m["legal"]) else "❌ Revisar",
        "_ok_dot": m["ok_dot"],
        "_ok_auth": m["ok_auth"],
    }


# ============================================================
# CRM STT (sttcrm.com)
# ============================================================
CRM = "https://sttcrm.com"


class SesionExpirada(Exception):
    pass


def crm_login(email: str, password: str) -> requests.Session:
    s = requests.Session()
    s.headers["user-agent"] = UA
    r = s.get(f"{CRM}/login", timeout=30)
    r.raise_for_status()
    soup = BeautifulSoup(r.text, "html.parser")
    pwd = soup.find("input", {"type": "password"})
    form = pwd.find_parent("form") if pwd else None
    if not form:
        raise RuntimeError("No encontré el formulario de login del CRM.")

    datos = {}
    for inp in form.find_all("input"):
        nombre = inp.get("name")
        if not nombre:
            continue
        tipo = (inp.get("type") or "text").lower()
        if tipo == "password":
            datos[nombre] = password
        elif tipo in ("email", "text"):
            datos[nombre] = email
        elif tipo == "checkbox":
            datos[nombre] = "true"
        else:
            datos[nombre] = inp.get("value", "")

    accion = form.get("action") or "/login"
    url = accion if accion.startswith("http") else CRM + accion
    s.post(url, data=datos, timeout=30)

    prueba = s.get(f"{CRM}/Admin", timeout=30)
    if "/login" in prueba.url.lower():
        raise RuntimeError("El CRM rechazó el login. Revisá el email y la contraseña en los secrets.")
    return s


def crm_get(s: requests.Session, ruta: str) -> BeautifulSoup:
    r = s.get(CRM + ruta, timeout=30)
    r.raise_for_status()
    if "/login" in r.url.lower():
        raise SesionExpirada()
    return BeautifulSoup(r.text, "html.parser")


def campos_detalle(soup: BeautifulSoup) -> dict:
    """Lee los pares etiqueta -> valor de una página de detalle del CRM."""
    campos = {}
    for lab in soup.select("main label.col-form-label"):
        fila = lab.find_parent(class_="row")
        if not fila:
            continue
        etiqueta = lab.get_text(strip=True)
        valor_div = fila.find(class_="form-text-row")
        valor = valor_div.get_text(" ", strip=True) if valor_div else ""
        campos.setdefault(etiqueta, valor)
    return campos


def leer_shipment(s: requests.Session, shipment_id: int) -> list[dict]:
    """Devuelve los Driver Assignments del Shipment con su enlace al Carrier."""
    soup = crm_get(s, f"/Admin/Shipments/Details/{shipment_id}")
    bloque = soup.find(id="order-DriverAssignment")
    asignaciones = []
    if not bloque:
        return asignaciones
    for item in bloque.select(".request-info-itembox"):
        a_da = item.find("a", href=re.compile(r"/Admin/DriverAssignment/Details/\d+"))
        a_car = item.find("a", href=re.compile(r"/Admin/Carrier/Details/\d+"))
        estado = ""
        for li in item.find_all("li"):
            if "Status" in li.get_text():
                p = li.find("p")
                estado = p.get_text(strip=True) if p else ""
        asignaciones.append({
            "assignment": a_da.get_text(strip=True) if a_da else "",
            "carrier_id": int(re.search(r"/Details/(\d+)", a_car["href"]).group(1)) if a_car else None,
            "carrier_nombre": a_car.get_text(strip=True) if a_car else "",
            "estado": estado,
        })
    return asignaciones


def leer_carrier(s: requests.Session, carrier_id: int) -> dict:
    c = campos_detalle(crm_get(s, f"/Admin/Carrier/Details/{carrier_id}"))
    return {
        "Company Name": c.get("Company Name", ""),
        "MC": c.get("MC", ""),
        "DOT": c.get("DOT", ""),
        "Address": c.get("Address", ""),
    }


# ============================================================
# COMPARACIÓN CRM vs MOTUS
# ============================================================
ABREV = {
    "LANE": "LN", "STREET": "ST", "AVENUE": "AVE", "ROAD": "RD", "DRIVE": "DR",
    "BOULEVARD": "BLVD", "HIGHWAY": "HWY", "COURT": "CT", "CIRCLE": "CIR",
    "PLACE": "PL", "PARKWAY": "PKWY", "TERRACE": "TER", "TRAIL": "TRL",
    "NORTH": "N", "SOUTH": "S", "EAST": "E", "WEST": "W", "SUITE": "STE",
    "APARTMENT": "APT", "HWY.": "HWY",
}


def norm_texto(t: str) -> str:
    t = re.sub(r"[^A-Z0-9 ]", " ", (t or "").upper())
    return " ".join(t.split())


def norm_calle(t: str) -> str:
    return " ".join(ABREV.get(p, p) for p in norm_texto(t).split())


def solo_digitos(t: str) -> str:
    return re.sub(r"\D", "", t or "")


def partir_dir_crm(dir_crm: str) -> dict:
    """'395 GARCIA LANE, SAN LUIS, AZ, 85349, US' -> partes."""
    partes = [p.strip() for p in (dir_crm or "").split(",") if p.strip()]
    if partes and partes[-1].upper() in ("US", "USA", "UNITED STATES"):
        partes = partes[:-1]
    if len(partes) < 4:
        return {"calle": dir_crm, "ciudad": "", "estado": "", "zip": ""}
    return {"calle": ", ".join(partes[:-3]), "ciudad": partes[-3],
            "estado": partes[-2], "zip": partes[-1]}


def comparar(crm: dict, m: dict) -> list[dict]:
    filas = []

    # USDOT activo
    filas.append({"Criterio": "USDOT activo", "CRM": crm["DOT"],
                  "Motus": m["estado_dot"] + (" (OUT OF SERVICE)" if m["fuera_servicio"] else ""),
                  "ok": m["ok_dot"]})

    # Legal Business Name
    cn = norm_texto(crm["Company Name"])
    if cn and cn == norm_texto(m["legal"]):
        ok_nombre, nota = True, m["legal"]
    elif cn and cn == norm_texto(m["dba"]):
        ok_nombre, nota = False, f'{m["legal"]} (el CRM coincide solo con el DBA: {m["dba"]})'
    else:
        ok_nombre, nota = False, m["legal"]
    filas.append({"Criterio": "Legal Business Name", "CRM": crm["Company Name"],
                  "Motus": nota, "ok": ok_nombre})

    # Principal Place of Business
    p = partir_dir_crm(crm["Address"])
    loc = m["loc_fisica"] or {}
    ok_dir = bool(loc) and all([
        norm_calle(p["calle"]) == norm_calle(" ".join(x for x in [loc.get("addressLine1"), loc.get("addressLine2")] if x)),
        norm_texto(p["ciudad"]) == norm_texto(loc.get("city")),
        norm_texto(p["estado"]) == norm_texto(loc.get("state")),
        solo_digitos(p["zip"])[:5] == solo_digitos(loc.get("zipCode"))[:5],
    ])
    filas.append({"Criterio": "Principal Place of Business", "CRM": crm["Address"],
                  "Motus": m["direccion"], "ok": ok_dir})

    # MC
    ok_mc = bool(solo_digitos(crm["MC"])) and solo_digitos(crm["MC"]) == solo_digitos(m["docket"])
    filas.append({"Criterio": "MC", "CRM": crm["MC"], "Motus": m["docket"], "ok": ok_mc})

    # Operating Authority
    filas.append({"Criterio": AUTORIDAD_REQUERIDA, "CRM": "—", "Motus": m["autoridad"],
                  "ok": m["ok_auth"]})
    return filas


# ============================================================
# UI
# ============================================================
st.set_page_config(page_title="Validador USDOT", page_icon="🚚", layout="wide")
st.title("🚚 Validador USDOT (Motus / FMCSA)")

tab_dot, tab_crm = st.tabs(["Validar USDOT", "Validar Shipment (CRM vs Motus)"])

# ---------- Pestaña 1: USDOT directo ----------
with tab_dot:
    entrada = st.text_area("Número(s) USDOT — uno por línea", "3692487", height=100)

    if st.button("Validar", type="primary", key="btn_dot"):
        dots = list(dict.fromkeys(re.findall(r"\d{4,8}", entrada)))
        if not dots:
            st.warning("Ingresá al menos un número USDOT.")
            st.stop()

        resultados = []
        barra = st.progress(0.0)
        for i, dot in enumerate(dots, 1):
            try:
                resultados.append(validar(dot))
            except requests.RequestException as e:
                resultados.append({"USDOT": dot, "Resultado": f"⚠️ Error de conexión: {e}"})
            barra.progress(i / len(dots))
        barra.empty()

        for r in resultados:
            with st.container(border=True):
                st.subheader(f'USDOT #{r["USDOT"]} — {r["Resultado"]}')
                if "Legal Business Name" not in r:
                    continue
                c1, c2 = st.columns(2)
                c1.markdown(f'**Estado USDOT:** {"🟢" if r["_ok_dot"] else "🔴"} {r["Estado USDOT"]}')
                c1.markdown(f'**Legal Business Name:** {r["Legal Business Name"]}')
                c2.markdown(f'**Principal Place of Business:** {r["Principal Place of Business"]}')
                c2.markdown(f'**{AUTORIDAD_REQUERIDA}:** '
                            f'{"🟢" if r["_ok_auth"] else "🔴"} {r["Autoridad Property (Except HHG)"]} '
                            f'({r["MC"] or "sin MC"})')

        if len(resultados) > 1:
            tabla = pd.DataFrame(resultados).drop(columns=["_ok_dot", "_ok_auth"], errors="ignore")
            st.dataframe(tabla, use_container_width=True, hide_index=True)

# ---------- Pestaña 2: Shipment del CRM ----------
with tab_crm:
    entrada_s = st.text_area("Shipment(s) — uno por línea (ej. S-039245)", "S-039245",
                             height=100, key="txt_ship")

    if st.button("Validar Shipment", type="primary", key="btn_ship"):
        ids = list(dict.fromkeys(int(x) for x in re.findall(r"\d{3,8}", entrada_s)))
        if not ids:
            st.warning("Ingresá al menos un Shipment.")
            st.stop()

        try:
            if "crm" not in st.session_state:
                with st.spinner("Iniciando sesión en el CRM…"):
                    st.session_state.crm = crm_login(st.secrets["STT_EMAIL"],
                                                     st.secrets["STT_PASSWORD"])
        except KeyError:
            st.error("Faltan STT_EMAIL y STT_PASSWORD en los secrets de la app.")
            st.stop()
        except Exception as e:
            st.error(str(e))
            st.stop()

        def con_sesion(fn, *args):
            try:
                return fn(st.session_state.crm, *args)
            except SesionExpirada:
                st.session_state.crm = crm_login(st.secrets["STT_EMAIL"], st.secrets["STT_PASSWORD"])
                return fn(st.session_state.crm, *args)

        for sid in ids:
            with st.container(border=True):
                st.subheader(f"Shipment S-{sid:06d}")
                try:
                    asignaciones = con_sesion(leer_shipment, sid)
                except Exception as e:
                    st.error(f"No pude leer el Shipment: {e}")
                    continue
                if not asignaciones:
                    st.warning("Este Shipment no tiene Driver Assignments.")
                    continue

                for a in asignaciones:
                    st.markdown(f'**{a["assignment"]}** · {a["carrier_nombre"]} · {a["estado"]}')
                    if not a["carrier_id"]:
                        st.warning("El Driver Assignment no tiene Carrier enlazado.")
                        continue
                    try:
                        crm = con_sesion(leer_carrier, a["carrier_id"])
                        m = resumen_motus(solo_digitos(crm["DOT"])) if solo_digitos(crm["DOT"]) else None
                    except Exception as e:
                        st.error(f"Error consultando: {e}")
                        continue
                    if not m:
                        st.error(f'El DOT del CRM ({crm["DOT"] or "vacío"}) no existe en Motus.')
                        continue

                    filas = comparar(crm, m)
                    todo_ok = all(f["ok"] for f in filas)
                    st.markdown("### ✅ Todo coincide" if todo_ok else "### ❌ Revisar")
                    tabla = pd.DataFrame([{"": "🟢" if f["ok"] else "🔴", "Criterio": f["Criterio"],
                                           "CRM": f["CRM"], "Motus": f["Motus"]} for f in filas])
                    st.dataframe(tabla, use_container_width=True, hide_index=True)
                    st.caption(f"[Abrir Carrier en el CRM]({CRM}/Admin/Carrier/Details/{a['carrier_id']}) · "
                               f"[Abrir en Motus](https://motus.dot.gov/customer/{solo_digitos(crm['DOT'])}/account)")
