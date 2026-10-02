"""
Lógica de negocio de la app de BackOffice de STT Logistics Group.

- MOTUS (FMCSA): estado del USDOT, Legal Name, Principal Place of Business y autoridad operativa.
- CRM de STT (sttcrm.com): Shipment, Driver Assignment, Carrier y archivos del Carrier.
- Reglas de pre-verificación de la BCA (Broker Carrier Agreement).

Este módulo no depende de Streamlit, así que se puede probar por separado.
"""
from __future__ import annotations

import io
import re
import secrets
from dataclasses import dataclass, field
from datetime import datetime, timedelta, timezone

import requests
from bs4 import BeautifulSoup

UA = ("Mozilla/5.0 (Windows NT 10.0; Win64; x64) AppleWebKit/537.36 "
      "(KHTML, like Gecko) Chrome/153.0.0.0 Safari/537.36")


# ============================================================
# Utilidades de texto
# ============================================================
def norm(texto: str | None) -> str:
    """Mayúsculas, sin puntuación y con espacios simples. No cambia palabras (LANE sigue siendo LANE)."""
    return " ".join(re.sub(r"[^A-Z0-9]+", " ", (texto or "").upper()).split())


def digitos(texto: str | None) -> str:
    return re.sub(r"\D", "", texto or "")


# ============================================================
# MOTUS
# ============================================================
MOTUS_API = "https://motus.dot.gov/api/carriers/{dot}"
MOTUS_WEB = "https://motus.dot.gov/customer/{dot}/account"
AUTORIDAD_PROPERTY = "Motor Carrier of Property (Except Household Goods)"
TIPO_DIR_FISICA = "eef9bd53-0da3-4b96-b462-8e2711a009ef"   # Principal Place of Business
TIPO_DIR_CORREO = "34878d0c-cf18-46ce-a23e-60bfcaf558db"   # Mailing Address


def motus_consultar(dot: str) -> dict | None:
    r = requests.get(
        MOTUS_API.format(dot=dot),
        headers={"accept": "application/json, text/plain, */*", "user-agent": UA,
                 "referer": "https://motus.dot.gov/public/search"},
        timeout=30,
    )
    if r.status_code == 404:
        return None
    r.raise_for_status()
    return r.json() or None


def motus_resumen(data: dict) -> dict:
    """Extrae de la respuesta de MOTUS los datos que se validan."""
    estado = (((data.get("entityDotNumber") or {}).get("dotNumberStatus") or {})
              .get("dotNumberStatus") or "")
    fuera_servicio = bool(data.get("outOfService"))

    nombres = [n for n in data.get("entityNames") or [] if not n.get("disableDate")]
    legal = next((n.get("entityName") or "" for n in nombres if n.get("nameType") == "Legal"), "")
    dba = next((n.get("entityName") or "" for n in nombres if n.get("nameType") == "DBA"), "")

    locs = [l for l in data.get("locations") or [] if not l.get("disableDate")]
    fisica = next((l for l in locs if l.get("addressTypeId") == TIPO_DIR_FISICA), None)
    if fisica is None:  # por si MOTUS cambia los identificadores de tipo
        fisica = next((l for l in locs if l.get("addressTypeId") != TIPO_DIR_CORREO), None)

    calle = ciudad = estado_dir = zip5 = ""
    if fisica:
        calle = " ".join(p for p in [fisica.get("addressLine1"), fisica.get("addressLine2")] if p)
        ciudad = fisica.get("city") or ""
        estado_dir = fisica.get("state") or ""
        zip5 = (fisica.get("zipCode") or "")[:5]

    autoridades = []
    for reg in data.get("entityRegistrations") or []:
        for roa in reg.get("entityRegistrationOperatingAuthorities") or []:
            oa = roa.get("entityOperatingAuthority") or {}
            if oa.get("disableDate"):
                continue
            autoridades.append({
                "tipo": (oa.get("operatingAuthorityType") or {}).get("operatingAuthorityType") or "",
                "docket": oa.get("docketNumber") or "",
                "estado": (oa.get("operatingAuthorityStatus") or {}).get("operatingAuthorityStatusName") or "",
            })

    return {
        "estado_dot": estado,
        "fuera_servicio": fuera_servicio,
        "dot_activo": estado == "Active" and not fuera_servicio,
        "legal": legal,
        "dba": dba,
        "calle": calle,
        "ciudad": ciudad,
        "estado": estado_dir,
        "zip": zip5,
        "direccion": f"{calle}, {ciudad}, {estado_dir} {zip5}".strip(", ") if fisica else "",
        "autoridades": autoridades,
    }


