"""STT Logistics Group · BackOffice — Pre-verificación de documentos (BCA)."""
import base64
import html
import re
from collections import Counter
from datetime import datetime
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import streamlit as st

import stt_core as core

RAIZ = Path(__file__).parent
VERSION = "2.1.0"  # Cambiala en cada entrega y anotala en bitacora/REGISTRO.md
ZONA = ZoneInfo("America/Guatemala")

st.set_page_config(page_title="STT BackOffice", page_icon=str(RAIZ / "assets" / "stt_icon.png"),
                   layout="wide", initial_sidebar_state="collapsed")

e = html.escape


# ============================================================
# Estilo
# ============================================================
CSS = """
<style>
@import url('https://fonts.googleapis.com/css2?family=Barlow+Condensed:ital,wght@0,600;0,700;1,800&family=Barlow:wght@400;500;600;700&display=swap');
:root{--ink:#000;--red:#BB202C;--red-wash:#FBEDEE;--paper:#fff;--road:#F3F4F5;--rule:#D9DCDF;
      --muted:#5A5F66;--go:#1E7B3C;--go-bright:#3DBE6A;--caution:#9A6200;--caution-wash:#FFF5E0}
.stApp, .stApp p, .stApp li, .stApp label, .stApp input, .stApp textarea, .stApp button, .stApp td, .stApp th{
  font-family:'Barlow',system-ui,sans-serif}
header[data-testid="stHeader"]{background:transparent}
.block-container{padding-top:1.2rem;max-width:1240px}
.stTabs [role="tab"] p{font-family:'Barlow Condensed',sans-serif;font-weight:700;font-size:19px}
.stTabs [role="tablist"]{gap:28px;border-bottom:1px solid var(--rule)}
div[data-testid="stForm"]{border:1px solid var(--rule);border-radius:4px;background:var(--road);padding:18px 20px 6px}
.stButton button, div[data-testid="stFormSubmitButton"] button{border-radius:3px;font-weight:700;font-size:16px;min-height:44px}

/* Encabezado */
.stt-top{display:flex;align-items:center;gap:26px;padding:6px 0 14px;border-bottom:4px solid var(--red)}
.stt-top img{height:58px;width:auto}
.stt-top .t1{font-family:'Barlow Condensed',sans-serif;font-weight:700;font-size:32px;line-height:1;color:var(--ink)}
.stt-top .t2{color:var(--muted);font-size:16px;margin-top:3px}

/* Intro */
.intro{max-width:70ch;color:var(--muted);font-size:17px;margin:4px 0 14px}
.pasos{display:grid;grid-template-columns:repeat(auto-fit,minmax(170px,1fr));gap:0;border-top:2px solid var(--ink);margin-top:26px}
.paso{padding:14px 18px 16px 0}
.paso b{display:block;font-family:'Barlow Condensed',sans-serif;font-size:20px;font-weight:700}
.paso span{color:var(--muted);font-size:15px}

/* Veredicto: la pieza protagonista, con las líneas de velocidad del logo */
.verdict{display:grid;grid-template-columns:1fr auto;gap:28px;align-items:center;
  padding:30px 34px 28px;margin:22px 0 0;color:#fff}
.verdict--ok{background:var(--ink)}
.verdict--stop{background:var(--red)}
.v-stamp{font-family:'Barlow Condensed',sans-serif;font-style:italic;font-weight:800;
  font-size:clamp(38px,5.6vw,72px);line-height:.92;letter-spacing:-.5px;display:flex;align-items:center;gap:18px}
.v-speed{flex:0 0 auto;width:clamp(40px,6vw,84px);height:.46em;transform:skewX(-14deg);
  background:repeating-linear-gradient(to bottom,var(--speed) 0 5px,transparent 5px 11px)}
.verdict--ok{--speed:var(--go-bright)}
.verdict--stop{--speed:#000}
.v-sub{font-size:18px;margin-top:12px;max-width:62ch;opacity:.95}
.v-code{border:1px solid rgba(255,255,255,.4);padding:14px 20px;min-width:250px}
.v-code small{display:block;font-size:14px;opacity:.8}
.v-code b{display:block;font-family:'Barlow Condensed',sans-serif;font-weight:700;font-size:36px;
  letter-spacing:1.5px;user-select:all;margin:2px 0}
.v-code span{font-size:14px;opacity:.8}

/* Datos del shipment */
.facts{display:grid;grid-template-columns:.9fr 1.7fr 1.1fr 1.8fr .9fr .9fr;border:1px solid var(--rule);border-top:0}
.fact{padding:12px 18px;border-left:1px solid var(--rule)}
.fact:first-child{border-left:0}
.fact span{display:block;font-size:14px;color:var(--muted)}
.fact b{font-family:'Barlow Condensed',sans-serif;font-weight:600;font-size:21px;line-height:1.15;overflow-wrap:break-word}

/* Acciones para poder enviar */
.acciones{border-left:5px solid var(--red);background:var(--red-wash);padding:16px 22px 8px;margin-top:22px}
.acciones h3{font-family:'Barlow Condensed',sans-serif;font-weight:700;font-size:24px;margin:0 0 6px;padding:0}
.acciones ol{margin:0;padding-left:22px}
.acciones li{margin:0 0 10px;font-size:16.5px}
.acciones a{color:var(--red);font-weight:700;margin-left:6px;white-space:nowrap}

/* Checklist */
.grp{margin-top:24px}
.grp-h{display:flex;justify-content:space-between;align-items:baseline;border-bottom:2px solid var(--ink);padding-bottom:5px}
.grp-h b{font-family:'Barlow Condensed',sans-serif;font-weight:700;font-size:22px}
.grp-h span{font-size:14.5px;color:var(--muted)}
.chk{display:grid;grid-template-columns:30px 1fr;gap:12px;padding:11px 0;border-bottom:1px solid var(--rule)}
.mark{width:24px;height:24px;border-radius:50%;display:grid;place-items:center;color:#fff;font-weight:700;font-size:14px;margin-top:2px}
.mark--ok{background:var(--go)} .mark--fail{background:var(--red)} .mark--warn{background:var(--caution)} .mark--info{background:#8A9099}
.chk-t{font-weight:600;font-size:16.5px}
.chk--fail .chk-t{color:var(--red)}
.chk-d{color:var(--muted);font-size:15px;margin-top:1px;overflow-wrap:break-word}
.chk-note{margin-top:7px;padding:7px 12px;background:var(--caution-wash);border-left:3px solid var(--caution);font-size:15px}
.chk-note a{color:var(--caution);font-weight:700;margin-left:6px}

/* CRM frente a MOTUS */
.cmp{border:1px solid var(--rule);margin-top:24px}
.cmp-h{background:var(--ink);color:#fff;padding:11px 16px;font-family:'Barlow Condensed',sans-serif;font-weight:700;font-size:21px}
.cmp-row{padding:11px 16px;border-top:1px solid var(--rule)}
.cmp-row:first-of-type{border-top:0}
.cmp-row > span{display:block;font-size:14px;color:var(--muted);margin-bottom:3px}
.cmp-pair{display:grid;grid-template-columns:62px 1fr;gap:2px 8px;font-size:15px}
.cmp-pair i{font-style:normal;color:var(--muted)}
.cmp-bad{color:var(--red);font-weight:700}
.cmp-links{padding:11px 16px;border-top:1px solid var(--rule);font-size:15px}
.cmp-links a{color:var(--red);font-weight:600;margin-right:16px}

/* BackOffice */
.kpis{display:grid;grid-template-columns:repeat(auto-fit,minmax(200px,1fr));border-top:2px solid var(--ink);margin-top:8px}
.kpi{padding:14px 20px 14px 0}
.kpi b{display:block;font-family:'Barlow Condensed',sans-serif;font-weight:700;font-size:46px;line-height:1}
.kpi span{color:var(--muted);font-size:15px}
.kpi--stop b{color:var(--red)}
.bars{margin-top:8px}
.bar{display:grid;grid-template-columns:minmax(160px,320px) 1fr 44px;gap:12px;align-items:center;padding:6px 0;font-size:15.5px}
.bar div{height:14px;background:var(--red)}
.bar em{font-style:normal;font-weight:700;text-align:right}
.ticket{border:1px solid var(--rule);margin-top:14px}
.ticket-h{padding:14px 20px;color:#fff;font-family:'Barlow Condensed',sans-serif;font-style:italic;font-weight:800;font-size:30px}
.ticket dl{display:grid;grid-template-columns:170px 1fr;margin:0;padding:12px 20px;gap:8px 12px;font-size:16px}
.ticket dt{color:var(--muted)}
.ticket dd{margin:0;font-weight:600}

.pie{margin-top:48px;padding-top:12px;border-top:1px solid var(--rule);color:var(--muted);font-size:13.5px}
h2.sec{font-family:'Barlow Condensed',sans-serif;font-weight:700;font-size:26px;margin:30px 0 4px;padding:0}

/* ---------- Responsivo ---------- */
.fact,.chk>div,.cmp-pair>div,.ticket dd,.acciones li{min-width:0;overflow-wrap:break-word}
.verdict>div{min-width:0}

/* Tablets y laptops pequeñas: el checklist y la comparación se apilan, comparación primero */
@media (max-width:1024px){
  div[data-testid="stHorizontalBlock"]:has(.cmp){flex-wrap:wrap;gap:0}
  div[data-testid="stHorizontalBlock"]:has(.cmp)>div[data-testid="stColumn"]{flex:1 1 100% !important;width:100% !important;min-width:100%}
  div[data-testid="stHorizontalBlock"]:has(.cmp)>div[data-testid="stColumn"]:has(.cmp){order:-1}
  .cmp-pair{grid-template-columns:70px 1fr}
}
@media (max-width:900px){.facts{grid-template-columns:1fr 1fr}.fact{border-left:0;border-top:1px solid var(--rule)}
  .fact:nth-child(-n+2){border-top:0}.fact:nth-child(even){border-left:1px solid var(--rule)}}

/* Teléfonos */
@media (max-width:640px){
  .block-container{padding-left:16px;padding-right:16px}
  .verdict{grid-template-columns:1fr;gap:18px;padding:22px 20px}
  .v-stamp{font-size:clamp(32px,10vw,44px);gap:12px}
  .v-sub{font-size:16.5px}
  .v-code{min-width:0}
  .v-code b{font-size:30px}
  .bar{grid-template-columns:1fr 44px}.bar div{grid-column:1/-1;order:3}
  .stTabs [role="tablist"]{gap:14px}
  .stTabs [role="tab"] p{font-size:16px}
  .acciones{padding:14px 16px 6px}
  .acciones a{white-space:normal;margin-left:5px}
  .kpi b{font-size:38px}
}
@media (max-width:480px){
  .stt-top{flex-direction:column;align-items:flex-start;gap:8px}
  .stt-top img{height:42px}.stt-top .t1{font-size:26px}.stt-top .t2{font-size:14.5px}
  .facts{grid-template-columns:1fr}
  .fact,.fact:nth-child(even){border-left:0}
  .fact:nth-child(2){border-top:1px solid var(--rule)}
  .ticket dl{grid-template-columns:1fr;gap:2px}
  .ticket dd{margin-bottom:8px}
  .stTabs [role="tablist"]{gap:14px}
  .stTabs [role="tab"] p{font-size:16px}
}

@media (max-width:360px){
  .stTabs [role="tablist"]{gap:10px}
  .stTabs [role="tab"] p{font-size:15px}
}
/* Pantallas grandes y TV: se agranda todo de forma proporcional */
@media (min-width:1800px){.block-container{zoom:1.2}}
@media (min-width:2300px){.block-container{zoom:1.55}}
@media (min-width:3000px){.block-container{zoom:2}}
</style>
"""
st.markdown(CSS, unsafe_allow_html=True)


