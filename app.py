"""STT Logistics Group · BackOffice — Pre-verificación de documentos (BCA).

Todo lo visible está en inglés de EE. UU. Las fechas y horas se muestran en hora de Guatemala.
"""
import base64
import json
import html
import re
from collections import Counter
from datetime import datetime, timezone
from pathlib import Path
from zoneinfo import ZoneInfo

import pandas as pd
import requests
import streamlit as st

import stt_core as core

RAIZ = Path(__file__).parent
VERSION = "2.5.0"  # Cambiala en cada entrega y anotala en bitacora/REGISTRO.md
ZONA = ZoneInfo("America/Guatemala")

st.set_page_config(page_title="STT BackOffice", page_icon=str(RAIZ / "assets" / "stt_icon.png"),
                   layout="wide", initial_sidebar_state="collapsed")

e = html.escape


def hora_gt(momento: datetime) -> str:
    """Fecha y hora en Guatemala con formato de EE. UU.: Oct 2, 2026, 3:22 PM."""
    d = momento.astimezone(ZONA)
    return f"{d:%b} {d.day}, {d.year}, {d.hour % 12 or 12}:{d:%M} {d:%p}"


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
.verdict--wait{background:#3A3F45}
.v-stamp{font-family:'Barlow Condensed',sans-serif;font-style:italic;font-weight:800;
  font-size:clamp(38px,5.6vw,72px);line-height:.92;letter-spacing:-.5px;display:flex;align-items:center;gap:18px}
.v-speed{flex:0 0 auto;width:clamp(40px,6vw,84px);height:.46em;transform:skewX(-14deg);
  background:repeating-linear-gradient(to bottom,var(--speed) 0 5px,transparent 5px 11px)}
.verdict--ok{--speed:var(--go-bright)}
.verdict--stop{--speed:#000}
.verdict--wait{--speed:#F2B233}
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
.mark--ok{background:var(--go)} .mark--fail{background:var(--red)} .mark--warn{background:var(--caution)} .mark--info{background:#8A9099} .mark--covered{background:var(--ink)}
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
h3.sub{font-family:'Barlow Condensed',sans-serif;font-weight:700;font-size:22px;margin:22px 0 2px;padding:0}
.bars-h{font-weight:600;color:var(--muted);margin:18px 0 2px}
h2.sec{font-family:'Barlow Condensed',sans-serif;font-weight:700;font-size:26px;margin:30px 0 4px;padding:0}

/* Banda de cumplimiento de políticas (verde / amarillo / rojo) */
.policy{display:grid;grid-template-columns:minmax(210px,280px) 1fr;gap:10px 34px;align-items:start;
  padding:20px 28px 22px;border-left:8px solid var(--pc);background:var(--pw)}
.policy--green{--pc:var(--go);--pw:#E9F5ED}
.policy--yellow{--pc:var(--caution);--pw:var(--caution-wash)}
.policy--red{--pc:var(--red);--pw:var(--red-wash)}
.policy-h b{display:block;font-family:'Barlow Condensed',sans-serif;font-weight:700;font-size:27px;line-height:1.05;color:var(--pc)}
.policy-h span{display:block;font-size:15px;color:var(--muted);margin-top:4px}
.meter{height:8px;background:rgba(0,0,0,.09);margin-top:10px}
.meter i{display:block;height:100%;background:var(--pc)}
.policy p{margin:0;font-size:17px;line-height:1.55;max-width:78ch;color:#1C1C1C}
.facts--people{grid-template-columns:1fr 1fr 1.3fr 1.3fr}
.facts--people .fact b{font-size:19px}

/* Política vigente: barra fija arriba del formulario */
.pol{display:flex;align-items:center;gap:16px;padding:14px 18px;margin:4px 0 14px;border:1px solid var(--rule);border-left:5px solid var(--ink);background:#fff}
.pol-doc{flex:0 0 auto;width:30px;height:38px;background:linear-gradient(var(--red) 0 7px,#fff 7px);border:2px solid var(--ink);
  clip-path:polygon(0 0,70% 0,100% 24%,100% 100%,0 100%)}
.pol-txt{flex:1 1 auto;min-width:0}
.pol-txt b{display:block;font-family:'Barlow Condensed',sans-serif;font-weight:700;font-size:21px;line-height:1.1}
.pol-txt span{display:block;font-size:15px;color:var(--muted);margin-top:2px}
a.pol-btn{flex:0 0 auto;display:inline-block;padding:10px 16px;background:var(--ink);color:#fff !important;text-decoration:none;
  font-weight:700;font-size:15px;border-radius:3px;white-space:nowrap}
a.pol-btn:hover,a.pol-btn:focus-visible{background:var(--red)}
.policy p a,.acciones .pol-ref a{color:inherit;font-weight:700}
.acciones .pol-ref{margin:4px 0 10px;font-size:15px;color:var(--muted)}
.acciones .pol-ref a{margin-left:0;white-space:normal}
.chk-d a{color:var(--red);font-weight:600;margin-left:6px;white-space:nowrap}
.hist{width:100%;border-collapse:collapse;font-size:15px}
.hist th{text-align:left;font-weight:600;color:var(--muted);border-bottom:2px solid var(--ink);padding:6px 10px 6px 0}
.hist td{border-bottom:1px solid var(--rule);padding:8px 10px 8px 0;vertical-align:top}
.hist a{color:var(--red);font-weight:600}
@media (max-width:640px){.pol{flex-wrap:wrap}.pol-txt{flex-basis:calc(100% - 50px)}a.pol-btn{width:100%;text-align:center}}

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
@media (max-width:900px){.policy{grid-template-columns:1fr;padding:18px 20px}
  .facts,.facts.facts--people{grid-template-columns:1fr 1fr}.fact{border-left:0;border-top:1px solid var(--rule)}
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
  .facts,.facts.facts--people{grid-template-columns:1fr}
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
    '<div class="t2">Document pre-verification before requests reach BackOffice</div></div></div>',
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


@st.cache_data(show_spinner=False)
def catalogo_politicas() -> dict:
    return json.loads((RAIZ / "politicas.json").read_text(encoding="utf-8"))


def fecha_larga(iso: str) -> str:
    d = datetime.strptime(iso, "%Y-%m-%d")
    return f"{d:%B} {d.day}, {d.year}"


def politica(clave: str) -> dict | None:
    """Versión vigente de una política (la primera de la lista), con su URL pública.
    None si todavía no hay un documento publicado."""
    p = catalogo_politicas().get(clave)
    if not p or not p.get("versions"):
        return None
    v = p["versions"][0]
    return {**p, **v, "url": f"./app/static/{v['file']}", "fecha": fecha_larga(v["effective"]),
            "etiqueta": f"{p['title']} v{v['version']}"}


def plural(n: int, singular: str, plural_: str | None = None) -> str:
    return f"{n} {singular if n == 1 else (plural_ or singular + 's')}"


# ============================================================
# Quién pide la solicitud
# ============================================================
def personas(r, manual: str) -> dict:
    s = r.shipment or {}
    owner, dispatcher = (s.get("owner") or "").strip(), (s.get("dispatcher") or "").strip()
    if manual:
        solicitante, origen = manual, "entered manually"
    elif owner:
        solicitante, origen = owner, "Shipment Owner"
    else:
        solicitante, origen = dispatcher, "Dispatcher" if dispatcher else ""
    # A quién se le habla en la banda de políticas
    if manual:
        saludo = manual.split()[0]
    else:
        nombres = [n.split()[0] for n in dict.fromkeys([owner, dispatcher]) if n]
        saludo = " and ".join(nombres)
    return {"owner": owner, "dispatcher": dispatcher, "solicitante": solicitante,
            "origen": origen, "saludo": saludo}


# ============================================================
# Piezas de la pantalla de resultado
# ============================================================
# Configuración de cada documento que la app pre-verifica
DOCS = {
    "BCA": {
        "corto": "BCA", "largo": "BCA", "pestana": "Verify BCA", "boton": "Verify BCA",
        "spinner": "Checking the shipment, Driver Assignment, carrier, and MOTUS…",
        "procedimiento": "BackOffice's BCA verification procedure",
        "requisitos_rojo": "verified driver, carrier, and route information",
        "intro": "Enter the shipment number before you request a BCA. The app checks the Driver Assignment, the "
                 "carrier, and MOTUS using the same procedure BackOffice follows, then tells you whether you can "
                 "submit the request or what to fix first.",
        "pasos": [("Driver Assignment", "Driver assigned, Dispatched, and with an email"),
                  ("Carrier", "Company name, DOT, MC, and address complete"),
                  ("MOTUS", "Active USDOT and MC, with identical name and Principal Place of Business"),
                  ("Route", "Without an active MC, the load cannot leave the state"),
                  ("Existing BCA", "If a signed, current BCA is on file, or the driver accepted the terms in the STT app, no new one is sent")],
    },
    "LC": {
        "corto": "LC", "largo": "Load Confirmation", "pestana": "Verify LC", "boton": "Verify LC",
        "spinner": "Checking the shipment, loads, payment, Driver Assignment, carrier documents, and MOTUS…",
        "procedimiento": "BackOffice's Load Confirmation procedure",
        "requisitos_rojo": "complete shipment, load, payment, driver, and carrier information",
        "intro": "Enter the shipment number before you request a Load Confirmation. The app checks Shipment Info, "
                 "Special Instructions, the loads, the payment, the Driver Assignment, the carrier's documents, and "
                 "MOTUS, then tells you whether you can submit the request or what to fix first.",
        "pasos": [("Shipment and Special Instructions", "Dates, driver name and phone, and broker contact match"),
                  ("Loads", "Type, quantity, description, year, and dimensions complete"),
                  ("Payment", "Amounts match the payment type, which decides the LC template"),
                  ("Driver and carrier", "Same checks as the BCA, plus the driver's phone"),
                  ("Documents", "Signed BCA (or terms accepted in the app), valid COI, and driver's license")],
    },
}


def html_veredicto(r, codigo: str | None, cuando: str) -> str:
    cfg = DOCS[r.documento]
    ship = e(r.shipment["numero"] if r.shipment else f"S-{r.shipment_id:06d}")
    _, total = r.requisitos
    avisos = sum(c.estado == "warn" for c in r.checks)
    if r.veredicto == "SIN_VERIFICAR":
        titulo = "Could not finish the check"
        sub = ("MOTUS did not respond to the app, so the carrier could not be verified and no code was issued. "
               "This is not a problem with your request. Try again in a few minutes.")
        return (f'<div class="verdict verdict--wait"><div><div class="v-stamp"><span class="v-speed"></span>'
                f'<span>{titulo}</span></div><div class="v-sub">{sub}</div></div></div>')
    if r.veredicto == "AUTORIZADO":
        cls, titulo = "ok", "Approved to send"
        sub = (f"The {cfg['largo']} request for {ship} meets all {total} automatic checks. "
               "You can now submit it to BackOffice.")
        if avisos == 1:
            sub += " BackOffice will review the item marked in yellow."
        elif avisos > 1:
            sub += f" BackOffice will review the {avisos} items marked in yellow."
        if any(c.grupo == core.GRUPO_MANUAL for c in r.checks):
            sub += " BackOffice still reviews the items listed under “BackOffice reviews manually.”"
    elif r.veredicto == "YA_EXISTE":
        if r.terminos_app:
            cls, titulo = "stop", "Do not send: signed in app"
            sub = (f"{e(r.terminos_app['nombre'])} already accepted the terms in the STT app (Terms Status: True), "
                   "which counts as a signed BCA. No new request is needed.")
        else:
            cls, titulo = "stop", "Do not send: BCA on file"
            sub = ("This carrier already has a signed BCA that matches current MOTUS information. "
                   "No new request is needed.")
    else:
        n = len(r.fallas)
        cls, titulo = "stop", "Not ready to send"
        cumplidos, total_req = r.cumplimiento[1], r.cumplimiento[2]
        if n == 1:
            sub = f"1 item must be fixed before the {cfg['largo']} on {ship} can be sent. Fix it in the CRM and verify again."
        else:
            sub = f"{n} items must be fixed before the {cfg['largo']} on {ship} can be sent. Fix them in the CRM and verify again."
        if total_req - cumplidos > n:
            sub += " The remaining requirements will be checked once these are fixed."
    caja = ""
    if codigo:
        caja = (f'<div class="v-code"><small>Pre-verification code</small><b>{e(codigo)}</b>'
                f'<span>Paste it into your request to BackOffice.<br>Issued {e(cuando)} (Guatemala time)</span></div>')
    return (f'<div class="verdict verdict--{cls}"><div><div class="v-stamp"><span class="v-speed"></span>'
            f'<span>{titulo}</span></div><div class="v-sub">{sub}</div></div>{caja}</div>')


def html_politicas(r, gente: dict) -> str:
    """Banda de cumplimiento de políticas. Es genérica para todos los documentos."""
    if r.veredicto == "SIN_VERIFICAR":
        return ""   # sin MOTUS no se puede medir el cumplimiento; no es culpa del solicitante
    cfg = DOCS[r.documento]
    nivel, cumplidos, total = r.cumplimiento
    nombre = gente["saludo"]
    pendientes = len([c for c in r.fallas if c.grupo != core.GRUPO_BCA])
    inicio = f"{e(nombre)}, this" if nombre else "This"

    if nivel == "green":
        titulo = "Fully compliant"
        gracias = f"Thank you, {e(nombre)}." if nombre else "Thank you."
        if r.veredicto == "YA_EXISTE":
            texto = (f"{gracias} Everything on this shipment is in order, and checking first saved an "
                     "unnecessary request. Following STT's document policy keeps loads moving and helps "
                     "BackOffice focus on the requests that need it.")
        else:
            texto = (f"{gracias} This request meets every requirement in STT's document policy and standard "
                     "operating procedures. Following the process keeps your loads moving and helps BackOffice "
                     "respond faster. We appreciate your attention to detail.")
    elif nivel == "yellow":
        titulo = "Partially compliant"
        texto = (f"{inicio} request is partially complete. Under STT's document policies and standard operating "
                 f"procedures, a {cfg['largo']} request must include complete and accurate information before it is "
                 f"submitted. {plural(pendientes, 'item')} still {'needs' if pendientes == 1 else 'need'} "
                 "your attention. Once you correct them in the CRM and verify again, you will receive your "
                 "pre-verification code right away.")
    else:
        titulo = "Not compliant"
        texto = (f"{inicio} request does not meet STT's document requirements. Company policy and our standard "
                 f"operating procedures require every {cfg['largo']} request to include {cfg['requisitos_rojo']} "
                 "before it reaches BackOffice. Requests submitted without it are returned, which "
                 "delays the load for you, your customer, and the carrier. Please complete the items below before "
                 "submitting. If anything is unclear, BackOffice is glad to help.")

    pol = politica(r.documento)
    if pol is None:
        enlace_politica = f"Every requirement comes from {cfg['procedimiento']}."
    elif nivel == "green":
        enlace_politica = (f'You can review the <a href="{pol["url"]}" target="_blank">{e(pol["title"])}</a> '
                           f'(updated {pol["fecha"]}) at any time.')
    else:
        enlace_politica = (f'Every requirement comes from the <a href="{pol["url"]}" target="_blank">'
                           f'{e(pol["title"])}</a>, updated {pol["fecha"]}.')
    pct = round(100 * cumplidos / total) if total else 0
    return (f'<div class="policy policy--{nivel}"><div class="policy-h"><b>{titulo}</b>'
            f'<span>{cumplidos} of {total} requirements met</span>'
            f'<div class="meter" role="img" aria-label="{pct}% of requirements met"><i style="width:{pct}%"></i></div></div>'
            f'<p>{texto} {enlace_politica}</p></div>')


def html_datos(r, gente: dict, cuando: str) -> str:
    s, a, c = r.shipment or {}, r.asignacion or {}, r.carrier or {}
    origen = ", ".join(x for x in [s.get("origen_ciudad"), s.get("origen_estado")] if x)
    destino = ", ".join(x for x in [s.get("destino_ciudad"), s.get("destino_estado")] if x)
    datos = [
        ("Shipment", s.get("numero") or f"S-{r.shipment_id:06d}"),
        ("Route", f"{origen} → {destino}" if origen or destino else "No route"),
        ("Driver Assignment", a.get("da_nombre") or "Not assigned"),
        ("Carrier", c.get("nombre") or a.get("carrier_nombre") or "No carrier"),
        ("DOT", c.get("dot") or "—"),
        ("MC", c.get("mc") or "No MC"),
    ]
    solicitante = gente["solicitante"] or "Not identified"
    if gente["origen"] and gente["solicitante"]:
        solicitante += f" ({gente['origen']})"
    gente_datos = [
        ("Shipment Owner", gente["owner"] or "Not set"),
        ("Dispatcher", gente["dispatcher"] or "Not set"),
        ("Requested by", solicitante),
        ("Verified", f"{cuando} (Guatemala time)"),
    ]
    fila = lambda items, extra="": f'<div class="facts {extra}">' + "".join(
        f'<div class="fact"><span>{e(k)}</span><b>{e(v)}</b></div>' for k, v in items) + "</div>"
    html_ = fila(datos) + fila(gente_datos, "facts--people")
    if r.documento == "LC" and r.shipment:
        html_ += fila([
            ("LC to send", r.plantilla or "Unknown"),
            ("Payment type", s.get("pago_tipo") or "Not set"),
            ("Case", r.caso),
            ("Loads", ", ".join(l.get("numero", "") for l in r.loads) or "None"),
        ], "facts--people")
    return html_


def html_acciones(r) -> str:
    if not r.fallas:
        return ""
    cfg = DOCS[r.documento]
    items = []
    for c in r.fallas:
        link = f'<a href="{e(c.link)}" target="_blank">{e(c.link_texto)}</a>' if c.link else ""
        items.append(f"<li>{e(c.solucion or c.detalle)}{link}</li>")
    titulo = "What to do" if r.veredicto == "YA_EXISTE" else "To send this request"
    pol = politica(r.documento)
    if pol:
        ref = (f'<div class="pol-ref">Based on the <a href="{pol["url"]}" target="_blank">{e(pol["title"])}</a>, '
               f'version {e(pol["version"])}, updated {pol["fecha"]}.</div>')
    else:
        ref = f'<div class="pol-ref">Based on {cfg["procedimiento"]}.</div>'
    return f'<div class="acciones"><h3>{titulo}</h3>{ref}<ol>{"".join(items)}</ol></div>'


MARCAS = {"ok": "✓", "fail": "✕", "warn": "!", "info": "–", "covered": "✓"}


def html_checklist(r) -> str:
    partes = []
    for g in r.grupos:
        cs = [c for c in r.checks if c.grupo == g]
        if not cs:
            continue
        evaluados = [c for c in cs if c.estado != "info"]
        # Una BCA vigente o los términos firmados en la app no son un error: ya está cubierto.
        cubierto = lambda c: c.grupo == core.GRUPO_BCA and c.estado == "fail"
        bien = sum(c.estado != "fail" or cubierto(c) for c in evaluados)
        filas = []
        for c in cs:
            nota = ""
            if c.estado == "warn" and c.solucion:
                link = f'<a href="{e(c.link)}" target="_blank">{e(c.link_texto)}</a>' if c.link else ""
                nota = f'<div class="chk-note">{e(c.solucion)}{link}</div>'
            enlace = ""
            # En filas correctas solo se enlazan documentos (BCA, COI, Driver Information), no páginas genéricas.
            if c.estado in ("ok", "info") and c.link and re.search(r"BCA|COI|Driver Information", c.link_texto):
                enlace = f' <a href="{e(c.link)}" target="_blank">{e(c.link_texto)}</a>'
            visual = "covered" if cubierto(c) else c.estado
            filas.append(
                f'<div class="chk chk--{visual}"><div class="mark mark--{visual}">{MARCAS[visual]}</div>'
                f'<div><div class="chk-t">{e(c.titulo)}</div><div class="chk-d">{e(c.detalle)}{enlace}</div>{nota}</div></div>')
        resumen = f"{bien} of {len(evaluados)}" if evaluados else ""
        if g == core.GRUPO_MANUAL:
            resumen = "Not automated yet"
        partes.append(f'<div class="grp"><div class="grp-h"><b>{e(g)}</b><span>{resumen}</span></div>{"".join(filas)}</div>')
    return "".join(partes)


def html_comparacion(r) -> str:
    c, m = r.carrier, r.motus
    if not c or not m:
        return ""
    dir_ok = core.direccion_igual(c["direccion"], m)
    auth = core.motus_property(m, core.digitos(c["mc"]) or None)
    mc_motus = f"{auth['docket']}: {auth['estado']}" if auth else "No Property authority"

    def fila(etiqueta, crm_v, motus_v, ok):
        cls = "" if ok else ' class="cmp-bad"'
        return (f'<div class="cmp-row"><span>{e(etiqueta)}</span><div class="cmp-pair">'
                f'<i>CRM</i><div{cls}>{e(crm_v or "blank")}</div><i>MOTUS</i><div>{e(motus_v or "—")}</div></div></div>')

    filas = [
        fila("Name", c["nombre"], m["legal"], core.norm(c["nombre"]) == core.norm(m["legal"])),
        fila("Principal Place of Business", c["direccion"], m["direccion"], dir_ok),
        fila("USDOT", c["dot"], m["estado_dot"] + (" (Out of Service)" if m["fuera_servicio"] else ""), m["dot_activo"]),
        fila("MC", c["mc"] or "No MC", mc_motus, (not core.digitos(c["mc"])) or bool(auth and auth["estado"] == "Active")),
    ]
    dot = core.digitos(c["dot"])
    links = (f'<div class="cmp-links"><a href="{core.MOTUS_WEB.format(dot=dot)}" target="_blank">View in MOTUS</a>'
             f'<a href="{core.CRM_URL}/Admin/Carrier/Details/{c["_id"]}" target="_blank">View carrier in CRM</a></div>')
    return f'<div class="cmp"><div class="cmp-h">CRM vs. MOTUS</div>{"".join(filas)}{links}</div>'


def guardar_en_registro(r, codigo: str | None, gente: dict) -> str | None:
    reg = registro()
    if not reg.activo:
        return None
    pol = politica(r.documento)
    try:
        reg.guardar({
            "documento": r.documento,
            "shipment": (r.shipment or {}).get("numero") or f"S-{r.shipment_id:06d}",
            "driver_assignment": (r.asignacion or {}).get("da_nombre"),
            "carrier": (r.carrier or {}).get("nombre"),
            "dot": (r.carrier or {}).get("dot"),
            "mc": (r.carrier or {}).get("mc"),
            "resultado": r.veredicto,
            "cumplimiento": r.cumplimiento[0],
            "codigo": codigo,
            "motivos": [c.titulo for c in r.fallas],
            "politica_version": pol["etiqueta"] if pol else DOCS[r.documento]["procedimiento"],
            "shipment_owner": gente["owner"] or None,
            "dispatcher": gente["dispatcher"] or None,
            "solicitado_por": gente["solicitante"] or None,
        })
    except Exception as ex:
        return f"The verification could not be saved to the log: {ex}"
    return None


def barra_politica(doc: str) -> None:
    pol = politica(doc)
    cfg = DOCS[doc]
    if pol is None:
        st.markdown(
            '<div class="pol"><div class="pol-doc" aria-hidden="true"></div>'
            f'<div class="pol-txt"><b>{e(cfg["largo"])} Verification Policy</b>'
            f'<span>The official document is being prepared by BackOffice. Until it is published, the app applies '
            f'{cfg["procedimiento"]}.</span></div></div>', unsafe_allow_html=True)
        return
    st.markdown(
        '<div class="pol"><div class="pol-doc" aria-hidden="true"></div>'
        f'<div class="pol-txt"><b>{e(pol["title"])}</b>'
        f'<span>Version {e(pol["version"])}, updated {pol["fecha"]}. This is the policy the app applies to every '
        f'{cfg["largo"]} request.</span></div>'
        f'<a class="pol-btn" href="{pol["url"]}" target="_blank">Open policy (PDF)</a></div>',
        unsafe_allow_html=True)
    with st.expander("Policy revision history"):
        filas_hist = "".join(
            f'<tr><td>{e(v["version"])}</td><td>{fecha_larga(v["effective"])}</td><td>{e(v["changes"])}</td>'
            f'<td><a href="./app/static/{e(v["file"])}" target="_blank">PDF</a></td></tr>'
            for v in catalogo_politicas()[doc]["versions"])
        st.markdown('<table class="hist"><thead><tr><th>Version</th><th>Effective</th><th>What changed</th>'
                    f'<th>File</th></tr></thead><tbody>{filas_hist}</tbody></table>', unsafe_allow_html=True)
        st.caption("The first row is the current version. Earlier versions stay available for reference.")


def pantalla_verificacion(doc: str) -> None:
    """Pestaña completa de pre-verificación de un documento (BCA o LC)."""
    cfg = DOCS[doc]
    barra_politica(doc)
    with st.form(f"form_{doc}", border=True):
        c1, c2, c3 = st.columns([2, 2, 1.2], vertical_alignment="bottom")
        texto_ship = c1.text_input("Shipment", placeholder="S-039981", key=f"ship_{doc}")
        manual = c2.text_input("Requested by (optional)", placeholder="Only if not the Shipment Owner or Dispatcher",
                               help="Leave blank and the app uses the Shipment Owner and Dispatcher from the CRM.",
                               key=f"quien_{doc}")
        verificar = c3.form_submit_button(cfg["boton"], type="primary", use_container_width=True)

    if verificar:
        sid = numero_shipment(texto_ship)
        if not sid:
            st.error("Enter the shipment number, for example S-039981.")
        elif "STT_EMAIL" not in st.secrets or "STT_PASSWORD" not in st.secrets:
            st.error("The app is not set up to access the CRM. STT_EMAIL and STT_PASSWORD are missing from the secrets.")
        else:
            funcion = core.pre_verificar_bca if doc == "BCA" else core.pre_verificar_lc
            try:
                with st.spinner(cfg["spinner"]):
                    resultados = funcion(crm_compartido(), sid, motus_fn=motus)
            except core.CRMError as ex:
                st.error(str(ex))
                resultados = None
            except requests.RequestException as ex:
                st.error(f"Could not connect to the CRM or MOTUS. Try again in a moment. ({ex})")
                resultados = None
            if resultados is not None:
                cuando = hora_gt(datetime.now(timezone.utc))
                paquete = []
                for r in resultados:
                    gente = personas(r, manual.strip())
                    codigo = core.nuevo_codigo(sid, doc) if r.veredicto == "AUTORIZADO" else None
                    # Si MOTUS no respondió no se registra: no es una solicitud frenada por el broker.
                    aviso = None if r.veredicto == "SIN_VERIFICAR" else guardar_en_registro(r, codigo, gente)
                    paquete.append({"r": r, "codigo": codigo, "aviso": aviso, "gente": gente, "cuando": cuando})
                st.session_state[f"res_{doc}"] = paquete

    paquete = st.session_state.get(f"res_{doc}")
    if not paquete:
        pasos = "".join(f'<div class="paso"><b>{e(t)}</b><span>{e(d)}</span></div>' for t, d in cfg["pasos"])
        st.markdown(f'<p class="intro">{e(cfg["intro"])}</p><div class="pasos">{pasos}</div>', unsafe_allow_html=True)
        return
    for item in paquete:
        r, gente = item["r"], item["gente"]
        st.markdown(html_veredicto(r, item["codigo"], item["cuando"]) + html_politicas(r, gente)
                    + html_datos(r, gente, item["cuando"]) + html_acciones(r), unsafe_allow_html=True)
        if item["aviso"]:
            st.warning(item["aviso"])
        if item["codigo"] and not registro().activo:
            st.caption("The code log isn't set up yet, so BackOffice can't validate this code in the app.")
        izq, der = st.columns([3, 2], gap="large")
        izq.markdown(html_checklist(r), unsafe_allow_html=True)
        der.markdown(html_comparacion(r), unsafe_allow_html=True)
        st.write("")


# ============================================================
# Pestañas
# ============================================================
tab_bca, tab_lc, tab_bo, tab_dot = st.tabs(["Verify BCA", "Verify LC", "Validate code", "USDOT lookup"])

with tab_bca:
    pantalla_verificacion("BCA")

with tab_lc:
    pantalla_verificacion("LC")

# ---------------- Validate code (BackOffice) ----------------
with tab_bo:
    reg = registro()
    with st.form("form_codigo", border=True):
        c1, c2 = st.columns([3, 1.2], vertical_alignment="bottom")
        codigo_in = c1.text_input("Pre-verification code", placeholder="BCA-39981-K7QM or LC-39981-K7QM")
        buscar = c2.form_submit_button("Validate code", type="primary", use_container_width=True)

    if buscar:
        cod = codigo_in.strip().upper()
        if not reg.activo:
            st.error("The code log is not set up. Add SUPABASE_URL and SUPABASE_KEY to the secrets.")
        elif not re.fullmatch(r"(BCA|LC)-\d+-[A-Z0-9]{4}", cod):
            st.error("Codes use this format: BCA-39981-K7QM or LC-39981-K7QM.")
        else:
            try:
                fila = reg.buscar(cod)
            except requests.RequestException as ex:
                st.error(f"Could not read the code log: {ex}")
                fila = None
            else:
                if not fila:
                    st.markdown(
                        '<div class="ticket"><div class="ticket-h" style="background:var(--red)">Invalid code</div>'
                        f'<dl><dt>Code</dt><dd>{e(cod)}</dd><dt>What this means</dt>'
                        '<dd>The app never issued this code. The request did not pass pre-verification.</dd></dl></div>',
                        unsafe_allow_html=True)
                else:
                    cuando = hora_gt(datetime.fromisoformat(fila["created_at"].replace("Z", "+00:00")))
                    st.markdown(
                        '<div class="ticket"><div class="ticket-h" style="background:var(--ink)">Valid code</div><dl>'
                        f'<dt>Code</dt><dd>{e(fila["codigo"])}</dd>'
                        f'<dt>Document</dt><dd>{e(DOCS.get(fila.get("documento"), {}).get("largo", fila.get("documento") or ""))}</dd>'
                        f'<dt>Shipment</dt><dd>{e(fila.get("shipment") or "")}</dd>'
                        f'<dt>Driver Assignment</dt><dd>{e(fila.get("driver_assignment") or "")}</dd>'
                        f'<dt>Carrier</dt><dd>{e(fila.get("carrier") or "")}</dd>'
                        f'<dt>DOT and MC</dt><dd>{e(fila.get("dot") or "")}, {e(fila.get("mc") or "no MC")}</dd>'
                        f'<dt>Shipment Owner</dt><dd>{e(fila.get("shipment_owner") or "Not set")}</dd>'
                        f'<dt>Dispatcher</dt><dd>{e(fila.get("dispatcher") or "Not set")}</dd>'
                        f'<dt>Requested by</dt><dd>{e(fila.get("solicitado_por") or "Not identified")}</dd>'
                        f'<dt>Verified</dt><dd>{e(cuando)} (Guatemala time)</dd>'
                        f'<dt>Policy applied</dt><dd>{e(fila.get("politica_version") or "Not recorded")}</dd>'
                        '</dl></div>',
                        unsafe_allow_html=True)
                    st.caption("The code confirms everything was in order at the time of verification. "
                               "If time has passed, verify the shipment again on the Verify BCA or Verify LC tab.")

    st.markdown('<h2 class="sec">Last 30 days</h2>', unsafe_allow_html=True)
    if not reg.activo:
        st.caption("Once the Supabase log is set up, this section shows how many requests were stopped "
                   "before reaching BackOffice, and why.")
    else:
        for doc in DOCS:
            try:
                filas = reg.recientes(30, doc)
            except requests.RequestException as ex:
                st.error(f"Could not read the code log: {ex}")
                filas = []
            total = len(filas)
            frenadas = sum(f["resultado"] != "AUTORIZADO" for f in filas)
            st.markdown(
                f'<h3 class="sub">{e(DOCS[doc]["largo"])}</h3><div class="kpis">'
                f'<div class="kpi"><b>{total:,}</b><span>Verifications</span></div>'
                f'<div class="kpi kpi--stop"><b>{frenadas:,}</b><span>Stopped before reaching BackOffice</span></div>'
                f'<div class="kpi"><b>{total - frenadas:,}</b><span>Approved to send</span></div>'
                '</div>', unsafe_allow_html=True)
            motivos = Counter(m for f in filas for m in (f.get("motivos") or []))
            if motivos:
                maximo = max(motivos.values())
                barras = "".join(
                    f'<div class="bar"><span>{e(m)}</span><div style="width:{100 * n / maximo:.0f}%"></div><em>{n:,}</em></div>'
                    for m, n in motivos.most_common(8))
                st.markdown(f'<div class="bars-h">Most common reasons</div><div class="bars">{barras}</div>',
                            unsafe_allow_html=True)

# ---------------- USDOT lookup ----------------
with tab_dot:
    with st.form("form_dot", border=True):
        c1, c2 = st.columns([3, 1.2], vertical_alignment="bottom")
        entrada = c1.text_area("USDOT numbers, one per line", "3692487", height=90)
        consultar = c2.form_submit_button("Look up in MOTUS", type="primary", use_container_width=True)

    if consultar:
        dots = list(dict.fromkeys(re.findall(r"\d{4,8}", entrada)))
        if not dots:
            st.error("Enter at least one USDOT number.")
        else:
            filas = []
            with st.spinner("Looking up MOTUS…"):
                for dot in dots:
                    try:
                        data = motus(dot)
                    except core.MotusNoDisponible as ex:
                        filas.append({"USDOT": dot, "USDOT Status": f"MOTUS did not respond ({ex}). Try again in a few minutes."})
                        continue
                    except requests.RequestException as ex:
                        filas.append({"USDOT": dot, "USDOT Status": f"Connection error: {ex}"})
                        continue
                    if not data:
                        filas.append({"USDOT": dot, "USDOT Status": "Not found in MOTUS"})
                        continue
                    m = core.motus_resumen(data)
                    a = core.motus_property(m)
                    filas.append({
                        "USDOT": dot,
                        "USDOT Status": m["estado_dot"] + (" (Out of Service)" if m["fuera_servicio"] else ""),
                        "Legal Business Name": m["legal"],
                        "Principal Place of Business": m["direccion"],
                        "MC": a["docket"] if a else "",
                        "Motor Carrier of Property": a["estado"] if a else "No authority",
                    })
            st.dataframe(pd.DataFrame(filas), use_container_width=True, hide_index=True)
            st.caption(f"Checked {hora_gt(datetime.now(timezone.utc))} (Guatemala time).")

st.markdown(f'<div class="pie">STT Logistics Group BackOffice. Version {VERSION}. '
            'All dates and times are shown in Guatemala time (UTC−6).</div>', unsafe_allow_html=True)