def motus_property(resumen: dict, mc: str | None = None) -> dict | None:
    """Autoridad Motor Carrier of Property; si se da un MC, solo la que tenga ese número."""
    for a in resumen["autoridades"]:
        if a["tipo"] != AUTORIDAD_PROPERTY:
            continue
        if mc is None or digitos(a["docket"]) == digitos(mc):
            return a
    return None


# ============================================================
# CRM de STT
# ============================================================
CRM_URL = "https://sttcrm.com"


class CRMError(Exception):
    pass


def leer_campos(soup: BeautifulSoup) -> tuple[dict, dict]:
    """Pares etiqueta -> valor de una página de detalle. Devuelve (por texto, por atributo for)."""
    por_texto, por_for = {}, {}
    for lab in soup.select("main label.col-form-label"):
        fila = lab.find_parent(class_="row")
        if not fila:
            continue
        etiqueta = lab.get_text(strip=True)
        caja = fila.find(class_="form-text-row")
        if caja:
            valor = caja.get_text(" ", strip=True)
        else:
            valor = fila.get_text(" ", strip=True)
            valor = valor[len(etiqueta):].strip() if valor.startswith(etiqueta) else valor
        por_texto.setdefault(etiqueta, valor)
        if lab.get("for"):
            por_for.setdefault(lab["for"], valor)
    return por_texto, por_for


def parse_shipment(soup: BeautifulSoup) -> dict | None:
    titulo = soup.title.get_text(strip=True) if soup.title else ""
    if "Shipment Detail" not in titulo:
        return None
    texto, por_for = leer_campos(soup)

    asignaciones = []
    bloque = soup.find(id="order-DriverAssignment")
    for item in (bloque.select(".request-info-itembox") if bloque else []):
        a_da = item.find("a", href=re.compile(r"/Admin/DriverAssignment/Details/\d+"))
        a_car = item.find("a", href=re.compile(r"/Admin/Carrier/Details/\d+"))
        a_drv = item.find("a", href=re.compile(r"/Admin/Driver/DriverDetails/\d+"))
        if not a_da:
            continue
        asignaciones.append({
            "da_id": int(re.search(r"/Details/(\d+)", a_da["href"]).group(1)),
            "da_nombre": a_da.get_text(strip=True),
            "carrier_id": int(re.search(r"/Details/(\d+)", a_car["href"]).group(1)) if a_car else None,
            "carrier_nombre": a_car.get_text(strip=True) if a_car else "",
            "driver": a_drv.get_text(" ", strip=True) if a_drv else "",
        })

    return {
        "numero": texto.get("Shipment #", ""),
        "orden": texto.get("Order", ""),
        "owner": por_for.get("OrderOwner", "") or texto.get("Shipment Owner", ""),
        "dispatcher": por_for.get("DispatcherId", "") or texto.get("Dispatcher Id", ""),
        "status": texto.get("Status", ""),
        "origen_ciudad": por_for.get("BillingAddress_City", ""),
        "origen_estado": por_for.get("BillingAddress_State", ""),
        "destino_ciudad": por_for.get("ShippingAddress_City", ""),
        "destino_estado": por_for.get("ShippingAddress_State", ""),
        "asignaciones": asignaciones,
    }


def parse_driver_assignment(soup: BeautifulSoup) -> dict:
    texto, _ = leer_campos(soup)
    return {
        "status": texto.get("Status", ""),
        "email": texto.get("Email", ""),
        "driver": texto.get("Driver Name", ""),
        "carrier": texto.get("Carrier", ""),
        "dot": texto.get("DOT", ""),
    }


def parse_carrier(soup: BeautifulSoup) -> dict:
    texto, _ = leer_campos(soup)
    token = soup.find("input", {"name": "__RequestVerificationToken"})
    return {
        "nombre": texto.get("Company Name", ""),
        "mc": texto.get("MC", ""),
        "dot": texto.get("DOT", ""),
        "direccion": texto.get("Address", ""),
        "status": texto.get("Status", ""),
        "_token": token.get("value", "") if token else "",
    }