def logo_b64() -> str:
    return base64.b64encode((RAIZ / "assets" / "stt_logo.png").read_bytes()).decode()


st.markdown(
    f'<div class="stt-top"><img src="data:image/png;base64,{logo_b64()}" alt="STT Logistics Group">'
    '<div><div class="t1">BackOffice</div>'
    '<div class="t2">Pre-verificación de documentos antes de enviarlos a BackOffice</div></div></div>',
    unsafe_allow_html=True,
)


# ============================================================
# Recursos compartidos
# ============================================================
@st.cache_resource(show_spinner=False)
def crm_compartido() -> core.CRM:
    return core.CRM(st.secrets["STT_EMAIL"], st.secrets["STT_PASSWORD"])


@st.cache_resource(show_spinner=False)
def registro() -> core.Registro:
    return core.Registro(st.secrets.get("SUPABASE_URL"), st.secrets.get("SUPABASE_KEY"))


@st.cache_data(ttl=300, show_spinner=False)
def motus(dot: str):
    return core.motus_consultar(dot)


def numero_shipment(texto: str) -> int | None:
    d = core.digitos(texto)
    return int(d) if d else None


# ============================================================
# Piezas de la pantalla de resultado
# ============================================================
def html_veredicto(r: core.ResultadoBCA, codigo: str | None) -> str:
    ship = e(r.shipment["numero"] if r.shipment else f"S-{r.shipment_id:06d}")
    cumplidos, total = r.requisitos
    avisos = sum(c.estado == "warn" for c in r.checks)
    if r.veredicto == "AUTORIZADO":
        cls, titulo = "ok", "Autorizado para enviar"
        sub = f"La solicitud de BCA de {ship} cumple los {total} requisitos. Ya podés enviarla a BackOffice."
        if avisos == 1:
            sub += " BackOffice va a revisar el punto marcado en amarillo."
        elif avisos > 1:
            sub += f" BackOffice va a revisar los {avisos} puntos marcados en amarillo."
    elif r.veredicto == "YA_EXISTE":
        cls, titulo = "stop", "No enviar: la BCA ya existe"
        sub = "Este carrier ya tiene una BCA firmada con la información actual de MOTUS. No hace falta pedir otra."
    else:
        n = len(r.fallas)
        cls, titulo = "stop", "Todavía no se puede enviar"
        sub = (f"{'Falta 1 requisito' if n == 1 else f'Faltan {n} requisitos'} para la BCA de {ship}. "
               "Corregilos en el CRM y volvé a verificar.")
    caja = ""
    if codigo:
        caja = (f'<div class="v-code"><small>Código de pre-verificación</small><b>{e(codigo)}</b>'
                '<span>Pegalo en tu solicitud a BackOffice</span></div>')
    return (f'<div class="verdict verdict--{cls}"><div><div class="v-stamp"><span class="v-speed"></span>'
            f'<span>{titulo}</span></div><div class="v-sub">{sub}</div></div>{caja}</div>')


