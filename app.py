import re
import requests
import pandas as pd
import streamlit as st

API = "https://motus.dot.gov/api/carriers/{dot}"
AUTORIDAD_REQUERIDA = "Motor Carrier of Property (Except Household Goods)"

HEADERS = {
    "accept": "application/json, text/plain, */*",
    "user-agent": "Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
                  "(KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36",
    "referer": "https://motus.dot.gov/public/search",
}


@st.cache_data(ttl=300, show_spinner=False)
def consultar_motus(dot: str) -> dict | None:
    r = requests.get(API.format(dot=dot), headers=HEADERS, timeout=30)
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json()


def formatear_direccion(loc: dict) -> str:
    partes = [loc.get("addressLine1"), loc.get("addressLine2")]
    calle = " ".join(p for p in partes if p)
    return f'{calle}, {loc.get("city")}, {loc.get("state")} {loc.get("zipCode")}'.strip(", ")


def validar(dot: str) -> dict:
    data = consultar_motus(dot)
    if not data:
        return {"USDOT": dot, "Resultado": "❌ No encontrado"}

    # 1. Estado del USDOT
    estado_dot = (data.get("entityDotNumber") or {}).get("dotNumberStatus", {}).get("dotNumberStatus", "")
    fuera_servicio = data.get("outOfService", False)

    # 2. Legal Business Name
    legal = next((n["entityName"] for n in data.get("entityNames", [])
                  if n.get("nameType") == "Legal" and not n.get("disableDate")), "")

    # 3. Principal Place of Business (direcciones activas, sin duplicados)
    direcciones = []
    for loc in data.get("locations", []):
        if loc.get("disableDate"):
            continue
        d = formatear_direccion(loc)
        if d not in direcciones:
            direcciones.append(d)

    # 4. Operating Authority: Motor Carrier of Property (Except HHG)
    autoridad_estado, docket = "No registrada", ""
    for reg in data.get("entityRegistrations", []):
        for roa in reg.get("entityRegistrationOperatingAuthorities", []):
            oa = roa.get("entityOperatingAuthority") or {}
            tipo = (oa.get("operatingAuthorityType") or {}).get("operatingAuthorityType", "")
            if tipo == AUTORIDAD_REQUERIDA and not oa.get("disableDate"):
                autoridad_estado = (oa.get("operatingAuthorityStatus") or {}).get(
                    "operatingAuthorityStatusName", "")
                docket = oa.get("docketNumber", "")

    ok_dot = estado_dot == "Active" and not fuera_servicio
    ok_auth = autoridad_estado == "Active"

    return {
        "USDOT": dot,
        "Estado USDOT": estado_dot + (" (OUT OF SERVICE)" if fuera_servicio else ""),
        "Legal Business Name": legal,
        "Principal Place of Business": " | ".join(direcciones),
        "MC": docket,
        "Autoridad Property (Except HHG)": autoridad_estado,
        "Resultado": "✅ Válido" if (ok_dot and ok_auth and legal) else "❌ Revisar",
        "_ok_dot": ok_dot,
        "_ok_auth": ok_auth,
        "_varias_dir": len(direcciones) > 1,
    }


# ---------------- UI ----------------
st.set_page_config(page_title="Validador USDOT", page_icon="🚚", layout="wide")
st.title("🚚 Validador USDOT (Motus / FMCSA)")

entrada = st.text_area("Número(s) USDOT — uno por línea", "3692487", height=100)

if st.button("Validar", type="primary"):
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
            if r["_varias_dir"]:
                st.caption("⚠️ Este carrier tiene más de una dirección (física y de correo). "
                           "Confirmá en Motus cuál es la Principal Place of Business.")

    if len(resultados) > 1:
        tabla = pd.DataFrame(resultados).drop(columns=["_ok_dot", "_ok_auth", "_varias_dir"],
                                              errors="ignore")
        st.dataframe(tabla, use_container_width=True, hide_index=True)