def parse_archivos(js: dict) -> list[dict]:
    filas = js.get("Data") or js.get("data") or []
    return [{
        "relacionado": f.get("DocumentType") or "",
        "tipo": f.get("TypeName") or "",
        "archivo": f.get("Filename") or "",
        "creado_por": f.get("CreatedBy") or "",
        "creado": f.get("CreatedOn") or "",
        "guid": f.get("DownloadGuid") or "",
    } for f in filas]


def partir_direccion_crm(direccion: str) -> dict:
    """Separa la dirección del CRM en calle, ciudad, estado y zip.

    Acepta los formatos que usa el CRM, por ejemplo:
      '395 GARCIA LANE, SAN LUIS, AZ, 85349, US'
      '8212 NE 13TH AVE APT C7, VANCOUVER, WA 98665'
    """
    texto = re.sub(r"[,\s]+(US|USA|UNITED STATES)\s*$", "", (direccion or "").strip(), flags=re.I)
    m = re.match(r"^(?P<calle>.+?),\s*(?P<ciudad>[^,]+?)\s*,\s*(?P<estado>[A-Za-z]{2})\s*,?\s*"
                 r"(?P<zip>\d{5})(?:-\d{4})?\s*$", texto)
    if not m:
        return {"calle": texto, "ciudad": "", "estado": "", "zip": ""}
    return {"calle": m["calle"].strip(), "ciudad": m["ciudad"].strip(),
            "estado": m["estado"].upper(), "zip": m["zip"]}


def direccion_igual(direccion_crm: str, motus: dict) -> bool:
    """Compara contra la Principal Place of Business de MOTUS: mismas palabras, sin importar
    mayúsculas, comas ni el 'US' final. Una abreviatura distinta (LANE / LN) no es igual."""
    if not motus.get("direccion"):
        return False
    p = partir_direccion_crm(direccion_crm)
    if p["ciudad"]:
        return (norm(p["calle"]) == norm(motus["calle"]) and norm(p["ciudad"]) == norm(motus["ciudad"])
                and norm(p["estado"]) == norm(motus["estado"]) and p["zip"] == motus["zip"])
    # Formato no reconocido: se compara el texto completo
    return norm(p["calle"]) == norm(f"{motus['calle']} {motus['ciudad']} {motus['estado']} {motus['zip']}")


class CRM:
    """Cliente de solo lectura del CRM. Inicia sesión una vez y la renueva si expira."""

    def __init__(self, email: str, password: str):
        self.email, self.password = email, password
        self.s: requests.Session | None = None

    def _login(self) -> None:
        s = requests.Session()
        s.headers["user-agent"] = UA
        r = s.get(f"{CRM_URL}/login", timeout=30)
        r.raise_for_status()
        soup = BeautifulSoup(r.text, "html.parser")
        pwd = soup.find("input", {"type": "password"})
        form = pwd.find_parent("form") if pwd else None
        if not form:
            raise CRMError("The CRM sign-in form was not found. The CRM may have changed its login page.")
        datos = {}
        for inp in form.find_all("input"):
            nombre = inp.get("name")
            if not nombre:
                continue
            tipo = (inp.get("type") or "text").lower()
            if tipo == "password":
                datos[nombre] = self.password
            elif tipo in ("email", "text"):
                datos[nombre] = self.email
            elif tipo == "checkbox":
                datos[nombre] = "true"
            else:
                datos[nombre] = inp.get("value", "")
        accion = form.get("action") or "/login"
        s.post(accion if accion.startswith("http") else CRM_URL + accion, data=datos, timeout=30)
        if "/login" in s.get(f"{CRM_URL}/Admin", timeout=30).url.lower():
            raise CRMError("The CRM rejected the username or password configured in the app.")
        self.s = s

    def _get(self, ruta: str, reintento: bool = True) -> requests.Response:
        if self.s is None:
            self._login()
        r = self.s.get(CRM_URL + ruta, timeout=30)
        if "/login" in r.url.lower():
            if reintento:
                self.s = None
                return self._get(ruta, reintento=False)
            raise CRMError("The CRM ended the session and the app could not sign back in.")
        r.raise_for_status()
        return r

    def _pagina(self, ruta: str) -> BeautifulSoup:
        return BeautifulSoup(self._get(ruta).text, "html.parser")

    def shipment(self, sid: int) -> dict | None:
        return parse_shipment(self._pagina(f"/Admin/Shipments/Details/{sid}"))

    def driver_assignment(self, da_id: int) -> dict:
        return parse_driver_assignment(self._pagina(f"/Admin/DriverAssignment/Details/{da_id}"))

    def carrier(self, carrier_id: int) -> dict:
        return parse_carrier(self._pagina(f"/Admin/Carrier/Details/{carrier_id}"))

    def archivos_carrier(self, carrier_id: int, token: str) -> list[dict]:
        r = self.s.post(
            f"{CRM_URL}/Admin/CarrierManagement/CarrierManagementPictureList?DriverId={carrier_id}",
            data={"draw": "1", "start": "0", "length": "500", "__RequestVerificationToken": token},
            headers={"X-Requested-With": "XMLHttpRequest",
                     "Referer": f"{CRM_URL}/Admin/Carrier/Details/{carrier_id}"},
            timeout=30,
        )
        r.raise_for_status()
        return parse_archivos(r.json())

    def descargar(self, guid: str) -> bytes:
        return self._get(f"/Admin/Download/DownloadFile?downloadGuid={guid}").content