def html_datos(r: core.ResultadoBCA) -> str:
    s, a, c = r.shipment or {}, r.asignacion or {}, r.carrier or {}
    origen = ", ".join(x for x in [s.get("origen_ciudad"), s.get("origen_estado")] if x)
    destino = ", ".join(x for x in [s.get("destino_ciudad"), s.get("destino_estado")] if x)
    datos = [
        ("Shipment", s.get("numero") or f"S-{r.shipment_id:06d}"),
        ("Ruta", f"{origen} → {destino}" if origen or destino else "Sin ruta"),
        ("Driver Assignment", a.get("da_nombre") or "Sin asignar"),
        ("Carrier", c.get("nombre") or a.get("carrier_nombre") or "Sin carrier"),
        ("DOT", c.get("dot") or "—"),
        ("MC", c.get("mc") or "Sin MC"),
    ]
    return '<div class="facts">' + "".join(
        f'<div class="fact"><span>{e(k)}</span><b>{e(v)}</b></div>' for k, v in datos) + "</div>"


def html_acciones(r: core.ResultadoBCA) -> str:
    if not r.fallas:
        return ""
    items = []
    for c in r.fallas:
        link = f'<a href="{e(c.link)}" target="_blank">{e(c.link_texto)}</a>' if c.link else ""
        items.append(f"<li>{e(c.solucion or c.detalle)}{link}</li>")
    titulo = "Qué hacer" if r.veredicto == "YA_EXISTE" else "Para poder enviar"
    return f'<div class="acciones"><h3>{titulo}</h3><ol>{"".join(items)}</ol></div>'


