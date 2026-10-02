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
    """'395 GARCIA LANE, SAN LUIS, AZ, 85349, US' -> calle, ciudad, estado, zip."""
    partes = [p.strip() for p in (direccion or "").split(",") if p.strip()]
    if partes and partes[-1].upper() in ("US", "USA", "UNITED STATES"):
        partes = partes[:-1]
    if len(partes) < 4:
        return {"calle": direccion or "", "ciudad": "", "estado": "", "zip": ""}
    return {"calle": ", ".join(partes[:-3]), "ciudad": partes[-3],
            "estado": partes[-2], "zip": digitos(partes[-1])[:5]}


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
            raise CRMError("No encontré el formulario de inicio de sesión del CRM.")
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
            raise CRMError("El CRM rechazó el usuario o la contraseña configurados en la app.")
        self.s = s

    def _get(self, ruta: str, reintento: bool = True) -> requests.Response:
        if self.s is None:
            self._login()
        r = self.s.get(CRM_URL + ruta, timeout=30)
        if "/login" in r.url.lower():
            if reintento:
                self.s = None
                return self._get(ruta, reintento=False)
            raise CRMError("El CRM cerró la sesión y no se pudo volver a entrar.")
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
# Reglas de la BCA
# ============================================================
GRUPO_DA = "Driver Assignment"
GRUPO_CARRIER = "Carrier en el CRM"
GRUPO_MOTUS = "MOTUS"
GRUPO_RUTA = "Ruta"
GRUPO_BCA = "BCA existente"
ORDEN_GRUPOS = [GRUPO_DA, GRUPO_CARRIER, GRUPO_MOTUS, GRUPO_RUTA, GRUPO_BCA]


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


def _link_da(da_id):
    return f"{CRM_URL}/Admin/DriverAssignment/Details/{da_id}"


def _link_carrier(cid):
    return f"{CRM_URL}/Admin/Carrier/Details/{cid}"


def revisar_bca_previa(archivos, leer_pdf, carrier, motus) -> tuple[Check, dict | None]:
    """Busca BCAs en los archivos del Carrier y decide si alguna sigue vigente frente a MOTUS."""
    candidatos = [f for f in archivos
                  if "BCA" in f["tipo"].upper() or "BCA" in f["archivo"].upper()]
    if not candidatos:
        return Check(GRUPO_BCA, "Sin BCA previa del carrier", "ok",
                     "No hay ninguna BCA en los archivos del Carrier."), None

    mc_crm = digitos(carrier["mc"])
    ilegibles, desactualizadas = [], []
    for f in candidatos:
        texto = leer_pdf(f["guid"]) if f["guid"] else None
        if texto is None:
            ilegibles.append(f)
            continue
        t = norm(texto)
        diferencias = []
        if motus["legal"] and norm(motus["legal"]) not in t:
            diferencias.append("Legal Name")
        if not re.search(rf"(?<!\d){digitos(carrier['dot'])}(?!\d)", re.sub(r"\s", "", texto)):
            diferencias.append("DOT")
        if motus["calle"] and norm(motus["calle"]) not in t:
            diferencias.append("dirección")
        if mc_crm and mc_crm not in re.sub(r"\D", "", texto):
            diferencias.append("MC")
        firmada = any(p in f["archivo"].upper() for p in ("SIGNATURE", "SIGNED", "FIRMAD")) \
            or "BCA" in f["tipo"].upper()
        if not firmada:
            diferencias.append("firma")
        if not diferencias:
            return Check(
                GRUPO_BCA, "Ya existe una BCA vigente", "fail",
                f"{f['archivo']}" + (f", subida el {f['creado']}," if f["creado"] else "")
                + " está firmada y coincide con MOTUS.",
                "No hace falta pedir otra BCA. Usá la que ya está en los archivos del Carrier.",
                _link_carrier(carrier["_id"]), "Ver archivos del Carrier"), f
        desactualizadas.append((f, diferencias))

    if ilegibles:
        nombres = ", ".join(f["archivo"] for f in ilegibles)
        return Check(GRUPO_BCA, "BCA previa sin poder leer", "warn",
                     f"No se pudo leer el contenido de {nombres}.",
                     "Podés enviar la solicitud. BackOffice va a revisar esa BCA antes de procesarla.",
                     _link_carrier(carrier["_id"]), "Ver archivos del Carrier"), None

    f, dif = desactualizadas[0]
    return Check(GRUPO_BCA, "BCA previa desactualizada", "ok",
                 f"{f['archivo']} no coincide con MOTUS ({', '.join(dif)}). Corresponde enviar una nueva."), None