def texto_pdf(contenido: bytes) -> str | None:
    """Texto de un PDF. None si no es PDF o es una imagen escaneada sin texto."""
    if not contenido or not contenido.startswith(b"%PDF"):
        return None
    try:
        from pypdf import PdfReader
        texto = "\n".join((p.extract_text() or "") for p in PdfReader(io.BytesIO(contenido)).pages)
    except Exception:
        return None
    return texto if len(texto.strip()) > 40 else None


# ============================================================
# Cumplimiento de políticas (genérico para BCA y futuros documentos)
# ============================================================
UMBRAL_ROJO = 0.25   # 25 % o menos de requisitos cumplidos -> rojo


def nivel_cumplimiento(checks: list, requisitos_base: int) -> tuple[str, int, int]:
    """Devuelve (nivel, cumplidos, total) con nivel 'green' | 'yellow' | 'red'.

    Los requisitos que no se llegaron a evaluar (porque faltaba algo antes) cuentan como no cumplidos,
    por eso el total nunca es menor que `requisitos_base`.
    """
    evaluados = [c for c in checks if c.estado in ("ok", "fail", "warn")]
    cumplidos = sum(c.estado != "fail" for c in evaluados)
    total = max(len(evaluados), requisitos_base)
    if cumplidos == len(evaluados) and len(evaluados) >= requisitos_base:
        return "green", cumplidos, total
    if cumplidos / total <= UMBRAL_ROJO:
        return "red", cumplidos, total
    return "yellow", cumplidos, total


# ============================================================
# Reglas de la BCA (textos visibles en inglés de EE. UU.)
# ============================================================
GRUPO_DA = "Driver Assignment"
GRUPO_CARRIER = "Carrier in the CRM"
GRUPO_MOTUS = "MOTUS"
GRUPO_RUTA = "Route"
GRUPO_BCA = "Existing BCA"
ORDEN_GRUPOS = [GRUPO_DA, GRUPO_CARRIER, GRUPO_MOTUS, GRUPO_RUTA, GRUPO_BCA]
REQUISITOS_BCA = 11   # 3 DA + 3 Carrier + 3 MOTUS + Ruta + BCA existente (sin MC; con MC son 13)


@dataclass
class Check:
    grupo: str
    titulo: str
    estado: str                 # ok | fail | warn | info
    detalle: str = ""
    solucion: str = ""
    link: str = ""
    link_texto: str = ""