MARCAS = {"ok": "✓", "fail": "✕", "warn": "!", "info": "–"}


def html_checklist(r: core.ResultadoBCA) -> str:
    partes = []
    for g in core.ORDEN_GRUPOS:
        cs = [c for c in r.checks if c.grupo == g]
        if not cs:
            continue
        evaluados = [c for c in cs if c.estado != "info"]
        bien = sum(c.estado != "fail" for c in evaluados)
        filas = []
        for c in cs:
            nota = ""
            if c.estado == "warn" and c.solucion:
                link = f'<a href="{e(c.link)}" target="_blank">{e(c.link_texto)}</a>' if c.link else ""
                nota = f'<div class="chk-note">{e(c.solucion)}{link}</div>'
            filas.append(
                f'<div class="chk chk--{c.estado}"><div class="mark mark--{c.estado}">{MARCAS[c.estado]}</div>'
                f'<div><div class="chk-t">{e(c.titulo)}</div><div class="chk-d">{e(c.detalle)}</div>{nota}</div></div>')
        resumen = f"{bien} de {len(evaluados)}" if evaluados else ""
        partes.append(f'<div class="grp"><div class="grp-h"><b>{e(g)}</b><span>{resumen}</span></div>{"".join(filas)}</div>')
    return "".join(partes)