def evaluar_bca(shipment_id: int, shipment: dict | None, asignacion: dict | None,
                da: dict | None, carrier: dict | None, archivos: list[dict] | None,
                motus_data: dict | None, leer_pdf) -> ResultadoBCA:
    """Aplica el procedimiento de verificación de BCA de BackOffice. Función pura (sin red)."""
    res = ResultadoBCA(shipment_id, shipment, asignacion, da, carrier)
    add = res.checks.append

    if shipment is None:
        add(Check(GRUPO_DA, "Shipment encontrado", "fail",
                  f"No existe el Shipment S-{shipment_id:06d} en el CRM.",
                  "Revisá el número e intentá de nuevo."))
        return res

    # ---- 1. Driver Assignment ----
    if asignacion is None:
        add(Check(GRUPO_DA, "Driver asignado", "fail",
                  "El Shipment no tiene ningún driver en Drivers Assignments.",
                  "Asigná el driver en la sección Drivers Assignments del Shipment.",
                  f"{CRM_URL}/Admin/Shipments/Details/{shipment_id}", "Abrir el Shipment"))
        return res

    link_da = _link_da(asignacion["da_id"])
    add(Check(GRUPO_DA, "Driver asignado", "ok",
              f"{asignacion['da_nombre']}, {asignacion['driver'] or (da or {}).get('driver') or 'driver sin nombre'}"))

    status = (da or {}).get("status", "")
    if status.strip().lower() == "dispatched":
        add(Check(GRUPO_DA, "Driver Assignment en Dispatched", "ok", "Status: Dispatched"))
    else:
        add(Check(GRUPO_DA, "Driver Assignment en Dispatched", "fail",
                  f"Status actual: {status or 'vacío'}.",
                  f"Cambiá el status de {asignacion['da_nombre']} a Dispatched.",
                  link_da, f"Abrir {asignacion['da_nombre']}"))

    email = (da or {}).get("email", "").strip()
    if "@" in email:
        add(Check(GRUPO_DA, "Email en el Driver Assignment", "ok", email))
    else:
        add(Check(GRUPO_DA, "Email en el Driver Assignment", "fail",
                  "El Driver Assignment no tiene email.",
                  f"Agregá el email en {asignacion['da_nombre']}. A ese correo se envía la BCA.",
                  link_da, f"Abrir {asignacion['da_nombre']}"))

    # ---- 2. Carrier en el CRM ----
    if carrier is None:
        add(Check(GRUPO_CARRIER, "Carrier enlazado", "fail",
                  "El Driver Assignment no tiene un Carrier enlazado.",
                  "Seleccioná el Carrier en el Driver Assignment.", link_da, f"Abrir {asignacion['da_nombre']}"))
        return res

    link_car = _link_carrier(carrier["_id"])
    for etiqueta, clave, obligatorio_txt in [
        ("Company Name", "nombre", "Agregá el nombre de la empresa del carrier."),
        ("DOT", "dot", "El DOT es obligatorio para todos los carriers en STT. Agregalo en el Carrier."),
        ("Address", "direccion", "Agregá la dirección: tiene que ser la Principal Place of Business de MOTUS."),
    ]:
        valor = carrier.get(clave, "").strip()
        if valor:
            add(Check(GRUPO_CARRIER, etiqueta, "ok", valor))
        else:
            add(Check(GRUPO_CARRIER, etiqueta, "fail", "Está vacío en el Carrier.",
                      obligatorio_txt, link_car, "Abrir el Carrier"))
    mc_crm = digitos(carrier.get("mc"))
    add(Check(GRUPO_CARRIER, "MC", "ok" if mc_crm else "info",
              carrier["mc"] if mc_crm else "Sin MC. El MC es opcional; sin él la carga debe quedarse en el mismo estado."))

    dot = digitos(carrier.get("dot"))
    if not dot:
        return res

    # ---- 3. MOTUS ----
    if motus_data is None:
        add(Check(GRUPO_MOTUS, "DOT registrado en MOTUS", "fail",
                  f"El DOT {dot} no existe en MOTUS.",
                  "Confirmá el número de DOT con el carrier y corregilo en el Carrier.",
                  link_car, "Abrir el Carrier"))
        return res

    m = motus_resumen(motus_data)
    res.motus = m
    link_motus = MOTUS_WEB.format(dot=dot)

    if m["dot_activo"]:
        add(Check(GRUPO_MOTUS, "USDOT activo", "ok", f"USDOT {dot}: Active"))
    else:
        add(Check(GRUPO_MOTUS, "USDOT activo", "fail",
                  f"USDOT {dot}: {m['estado_dot'] or 'sin estado'}"
                  + (", con orden de Out of Service" if m["fuera_servicio"] else "") + ".",
                  "El carrier no está autorizado para operar. Asigná otro carrier.",
                  link_motus, "Ver en MOTUS"))

    if norm(carrier["nombre"]) == norm(m["legal"]):
        add(Check(GRUPO_MOTUS, "Company Name igual al Legal Name", "ok", m["legal"]))
    else:
        extra = " Coincide con el DBA, pero la BCA va a nombre legal." \
            if m["dba"] and norm(carrier["nombre"]) == norm(m["dba"]) else ""
        add(Check(GRUPO_MOTUS, "Company Name igual al Legal Name", "fail",
                  f"CRM: {carrier['nombre'] or 'vacío'}. MOTUS: {m['legal'] or 'sin Legal Name'}.{extra}",
                  f"Escribí el Company Name exactamente como en MOTUS: {m['legal']}.",
                  link_car, "Abrir el Carrier"))

    p = partir_direccion_crm(carrier["direccion"])
    igual = bool(m["direccion"]) and all([
        norm(p["calle"]) == norm(m["calle"]),
        norm(p["ciudad"]) == norm(m["ciudad"]),
        norm(p["estado"]) == norm(m["estado"]),
        p["zip"] == m["zip"],
    ])
    if igual:
        add(Check(GRUPO_MOTUS, "Dirección igual a la Principal Place of Business", "ok", m["direccion"]))
    else:
        add(Check(GRUPO_MOTUS, "Dirección igual a la Principal Place of Business", "fail",
                  f"CRM: {carrier['direccion'] or 'vacía'}. MOTUS: {m['direccion'] or 'sin dirección física'}.",
                  f"Copiá la dirección exactamente como aparece en MOTUS: {m['direccion']}. "
                  "No se aceptan abreviaturas distintas ni la dirección de correo.",
                  link_car, "Abrir el Carrier"))

    mc_activo = False
    if mc_crm:
        auth = motus_property(m, mc_crm)
        if auth is None:
            otros = ", ".join(a["docket"] for a in m["autoridades"] if a["tipo"] == AUTORIDAD_PROPERTY) or "ninguno"
            add(Check(GRUPO_MOTUS, "MC con autoridad Motor Carrier of Property", "fail",
                      f"El MC {mc_crm} del CRM no aparece en MOTUS para este DOT (MOTUS tiene: {otros}).",
                      "Corregí el MC en el Carrier o dejalo vacío si el carrier no tiene MC.",
                      link_car, "Abrir el Carrier"))
        elif auth["estado"] == "Active":
            mc_activo = True
            add(Check(GRUPO_MOTUS, "MC con autoridad Motor Carrier of Property", "ok",
                      f"{auth['docket']}: Active"))
        else:
            add(Check(GRUPO_MOTUS, "MC con autoridad Motor Carrier of Property", "warn",
                      f"{auth['docket']}: {auth['estado'] or 'sin estado'} en MOTUS. "
                      "Se revisa la regla de mismo estado."))

    # ---- 4. Ruta (solo sin MC o con MC inactivo) ----
    o, d = shipment["origen_estado"].strip().upper(), shipment["destino_estado"].strip().upper()
    ruta = f"{o or '?'} a {d or '?'}"
    if mc_activo:
        add(Check(GRUPO_RUTA, "Ruta permitida", "ok", f"De {ruta}. Con MC activo se permite mover carga entre estados."))
    elif not (o and d):
        add(Check(GRUPO_RUTA, "Ruta permitida", "fail",
                  "Al Shipment le falta el estado de origen o de destino.",
                  "Completá Origin y Destination en Route Information del Shipment.",
                  f"{CRM_URL}/Admin/Shipments/Details/{shipment_id}", "Abrir el Shipment"))
    elif o == d:
        add(Check(GRUPO_RUTA, "Ruta permitida", "ok", f"De {ruta}: la carga no sale del estado."))
    else:
        en_motus = motus_property(m)
        if not mc_crm and en_motus and en_motus["estado"] == "Active":
            solucion = (f"MOTUS muestra {en_motus['docket']} activo para este carrier. "
                        "Agregalo en el Carrier y volvé a verificar.")
        else:
            solucion = ("Sin MC activo, el carrier solo puede mover carga dentro de un mismo estado. "
                        "Asigná un carrier con MC activo o corregí el MC del Carrier.")
        add(Check(GRUPO_RUTA, "Ruta permitida", "fail",
                  f"La carga va de {ruta} y el carrier no tiene MC activo.",
                  solucion, link_car, "Abrir el Carrier"))

    # ---- 5. BCA existente ----
    if archivos is None:
        add(Check(GRUPO_BCA, "Archivos del Carrier", "warn",
                  "No se pudo leer la sección de archivos del Carrier.",
                  "Podés enviar la solicitud. BackOffice va a revisar si ya hay una BCA.",
                  link_car, "Ver archivos del Carrier"))
    else:
        chk, previa = revisar_bca_previa(archivos, leer_pdf, carrier, m)
        res.bca_previa = previa
        add(chk)
    res.motus = m
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