@dataclass
class ResultadoBCA:
    shipment_id: int
    shipment: dict | None
    asignacion: dict | None = None
    da: dict | None = None
    carrier: dict | None = None
    motus: dict | None = None
    bca_previa: dict | None = None
    checks: list[Check] = field(default_factory=list)

    @property
    def fallas(self) -> list[Check]:
        return [c for c in self.checks if c.estado == "fail"]

    @property
    def veredicto(self) -> str:
        if any(c.grupo == GRUPO_BCA for c in self.fallas):
            return "YA_EXISTE"
        return "NO_ENVIAR" if self.fallas else "AUTORIZADO"

    @property
    def requisitos(self) -> tuple[int, int]:
        evaluados = [c for c in self.checks if c.estado in ("ok", "fail", "warn")]
        return sum(c.estado != "fail" for c in evaluados), len(evaluados)

    @property
    def cumplimiento(self) -> tuple[str, int, int]:
        """Nivel de cumplimiento del solicitante. La BCA ya existente no es un error del broker,
        así que no cuenta en contra."""
        checks = [c for c in self.checks if not (c.grupo == GRUPO_BCA and c.estado == "fail")]
        base = REQUISITOS_BCA - (1 if self.veredicto == "YA_EXISTE" else 0)
        return nivel_cumplimiento(checks, base)


def _link_da(da_id):
    return f"{CRM_URL}/Admin/DriverAssignment/Details/{da_id}"


def _link_carrier(cid):
    return f"{CRM_URL}/Admin/Carrier/Details/{cid}"


def _numero_en_texto(numero: str, texto: str) -> bool:
    """True si el número aparece completo en el texto (MC-1732074, MC 1732074, USDOT: 4409578…)."""
    if not numero:
        return False
    return bool(re.search(rf"(?<!\d){numero}(?!\d)", re.sub(r"[\s\-]", "", texto)))


def revisar_bca_previa(archivos, leer_pdf, carrier, motus, mc_activo: bool) -> tuple[Check, dict | None]:
    """Busca BCAs en los archivos del Carrier y decide si alguna sigue vigente frente a MOTUS.

    Regla de BackOffice para el número que debe tener la BCA (ver bitácora B-019):
    - Carrier con MC activo en MOTUS: la plantilla de BCA lleva el MC#. El DOT puede no aparecer.
    - Carrier sin MC, o con el MC INACTIVE en MOTUS: la BCA lleva el DOT#.
    """
    candidatos = [f for f in archivos
                  if "BCA" in f["tipo"].upper() or "BCA" in f["archivo"].upper()]
    if not candidatos:
        return Check(GRUPO_BCA, "No previous BCA for this carrier", "ok",
                     "There is no BCA in the carrier's files."), None

    if mc_activo:
        etiqueta_id, numero_id = f"MC {digitos(carrier['mc'])}", digitos(carrier["mc"])
    else:
        etiqueta_id, numero_id = f"DOT {digitos(carrier['dot'])}", digitos(carrier["dot"])

    ilegibles, desactualizadas = [], []
    for f in candidatos:
        texto = leer_pdf(f["guid"]) if f["guid"] else None
        if texto is None:
            ilegibles.append(f)
            continue
        t = norm(texto)
        revision = [
            ("legal name", bool(motus["legal"]) and norm(motus["legal"]) in t),
            (etiqueta_id, _numero_en_texto(numero_id, texto)),
            ("address", bool(motus["calle"]) and norm(motus["calle"]) in t),
            ("signature", any(p in f["archivo"].upper() for p in ("SIGNATURE", "SIGNED", "FIRMAD"))
             or "BCA" in f["tipo"].upper()),
        ]
        resumen = ". ".join(f"{n[0].upper() + n[1:]}: {'found' if ok else 'not found'}" for n, ok in revision) + "."
        link = f"{CRM_URL}/Admin/Download/DownloadFile?downloadGuid={f['guid']}"
        subida = f", uploaded {f['creado']}" if f["creado"] else ""
        if all(ok for _, ok in revision):
            return Check(
                GRUPO_BCA, "A current BCA is already on file", "fail",
                f"{f['archivo']}{subida}. Checked against MOTUS. {resumen}",
                "No new BCA is needed. Use the signed BCA already in the carrier's files.",
                link, "Open the BCA on file"), f
        desactualizadas.append((f, [n for n, ok in revision if not ok], resumen, link, subida))

    if ilegibles:
        nombres = ", ".join(f["archivo"] for f in ilegibles)
        return Check(GRUPO_BCA, "Previous BCA could not be read", "warn",
                     f"The content of {nombres} could not be read automatically. It may be a scanned image.",
                     "You can submit the request. BackOffice will review that BCA before processing it.",
                     _link_carrier(carrier["_id"]), "View carrier files"), None

    f, faltan, resumen, link, subida = desactualizadas[0]
    otras = f" {len(desactualizadas) - 1} other previous BCA(s) also do not match." if len(desactualizadas) > 1 else ""
    return Check(GRUPO_BCA, "Previous BCA is out of date", "ok",
                 f"{f['archivo']}{subida}. Checked against MOTUS. {resumen} "
                 f"A new BCA is needed because it is missing the current {', '.join(faltan)}.{otras}",
                 "", link, "Open the previous BCA"), None