def html_comparacion(r: core.ResultadoBCA) -> str:
    c, m = r.carrier, r.motus
    if not c or not m:
        return ""
    dir_ok = core.direccion_igual(c["direccion"], m)
    auth = core.motus_property(m, core.digitos(c["mc"]) or None)
    mc_motus = f"{auth['docket']}: {auth['estado']}" if auth else "Sin autoridad Property"

    def fila(etiqueta, crm_v, motus_v, ok):
        cls = "" if ok else ' class="cmp-bad"'
        return (f'<div class="cmp-row"><span>{e(etiqueta)}</span><div class="cmp-pair">'
                f'<i>CRM</i><div{cls}>{e(crm_v or "vacío")}</div><i>MOTUS</i><div>{e(motus_v or "—")}</div></div></div>')

    filas = [
        fila("Nombre", c["nombre"], m["legal"], core.norm(c["nombre"]) == core.norm(m["legal"])),
        fila("Principal Place of Business", c["direccion"], m["direccion"], dir_ok),
        fila("USDOT", c["dot"], m["estado_dot"] + (" (Out of Service)" if m["fuera_servicio"] else ""), m["dot_activo"]),
        fila("MC", c["mc"] or "Sin MC", mc_motus, (not core.digitos(c["mc"])) or bool(auth and auth["estado"] == "Active")),
    ]
    dot = core.digitos(c["dot"])
    links = (f'<div class="cmp-links"><a href="{core.MOTUS_WEB.format(dot=dot)}" target="_blank">Ver en MOTUS</a>'
             f'<a href="{core.CRM_URL}/Admin/Carrier/Details/{c["_id"]}" target="_blank">Ver Carrier en el CRM</a></div>')
    return f'<div class="cmp"><div class="cmp-h">CRM frente a MOTUS</div>{"".join(filas)}{links}</div>'


def guardar_en_registro(r: core.ResultadoBCA, codigo: str | None, quien: str) -> str | None:
    reg = registro()
    if not reg.activo:
        return None
    try:
        reg.guardar({
            "documento": "BCA",
            "shipment": (r.shipment or {}).get("numero") or f"S-{r.shipment_id:06d}",
            "driver_assignment": (r.asignacion or {}).get("da_nombre"),
            "carrier": (r.carrier or {}).get("nombre"),
            "dot": (r.carrier or {}).get("dot"),
            "mc": (r.carrier or {}).get("mc"),
            "resultado": r.veredicto,
            "codigo": codigo,
            "motivos": [c.titulo for c in r.fallas],
            "solicitado_por": quien or None,
        })
    except Exception as ex:
        return f"No se pudo guardar la verificación en el registro: {ex}"
    return None


# ============================================================
# Pestañas
# ============================================================
tab_bca, tab_bo, tab_dot = st.tabs(["Verificar BCA", "Validar código", "Consulta USDOT"])

# ---------------- Pre-verificación BCA ----------------
with tab_bca:
    with st.form("form_bca", border=True):
        c1, c2, c3 = st.columns([2, 2, 1.2], vertical_alignment="bottom")
        texto_ship = c1.text_input("Shipment", placeholder="S-039981")
        quien = c2.text_input("Broker o dispatcher que pide la BCA", placeholder="Tu nombre y apellido")
        verificar = c3.form_submit_button("Verificar BCA", type="primary", use_container_width=True)

    if verificar:
        sid = numero_shipment(texto_ship)
        if not sid:
            st.error("Escribí el número de Shipment, por ejemplo S-039981.")
        elif "STT_EMAIL" not in st.secrets or "STT_PASSWORD" not in st.secrets:
            st.error("La app no tiene configurado el acceso al CRM. Faltan STT_EMAIL y STT_PASSWORD en los secrets.")
        else:
            try:
                with st.spinner("Revisando el Shipment, el Driver Assignment, el Carrier y MOTUS…"):
                    resultados = core.pre_verificar_bca(crm_compartido(), sid, motus_fn=motus)
            except core.CRMError as ex:
                st.error(str(ex))
                resultados = None
            except requests.RequestException as ex:
                st.error(f"No se pudo conectar con el CRM o con MOTUS. Intentá de nuevo en un momento. ({ex})")
                resultados = None
            if resultados is not None:
                paquete = []
                for r in resultados:
                    codigo = core.nuevo_codigo(sid) if r.veredicto == "AUTORIZADO" else None
                    aviso = guardar_en_registro(r, codigo, quien.strip())
                    paquete.append({"r": r, "codigo": codigo, "aviso": aviso})
                st.session_state["bca"] = paquete

    paquete = st.session_state.get("bca")
    if not paquete:
        st.markdown(
            '<p class="intro">Escribí el número de Shipment antes de pedir la BCA. La app revisa el Driver '
            'Assignment, el Carrier y MOTUS con el mismo procedimiento que usa BackOffice, y te dice si ya '
            'podés enviar la solicitud o qué tenés que corregir primero.</p>'
            '<div class="pasos">'
            '<div class="paso"><b>Driver Assignment</b><span>Driver asignado, en Dispatched y con email</span></div>'
            '<div class="paso"><b>Carrier</b><span>Company Name, DOT, MC y dirección completos</span></div>'
            '<div class="paso"><b>MOTUS</b><span>USDOT y MC activos, nombre y Principal Place of Business idénticos</span></div>'
            '<div class="paso"><b>Ruta</b><span>Sin MC activo, la carga no puede salir del estado</span></div>'
            '<div class="paso"><b>BCA existente</b><span>Si ya hay una firmada y vigente, no se envía otra</span></div>'
            '</div>',
            unsafe_allow_html=True,
        )
    else:
        for item in paquete:
            r = item["r"]
            st.markdown(html_veredicto(r, item["codigo"]) + html_datos(r) + html_acciones(r),
                        unsafe_allow_html=True)
            if item["aviso"]:
                st.warning(item["aviso"])
            if item["codigo"] and not registro().activo:
                st.caption("El registro de códigos todavía no está configurado, así que BackOffice no podrá "
                           "verificar este código desde la app.")
            izq, der = st.columns([3, 2], gap="large")
            izq.markdown(html_checklist(r), unsafe_allow_html=True)
            der.markdown(html_comparacion(r), unsafe_allow_html=True)
            st.write("")