def evaluar_bca(shipment_id: int, shipment: dict | None, asignacion: dict | None,
                da: dict | None, carrier: dict | None, archivos: list[dict] | None,
                motus_data: dict | None, leer_pdf) -> ResultadoBCA:
    """Aplica el procedimiento de verificación de BCA de BackOffice. Función pura (sin red)."""
    res = ResultadoBCA(shipment_id, shipment, asignacion, da, carrier)
    add = res.checks.append

    if shipment is None:
        add(Check(GRUPO_DA, "Shipment found", "fail",
                  f"Shipment S-{shipment_id:06d} does not exist in the CRM.",
                  "Check the shipment number and try again."))
        return res

    # ---- 1. Driver Assignment ----
    if asignacion is None:
        add(Check(GRUPO_DA, "Driver assigned", "fail",
                  "The shipment has no driver in Drivers Assignments.",
                  "Assign the driver in the Drivers Assignments section of the shipment.",
                  f"{CRM_URL}/Admin/Shipments/Details/{shipment_id}", "Open shipment"))
        return res

    link_da = _link_da(asignacion["da_id"])
    add(Check(GRUPO_DA, "Driver assigned", "ok",
              f"{asignacion['da_nombre']}, {asignacion['driver'] or (da or {}).get('driver') or 'driver name missing'}"))

    status = (da or {}).get("status", "")
    if status.strip().lower() == "dispatched":
        add(Check(GRUPO_DA, "Driver Assignment is Dispatched", "ok", "Status: Dispatched"))
    else:
        add(Check(GRUPO_DA, "Driver Assignment is Dispatched", "fail",
                  f"Current status: {status or 'blank'}.",
                  f"Change the status of {asignacion['da_nombre']} to Dispatched.",
                  link_da, f"Open {asignacion['da_nombre']}"))

    email = (da or {}).get("email", "").strip()
    if "@" in email:
        add(Check(GRUPO_DA, "Email on the Driver Assignment", "ok", email))
    else:
        add(Check(GRUPO_DA, "Email on the Driver Assignment", "fail",
                  "The Driver Assignment has no email.",
                  f"Add the email to {asignacion['da_nombre']}. The BCA is sent to that address.",
                  link_da, f"Open {asignacion['da_nombre']}"))

    # ---- 2. Carrier en el CRM ----
    if carrier is None:
        add(Check(GRUPO_CARRIER, "Carrier linked", "fail",
                  "The Driver Assignment has no carrier linked.",
                  "Select the carrier on the Driver Assignment.", link_da, f"Open {asignacion['da_nombre']}"))
        return res

    link_car = _link_carrier(carrier["_id"])
    for etiqueta, clave, falta in [
        ("Company Name", "nombre", "Add the carrier's company name."),
        ("DOT", "dot", "A DOT number is required for every carrier at STT. Add it to the carrier."),
        ("Address", "direccion", "Add the address. It must be the Principal Place of Business shown in MOTUS."),
    ]:
        valor = carrier.get(clave, "").strip()
        if valor:
            add(Check(GRUPO_CARRIER, etiqueta, "ok", valor))
        else:
            add(Check(GRUPO_CARRIER, etiqueta, "fail", "This field is blank on the carrier.",
                      falta, link_car, "Open carrier"))
    mc_crm = digitos(carrier.get("mc"))
    add(Check(GRUPO_CARRIER, "MC", "ok" if mc_crm else "info",
              carrier["mc"] if mc_crm else "No MC. The MC is optional; without it the load must stay within one state."))

    dot = digitos(carrier.get("dot"))
    if not dot:
        return res

    # ---- 3. MOTUS ----
    if motus_data is None:
        add(Check(GRUPO_MOTUS, "DOT registered in MOTUS", "fail",
                  f"DOT {dot} does not exist in MOTUS.",
                  "Confirm the DOT number with the carrier and correct it on the carrier.",
                  link_car, "Open carrier"))
        return res

    m = motus_resumen(motus_data)
    res.motus = m
    link_motus = MOTUS_WEB.format(dot=dot)

    if m["dot_activo"]:
        add(Check(GRUPO_MOTUS, "USDOT is active", "ok", f"USDOT {dot}: Active"))
    else:
        add(Check(GRUPO_MOTUS, "USDOT is active", "fail",
                  f"USDOT {dot}: {m['estado_dot'] or 'no status'}"
                  + (", under an Out-of-Service order" if m["fuera_servicio"] else "") + ".",
                  "This carrier is not authorized to operate. Assign a different carrier.",
                  link_motus, "View in MOTUS"))

    if norm(carrier["nombre"]) == norm(m["legal"]):
        add(Check(GRUPO_MOTUS, "Company Name matches Legal Name", "ok", m["legal"]))
    else:
        extra = " It matches the DBA, but the BCA must use the legal name." \
            if m["dba"] and norm(carrier["nombre"]) == norm(m["dba"]) else ""
        add(Check(GRUPO_MOTUS, "Company Name matches Legal Name", "fail",
                  f"CRM: {carrier['nombre'] or 'blank'}. MOTUS: {m['legal'] or 'no legal name'}.{extra}",
                  f"Enter the company name exactly as it appears in MOTUS: {m['legal']}.",
                  link_car, "Open carrier"))

    if direccion_igual(carrier["direccion"], m):
        add(Check(GRUPO_MOTUS, "Address matches Principal Place of Business", "ok", m["direccion"]))
    else:
        add(Check(GRUPO_MOTUS, "Address matches Principal Place of Business", "fail",
                  f"CRM: {carrier['direccion'] or 'blank'}. MOTUS: {m['direccion'] or 'no physical address'}.",
                  f"Enter the address exactly as it appears in MOTUS: {m['direccion']}. "
                  "Different abbreviations and the mailing address are not accepted.",
                  link_car, "Open carrier"))

    mc_activo = False
    if mc_crm:
        auth = motus_property(m, mc_crm)
        if auth is None:
            otros = ", ".join(a["docket"] for a in m["autoridades"] if a["tipo"] == AUTORIDAD_PROPERTY) or "none"
            add(Check(GRUPO_MOTUS, "MC holds Motor Carrier of Property authority", "fail",
                      f"MC {mc_crm} from the CRM is not listed in MOTUS for this DOT (MOTUS shows: {otros}).",
                      "Correct the MC on the carrier, or leave it blank if the carrier has no MC.",
                      link_car, "Open carrier"))
        elif auth["estado"] == "Active":
            mc_activo = True
            add(Check(GRUPO_MOTUS, "MC holds Motor Carrier of Property authority", "ok",
                      f"{auth['docket']}: Active"))
        else:
            add(Check(GRUPO_MOTUS, "MC holds Motor Carrier of Property authority", "warn",
                      f"{auth['docket']}: {auth['estado'] or 'no status'} in MOTUS. "
                      "The same-state rule applies."))

    # ---- 4. Ruta (solo sin MC o con MC inactivo) ----
    o, d = shipment["origen_estado"].strip().upper(), shipment["destino_estado"].strip().upper()
    ruta = f"{o or '?'} to {d or '?'}"
    if mc_activo:
        add(Check(GRUPO_RUTA, "Route is allowed", "ok", f"{ruta}. Interstate loads are allowed with an active MC."))
    elif not (o and d):
        add(Check(GRUPO_RUTA, "Route is allowed", "fail",
                  "The shipment is missing the origin or destination state.",
                  "Complete Origin and Destination under Route Information on the shipment.",
                  f"{CRM_URL}/Admin/Shipments/Details/{shipment_id}", "Open shipment"))
    elif o == d:
        add(Check(GRUPO_RUTA, "Route is allowed", "ok", f"{ruta}: the load stays within one state."))
    else:
        en_motus = motus_property(m)
        if not mc_crm and en_motus and en_motus["estado"] == "Active":
            solucion = (f"MOTUS shows {en_motus['docket']} as active for this carrier. "
                        "Add it to the carrier and verify again.")
        else:
            solucion = ("Without an active MC, the carrier can only move loads within one state. "
                        "Assign a carrier with an active MC or correct the carrier's MC.")
        add(Check(GRUPO_RUTA, "Route is allowed", "fail",
                  f"The load moves from {ruta} and the carrier has no active MC.",
                  solucion, link_car, "Open carrier"))

    # ---- 5. BCA existente ----
    if archivos is None:
        add(Check(GRUPO_BCA, "Carrier files", "warn",
                  "The carrier's files section could not be read.",
                  "You can submit the request. BackOffice will check whether a BCA is already on file.",
                  link_car, "View carrier files"))
    else:
        chk, previa = revisar_bca_previa(archivos, leer_pdf, carrier, m, mc_activo)
        res.bca_previa = previa
        add(chk)
    return res