# ---------------- Verificar código (BackOffice) ----------------
with tab_bo:
    reg = registro()
    with st.form("form_codigo", border=True):
        c1, c2 = st.columns([3, 1.2], vertical_alignment="bottom")
        codigo_in = c1.text_input("Código de pre-verificación", placeholder="BCA-39981-K7QM")
        buscar = c2.form_submit_button("Validar código", type="primary", use_container_width=True)

    if buscar:
        cod = codigo_in.strip().upper()
        if not reg.activo:
            st.error("El registro no está configurado. Agregá SUPABASE_URL y SUPABASE_KEY en los secrets.")
        elif not re.fullmatch(r"BCA-\d+-[A-Z0-9]{4}", cod):
            st.error("El código tiene este formato: BCA-39981-K7QM.")
        else:
            try:
                fila = reg.buscar(cod)
            except requests.RequestException as ex:
                st.error(f"No se pudo consultar el registro: {ex}")
                fila = None
            else:
                if not fila:
                    st.markdown(
                        '<div class="ticket"><div class="ticket-h" style="background:var(--red)">Código no válido</div>'
                        f'<dl><dt>Código</dt><dd>{e(cod)}</dd><dt>Qué significa</dt>'
                        '<dd>La app nunca generó este código. La solicitud no pasó la pre-verificación.</dd></dl></div>',
                        unsafe_allow_html=True)
                else:
                    cuando = datetime.fromisoformat(fila["created_at"].replace("Z", "+00:00")).astimezone(ZONA)
                    st.markdown(
                        '<div class="ticket"><div class="ticket-h" style="background:var(--ink)">Código válido</div><dl>'
                        f'<dt>Código</dt><dd>{e(fila["codigo"])}</dd>'
                        f'<dt>Shipment</dt><dd>{e(fila["shipment"] or "")}</dd>'
                        f'<dt>Driver Assignment</dt><dd>{e(fila.get("driver_assignment") or "")}</dd>'
                        f'<dt>Carrier</dt><dd>{e(fila.get("carrier") or "")}</dd>'
                        f'<dt>DOT y MC</dt><dd>{e(fila.get("dot") or "")}, {e(fila.get("mc") or "sin MC")}</dd>'
                        f'<dt>Verificado</dt><dd>{cuando:%d/%m/%Y %H:%M} (hora de Guatemala)</dd>'
                        f'<dt>Pedido por</dt><dd>{e(fila.get("solicitado_por") or "No indicado")}</dd>'
                        '</dl></div>',
                        unsafe_allow_html=True)
                    st.caption("El código confirma que todo estaba en orden al momento de verificar. "
                               "Si pasó tiempo, podés volver a verificar el Shipment en la primera pestaña.")

    st.markdown('<h2 class="sec">Últimos 30 días</h2>', unsafe_allow_html=True)
    if not reg.activo:
        st.caption("Cuando se configure el registro en Supabase, aquí vas a ver cuántas solicitudes de BCA "
                   "se frenaron antes de llegar a BackOffice y por qué.")
    else:
        try:
            filas = reg.recientes(30)
        except requests.RequestException as ex:
            st.error(f"No se pudo leer el registro: {ex}")
            filas = []
        total = len(filas)
        frenadas = sum(f["resultado"] != "AUTORIZADO" for f in filas)
        autorizadas = total - frenadas
        st.markdown(
            '<div class="kpis">'
            f'<div class="kpi"><b>{total}</b><span>Verificaciones de BCA</span></div>'
            f'<div class="kpi kpi--stop"><b>{frenadas}</b><span>Frenadas antes de llegar a BackOffice</span></div>'
            f'<div class="kpi"><b>{autorizadas}</b><span>Autorizadas para enviar</span></div>'
            '</div>', unsafe_allow_html=True)
        motivos = Counter(m for f in filas for m in (f.get("motivos") or []))
        if motivos:
            maximo = max(motivos.values())
            barras = "".join(
                f'<div class="bar"><span>{e(m)}</span><div style="width:{100 * n / maximo:.0f}%"></div><em>{n}</em></div>'
                for m, n in motivos.most_common(8))
            st.markdown(f'<h2 class="sec">Motivos más frecuentes</h2><div class="bars">{barras}</div>',
                        unsafe_allow_html=True)

# ---------------- Consulta USDOT ----------------
with tab_dot:
    with st.form("form_dot", border=True):
        c1, c2 = st.columns([3, 1.2], vertical_alignment="bottom")
        entrada = c1.text_area("Números USDOT, uno por línea", "3692487", height=90)
        consultar = c2.form_submit_button("Consultar MOTUS", type="primary", use_container_width=True)

    if consultar:
        dots = list(dict.fromkeys(re.findall(r"\d{4,8}", entrada)))
        if not dots:
            st.error("Escribí al menos un número USDOT.")
        else:
            filas = []
            with st.spinner("Consultando MOTUS…"):
                for dot in dots:
                    try:
                        data = motus(dot)
                    except requests.RequestException as ex:
                        filas.append({"USDOT": dot, "Estado USDOT": f"Error de conexión: {ex}"})
                        continue
                    if not data:
                        filas.append({"USDOT": dot, "Estado USDOT": "No existe en MOTUS"})
                        continue
                    m = core.motus_resumen(data)
                    a = core.motus_property(m)
                    filas.append({
                        "USDOT": dot,
                        "Estado USDOT": m["estado_dot"] + (" (Out of Service)" if m["fuera_servicio"] else ""),
                        "Legal Business Name": m["legal"],
                        "Principal Place of Business": m["direccion"],
                        "MC": a["docket"] if a else "",
                        "Motor Carrier of Property": a["estado"] if a else "Sin autoridad",
                    })
            st.dataframe(pd.DataFrame(filas), use_container_width=True, hide_index=True)

st.markdown(f'<div class="pie">STT Logistics Group, BackOffice. Versión {VERSION}</div>', unsafe_allow_html=True)