def pre_verificar_bca(crm: CRM, shipment_id: int, motus_fn=motus_consultar) -> list[ResultadoBCA]:
    """Recorre el CRM y MOTUS para un Shipment y devuelve un resultado por Driver Assignment."""
    shipment = crm.shipment(shipment_id)
    if shipment is None or not shipment["asignaciones"]:
        return [evaluar_bca(shipment_id, shipment, None, None, None, None, None, None)]

    resultados = []
    for asig in shipment["asignaciones"]:
        da = crm.driver_assignment(asig["da_id"])
        carrier = archivos = motus_data = None
        if asig["carrier_id"]:
            carrier = crm.carrier(asig["carrier_id"])
            carrier["_id"] = asig["carrier_id"]
            try:
                archivos = crm.archivos_carrier(asig["carrier_id"], carrier["_token"])
            except Exception:
                archivos = None
            dot = digitos(carrier["dot"])
            motus_data = motus_fn(dot) if dot else None

        def leer_pdf(guid):
            try:
                return texto_pdf(crm.descargar(guid))
            except Exception:
                return None

        resultados.append(evaluar_bca(shipment_id, shipment, asig, da, carrier,
                                      archivos, motus_data, leer_pdf))
    return resultados


# ============================================================
# Registro de verificaciones (Supabase)
# ============================================================
def nuevo_codigo(shipment_id: int) -> str:
    alfabeto = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
    return f"BCA-{shipment_id}-" + "".join(secrets.choice(alfabeto) for _ in range(4))


class Registro:
    """Guarda cada pre-verificación en la tabla `prechecks` de Supabase (API REST)."""

    def __init__(self, url: str | None, key: str | None):
        self.url = (url or "").rstrip("/")
        self.key = key or ""

    @property
    def activo(self) -> bool:
        return bool(self.url and self.key)

    def _h(self):
        return {"apikey": self.key, "Authorization": f"Bearer {self.key}",
                "Content-Type": "application/json"}

    def guardar(self, fila: dict) -> None:
        if not self.activo:
            return
        r = requests.post(f"{self.url}/rest/v1/prechecks", json=fila,
                          headers={**self._h(), "Prefer": "return=minimal"}, timeout=15)
        r.raise_for_status()

    def buscar(self, codigo: str) -> dict | None:
        r = requests.get(f"{self.url}/rest/v1/prechecks",
                         params={"codigo": f"eq.{codigo}", "select": "*", "limit": "1"},
                         headers=self._h(), timeout=15)
        r.raise_for_status()
        filas = r.json()
        return filas[0] if filas else None

    def recientes(self, dias: int = 30) -> list[dict]:
        desde = (datetime.now(timezone.utc) - timedelta(days=dias)).isoformat()
        r = requests.get(f"{self.url}/rest/v1/prechecks",
                         params={"created_at": f"gte.{desde}", "documento": "eq.BCA",
                                 "select": "created_at,resultado,motivos", "limit": "10000"},
                         headers=self._h(), timeout=20)
        r.raise_for_status()
        return r.json()
