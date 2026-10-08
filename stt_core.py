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
import threading
import time
from dataclasses import dataclass, field
from datetime import date, datetime, timedelta, timezone

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


class MotusNoDisponible(Exception):
    """MOTUS no respondió o rechazó la consulta (por ejemplo, error 403). Ver bitácora B-023."""


_motus_sesion: requests.Session | None = None
_motus_lock = threading.Lock()


def _sesion_motus(nueva: bool = False) -> requests.Session:
    """Sesión que se presenta como un visitante de la página pública de MOTUS:
    primero abre la página de búsqueda (para recibir sus cookies) y luego consulta los datos."""
    global _motus_sesion
    with _motus_lock:
        if _motus_sesion is None or nueva:
            s = requests.Session()
            s.headers.update({
                "user-agent": UA,
                "accept-language": "en-US,en;q=0.9",
            })
            try:
                s.get("https://motus.dot.gov/public/search", timeout=20,
                      headers={"accept": "text/html,application/xhtml+xml,application/xml;q=0.9,*/*;q=0.8"})
            except requests.RequestException:
                pass
            _motus_sesion = s
        return _motus_sesion


def motus_consultar(dot: str) -> dict | None:
    """Datos del carrier en MOTUS. None si el DOT no existe; MotusNoDisponible si MOTUS no responde."""
    ultimo = ""
    for intento in range(3):
        s = _sesion_motus(nueva=intento > 0)
        try:
            r = s.get(MOTUS_API.format(dot=dot), timeout=30, headers={
                "accept": "application/json, text/plain, */*",
                "referer": "https://motus.dot.gov/public/search",
                "sec-fetch-site": "same-origin",
                "sec-fetch-mode": "cors",
                "sec-fetch-dest": "empty",
            })
        except requests.RequestException as ex:
            ultimo = type(ex).__name__
        else:
            if r.status_code == 404:
                return None
            if r.ok:
                try:
                    return r.json() or None
                except ValueError:
                    ultimo = "the response was not carrier data"
            else:
                ultimo = f"HTTP {r.status_code}"
        time.sleep(1.5 * (intento + 1))
    raise MotusNoDisponible(ultimo)


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

    correo = next((l for l in locs if l.get("addressTypeId") == TIPO_DIR_CORREO), None)
    calle_correo = " ".join(p for p in [(correo or {}).get("addressLine1"), (correo or {}).get("addressLine2")] if p)
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
        "calle_correo": calle_correo,
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


def _texto_campo(soup: BeautifulSoup, campo_for: str) -> str:
    """Texto de un campo respetando los saltos de línea (Special Instructions)."""
    lab = soup.find("label", attrs={"for": campo_for})
    caja = lab.find_parent(class_="row").find(class_="form-text-row") if lab else None
    return caja.get_text("\n", strip=True) if caja else ""


def _id_enlace(soup: BeautifulSoup, patron: str) -> int | None:
    a = soup.select_one("main") and soup.select_one("main").find("a", href=re.compile(patron))
    return int(re.search(patron, a["href"]).group(1)) if a else None


def _requests_shipment(soup: BeautifulSoup) -> list[dict]:
    """Bloque Requests del Shipment: id, tipo, documento y respuesta."""
    salida = []
    bloque = soup.find(id="order-Request")
    for item in (bloque.select(".request-info-itembox") if bloque else []):
        a = item.find("a", href=re.compile(r"/Admin/Request/Details/\d+"))
        campos = {}
        for li in item.find_all("li"):
            lab = li.find("label")
            if lab:
                campos[lab.get_text(strip=True).rstrip(" :").strip()] = li.get_text(" ", strip=True)[len(lab.get_text(" ", strip=True)):].strip()
        salida.append({"id": a.get_text(strip=True) if a else "", "tipo": campos.get("Request Type", ""),
                       "documento": campos.get("Document", ""), "respuesta": campos.get("Response", "")})
    return salida


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
            "driver_id": int(re.search(r"/DriverDetails/(\d+)", a_drv["href"]).group(1)) if a_drv else None,
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
        # --- datos para Load Confirmation ---
        "fecha_pu": por_for.get("EstimatedPickUpDate", ""),
        "fecha_entrega": por_for.get("EstimatedDeliveryDate", ""),
        "internacional": "✔" in por_for.get("International", ""),
        "supervisor": por_for.get("Supervisor", ""),
        "pago_tipo": por_for.get("PaymentType", ""),
        "carrier_pay": por_for.get("CarrierPay", ""),
        "broker_pays_carrier": por_for.get("BrokerPaysCarrier", ""),
        "truck_type": por_for.get("TruckType", ""),
        "special_instructions": _texto_campo(soup, "SpecialInstruction"),
        "orden_id": _id_enlace(soup, r"/Admin/Orders/Details/(\d+)"),
        "requests": _requests_shipment(soup),
        "dispatchers_externos": len((soup.find(id="order-DispatcherAssignment") or BeautifulSoup("", "html.parser"))
                                    .select(".request-info-itembox")),
        "_token": (soup.find("input", {"name": "__RequestVerificationToken"}) or {}).get("value", ""),
    }


def parse_driver_assignment(soup: BeautifulSoup) -> dict:
    texto, _ = leer_campos(soup)
    return {
        "status": texto.get("Status", ""),
        "email": texto.get("Email", ""),
        "driver": texto.get("Driver Name", ""),
        "carrier": texto.get("Carrier", ""),
        "dot": texto.get("DOT", ""),
        "telefono": texto.get("Driver Phone", ""),
        "send_to_app": "✔" in texto.get("Send To App", ""),
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


def parse_conductores(js: dict) -> list[dict]:
    """Sección Driver Information del Carrier (POST /Admin/Driver/DriverList)."""
    filas = js.get("Data") or js.get("data") or []
    return [{
        "id": f.get("Id"),
        "nombre": " ".join(str(f.get("Name") or "").split()),
        "email": f.get("EmailID") or "",
        "terms": str(f.get("TermsStatus") if f.get("TermsStatus") is not None else "").strip(),
        "dot_status": str(f.get("DOTStatus") if f.get("DOTStatus") is not None else "").strip(),
        "stripe_status": str(f.get("StripeStatus") if f.get("StripeStatus") is not None else "").strip(),
        "estado": f.get("DriverStatus") or "",
    } for f in filas]


def parse_load(soup: BeautifulSoup) -> dict:
    _, f = leer_campos(soup)
    return {
        "numero": f.get("CustomLoadId", ""), "tipo": f.get("LoadType", ""), "cantidad": f.get("Quantity", ""),
        "year": f.get("Year", ""), "make": f.get("Make", ""), "model": f.get("Model1", ""),
        "length": f.get("Length", ""), "width": f.get("Width", ""), "height": f.get("Height", ""),
        "weight": f.get("Weight", ""), "empty": f.get("Empty", ""), "cargo": f.get("Cargo", ""),
        "hitch": f.get("Hitch", ""), "lot": f.get("LotNumber", ""),
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

    def conductores_carrier(self, carrier_id: int, token: str) -> list[dict]:
        r = self.s.post(
            f"{CRM_URL}/Admin/Driver/DriverList?CarrierId={carrier_id}",
            data={"draw": "1", "start": "0", "length": "500", "__RequestVerificationToken": token},
            headers={"X-Requested-With": "XMLHttpRequest",
                     "Referer": f"{CRM_URL}/Admin/Carrier/Details/{carrier_id}"},
            timeout=30,
        )
        r.raise_for_status()
        return parse_conductores(r.json())

    def loads_shipment(self, shipment_id: int, token: str) -> list[dict]:
        """Sección Loads del Shipment (POST /Admin/Order/OrderLoadList)."""
        r = self.s.post(
            f"{CRM_URL}/Admin/Order/OrderLoadList?OrderId={shipment_id}",
            data={"draw": "1", "start": "0", "length": "100", "__RequestVerificationToken": token},
            headers={"X-Requested-With": "XMLHttpRequest",
                     "Referer": f"{CRM_URL}/Admin/Shipments/Details/{shipment_id}"},
            timeout=30,
        )
        r.raise_for_status()
        filas = r.json().get("Data") or []
        return [{"id": f.get("LoadId"), "numero": f.get("LoadNumber") or ""} for f in filas if f.get("LoadId")]

    def load(self, load_id: int) -> dict:
        return parse_load(self._pagina(f"/Admin/Loads/Details/{load_id}"))

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
    terminos_app: dict | None = None     # fila de Driver Information con Terms Status True
    motus_error: str | None = None       # MOTUS no respondió: no se puede aprobar
    checks: list[Check] = field(default_factory=list)

    documento = "BCA"

    @property
    def grupos(self) -> list[str]:
        return ORDEN_GRUPOS

    @property
    def fallas(self) -> list[Check]:
        return [c for c in self.checks if c.estado == "fail"]

    @property
    def veredicto(self) -> str:
        if any(c.grupo == GRUPO_BCA for c in self.fallas):
            return "YA_EXISTE"
        if self.motus_error:
            return "SIN_VERIFICAR"
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


def driver_firmo_en_app(conductores: list[dict] | None, asignacion: dict | None,
                        da: dict | None) -> tuple[str, dict | None]:
    """Excepción de BackOffice (B-021), válida para BCA y Load Confirmation.

    Si el driver del Driver Assignment aparece en Driver Information del Carrier con
    Terms Status = True, firmó los términos en la app de STT y no hace falta una BCA nueva.
    No importa si "Send To App" está marcado en el Driver Assignment.

    Devuelve ('si' | 'no' | 'no_listado' | 'desconocido', fila del driver).
    """
    if conductores is None or asignacion is None:
        return "desconocido", None
    fila = None
    if asignacion.get("driver_id"):
        fila = next((c for c in conductores if c["id"] == asignacion["driver_id"]), None)
    if fila is None:
        nombre = norm(asignacion.get("driver") or (da or {}).get("driver"))
        fila = next((c for c in conductores if nombre and norm(c["nombre"]) == nombre), None)
    if fila is None:
        return "no_listado", None
    return ("si" if fila["terms"].lower() in ("true", "1", "yes") else "no"), fila


def evaluar_bca(shipment_id: int, shipment: dict | None, asignacion: dict | None,
                da: dict | None, carrier: dict | None, archivos: list[dict] | None,
                motus_data: dict | None, leer_pdf, conductores: list[dict] | None = None,
                motus_error: str | None = None) -> ResultadoBCA:
    """Procedimiento de BCA + excepción de términos firmados en la app."""
    estado_app, fila = driver_firmo_en_app(conductores, asignacion, da) if carrier else ("desconocido", None)
    res = _evaluar_bca_base(shipment_id, shipment, asignacion, da, carrier, archivos, motus_data, leer_pdf,
                            revisar_archivos=(estado_app != "si"), motus_error=motus_error)
    if carrier is None or asignacion is None:
        return res
    link_car = _link_carrier(carrier["_id"])
    nombre = (fila or {}).get("nombre") or asignacion.get("driver") or "The driver"
    if estado_app == "si":
        res.terminos_app = fila
        res.checks.append(Check(
            GRUPO_BCA, "Driver accepted the terms in the STT app", "fail",
            f"{nombre} is listed in the carrier's Driver Information with Terms Status: True. "
            "The driver signed through the app, so it counts as a signed BCA. "
            "The Send To App box on the Driver Assignment does not need to be checked.",
            "No new BCA is needed. The driver already accepted the terms in the STT app.",
            link_car, "View Driver Information"))
        return res
    if estado_app in ("no", "no_listado"):
        detalle = (f"{nombre} is listed in Driver Information with Terms Status: {fila['terms'] or 'blank'}."
                   if estado_app == "no" else
                   f"{nombre} is not listed in the carrier's Driver Information.")
        info = Check(GRUPO_BCA, "Terms accepted in the STT app", "info",
                     detalle + " The app exception does not apply, so the BCA files are checked.")
        pos = next((i for i, c in enumerate(res.checks) if c.grupo == GRUPO_BCA), len(res.checks))
        if pos < len(res.checks):
            res.checks.insert(pos, info)
    return res


def _evaluar_bca_base(shipment_id: int, shipment: dict | None, asignacion: dict | None,
                da: dict | None, carrier: dict | None, archivos: list[dict] | None,
                motus_data: dict | None, leer_pdf, revisar_archivos: bool = True,
                motus_error: str | None = None) -> ResultadoBCA:
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
    if motus_error:
        res.motus_error = motus_error
        add(Check(GRUPO_MOTUS, "MOTUS lookup", "warn",
                  f"MOTUS did not respond to the app ({motus_error}), so the carrier could not be verified.",
                  "Try again in a few minutes. If it keeps happening, let BackOffice know.",
                  MOTUS_WEB.format(dot=dot), "Open in MOTUS"))
        return res
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
    if not revisar_archivos:
        return res      # el driver firmó en la app (lo agrega evaluar_bca)
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
        carrier = archivos = motus_data = conductores = motus_error = None
        if asig["carrier_id"]:
            carrier = crm.carrier(asig["carrier_id"])
            carrier["_id"] = asig["carrier_id"]
            try:
                archivos = crm.archivos_carrier(asig["carrier_id"], carrier["_token"])
            except Exception:
                archivos = None
            try:
                conductores = crm.conductores_carrier(asig["carrier_id"], carrier["_token"])
            except Exception:
                conductores = None
            dot = digitos(carrier["dot"])
            try:
                motus_data = motus_fn(dot) if dot else None
            except MotusNoDisponible as ex:
                motus_error = str(ex) or "no response"

        def leer_pdf(guid):
            try:
                return texto_pdf(crm.descargar(guid))
            except Exception:
                return None

        resultados.append(evaluar_bca(shipment_id, shipment, asig, da, carrier,
                                      archivos, motus_data, leer_pdf, conductores, motus_error))
    return resultados


# ============================================================
# Load Confirmation (LC)
# Procedimiento: "Load Confirmation" de BackOffice (bitácora B-024).
# ============================================================
GRUPO_INFO = "Shipment info"
GRUPO_LOADS = "Loads"
GRUPO_PAGO = "Payment"
GRUPO_TRUCK = "Truck"
GRUPO_SI = "Special Instructions"
GRUPO_DOCS = "Carrier documents"
GRUPO_APP = "STT app driver"
GRUPO_MANUAL = "BackOffice reviews manually"
ORDEN_GRUPOS_LC = [GRUPO_INFO, GRUPO_LOADS, GRUPO_PAGO, GRUPO_TRUCK, GRUPO_SI, GRUPO_DA, GRUPO_CARRIER,
                   GRUPO_MOTUS, GRUPO_RUTA, GRUPO_DOCS, GRUPO_APP, GRUPO_MANUAL]
REQUISITOS_LC = 24

PLANTILLAS_LC = [("broker", "LoadConfirmation.BB"), ("cod", "LoadConfirmation.COD"),
                 ("otr", "LoadConfirmation.OTR"), ("joint", "LoadConfirmation.JL")]
ESTADOS_POSITIVOS = {"true", "approved", "active", "verified", "valid", "complete", "completed", "yes", "connected"}

# Tipos de camión (nombre canónico -> formas en que se escriben)
TIPOS_CAMION = {
    "RGNE": [r"\bRGNE\b"], "RGN": [r"\bRGN\b"], "DOUBLE DROP": [r"double[\s-]?drop"],
    "STEP DECK": [r"step[\s-]?deck", r"\bSDL\b"], "DRY VAN": [r"dry[\s-]?van"],
    "REEFER": [r"\breefer\b", r"\brefeer\b", r"refrigerated"], "FLATBED": [r"flat[\s-]?bed"],
    "POWER ONLY": [r"power[\s-]?only"], "LOWBOY": [r"low[\s-]?boy"], "HOT SHOT": [r"hot[\s-]?shot"],
    "FLAT RACK": [r"flat[\s-]?rack"], "BEAM TRAILER": [r"beam[\s-]?trailer"], "BOX TRUCK": [r"box[\s-]?truck"],
    "STRAIGHT VAN": [r"straight[\s-]?van"], "TILT BED": [r"tilt[\s-]?bed"], "CAR HAULER": [r"car[\s-]?hauler"],
    "CONESTOGA": [r"conestoga"], "DRIVE AWAY": [r"drive[\s-]?away"],
}
MOTORIZADOS = r"vehicle|\bcar\b|\bcars\b|truck|\bsuv\b|motorcycle|\bboat|\brv\b|tractor|equipment|machinery|forklift|excavator|loader|\bbus\b|\batv\b|\butv\b|golf cart|jet ?ski|trailer"


@dataclass
class ResultadoLC(ResultadoBCA):
    plantilla: str = ""
    caso: str = "Standard"
    loads: list = field(default_factory=list)
    documento = "LC"

    @property
    def grupos(self) -> list[str]:
        return ORDEN_GRUPOS_LC

    @property
    def veredicto(self) -> str:
        if self.motus_error:
            return "SIN_VERIFICAR"
        return "NO_ENVIAR" if self.fallas else "AUTORIZADO"

    @property
    def cumplimiento(self) -> tuple[str, int, int]:
        return nivel_cumplimiento([c for c in self.checks if c.grupo != GRUPO_MANUAL], REQUISITOS_LC)


def _num(texto) -> float:
    try:
        return float(re.sub(r"[^\d.\-]", "", str(texto or "")) or 0)
    except ValueError:
        return 0.0


def _fecha(texto) -> date | None:
    m = re.search(r"(\d{1,2})/(\d{1,2})/(\d{2,4})", texto or "")
    if not m:
        return None
    mes, dia, anio = map(int, m.groups())
    anio += 2000 if anio < 100 else 0
    try:
        return date(anio, mes, dia)
    except ValueError:
        return None


def _fecha_cerca(texto: str, claves: list[str]) -> date | None:
    """Primera fecha que aparece justo después de alguna de las palabras clave."""
    for clave in claves:
        for m in re.finditer(clave, texto, re.I):
            f = _fecha(texto[m.end(): m.end() + 60])
            if f:
                return f
    return None


def _fmt(d: date | None) -> str:
    return f"{d.month}/{d.day}/{d.year}" if d else "not found"


def _telefonos(texto: str) -> list[str]:
    return [digitos(t)[-10:] for t in re.findall(r"\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{4}", texto or "")]


def tipo_camion(texto: str) -> list[str]:
    return [canon for canon, pats in TIPOS_CAMION.items() if any(re.search(p, texto or "", re.I) for p in pats)]


def plantilla_lc(pago: str) -> str:
    p = (pago or "").lower()
    return next((plant for clave, plant in PLANTILLAS_LC if clave in p), "")


def _es_positivo(valor: str) -> bool:
    return (valor or "").strip().lower() in ESTADOS_POSITIVOS


def revisar_loads(loads: list[dict] | None, link_ship: str) -> list[Check]:
    if loads is None:
        return [Check(GRUPO_LOADS, "Loads", "warn", "The shipment's Loads section could not be read.",
                      "You can submit the request. BackOffice will review the loads.", link_ship, "Open shipment")]
    if not loads:
        return [Check(GRUPO_LOADS, "At least one load", "fail", "The shipment has no loads.",
                      "Add the load in the Loads section of the shipment.", link_ship, "Open shipment")]
    checks = [Check(GRUPO_LOADS, "At least one load", "ok", ", ".join(l["numero"] for l in loads))]
    for l in loads:
        n = l["numero"] or "Load"
        link = f"{CRM_URL}/Admin/Loads/Details/{l.get('_id')}" if l.get("_id") else link_ship
        tipo = (l["tipo"] or "").lower()
        desc = f"{l['make']} {l['model']}".strip()
        add = lambda titulo, ok, detalle, sol: checks.append(
            Check(GRUPO_LOADS, f"{n}: {titulo}", "ok" if ok else "fail", detalle, "" if ok else sol, link, f"Open {n}"))
        add("load type and quantity", bool(l["tipo"]) and _num(l["cantidad"]) > 0,
            f"Load type: {l['tipo'] or 'blank'}. Quantity: {l['cantidad'] or 'blank'}.",
            f"Complete Load Type and Quantity on {n}.")
        add("make and model", bool(desc), desc or "Make and Model are blank.",
            f"Describe what is being moved in Make and Model on {n}.")
        if "pallet" in tipo or "crate" in tipo:
            add("pallets or crates described", bool(re.search(r"pallet|crate", desc, re.I)), desc or "blank",
                f"The load type is {l['tipo']}. Make and Model must say whether it moves on pallets or in crates.")
        if re.search(MOTORIZADOS, tipo):
            add("year", _num(l["year"]) > 1900, f"Year: {l['year'] or 'blank'}.",
                f"Add the year on {n}. It is required for loads with an engine.")
        if "vehicle" not in tipo:
            dims = {"Length": l["length"], "Width": l["width"], "Height": l["height"], "Weight": l["weight"]}
            faltan = [k for k, v in dims.items() if _num(v) <= 0]
            add("dimensions and weight", not faltan,
                f"{_num(l['length']):g} ft × {_num(l['width']):g} ft × {_num(l['height']):g} ft, {_num(l['weight']):,.0f} lbs.",
                f"Add {', '.join(faltan)} on {n}. Dimensions are required for every load except vehicles.")
        if "container" in tipo:
            vacio = (l["empty"] or "").strip().lower()
            ok_vacio = vacio in ("yes", "no", "not")
            add("empty or loaded", ok_vacio, f"Empty: {l['empty'] or 'blank'}.",
                f"Set Empty to YES or NO on {n}.")
            if vacio in ("no", "not"):
                add("container contents", bool(l["cargo"] or desc), l["cargo"] or desc or "blank",
                    f"Describe what the container carries in Cargo or in Make and Model on {n}.")
        if (l["hitch"] or "").strip().lower() in ("yes", "true", "✔", "y"):
            add("hitch type", False, f"Hitch: {l['hitch']}.", f"Specify the hitch type on {n}.")
    return checks


def revisar_pago(s: dict, link_ship: str) -> list[Check]:
    checks = []
    pago = s.get("pago_tipo", "")
    cp, bpc = _num(s.get("carrier_pay")), _num(s.get("broker_pays_carrier"))
    plant = plantilla_lc(pago)
    if not plant:
        return [Check(GRUPO_PAGO, "Payment type", "fail", f"Payment type: {pago or 'blank'}.",
                      "Select the payment type in Transfer Specifications. It decides which LC is sent.",
                      link_ship, "Open shipment")]
    checks.append(Check(GRUPO_PAGO, "Payment type", "ok", f"{pago}. LC template: {plant}."))
    p = pago.lower()
    montos = f"Carrier Pay ${cp:,.2f}. Broker Pays Carrier ${bpc:,.2f}."
    if "broker" in p:
        ok, sol = cp > 0 and abs(cp - bpc) < 0.01, "With Pay by Broker, Carrier Pay and Broker Pays Carrier must be the same amount."
    elif "cod" in p:
        ok, sol = cp > 0 and bpc == 0, "With COD, only Carrier Pay is filled. Broker Pays Carrier must be $0."
    elif "otr" in p:
        ok, sol = cp > 0 and bpc == 0, "With OTR, only Carrier Pay is filled. STT does not pay the driver."
    else:  # joint
        ok, sol = cp > 0 and 0 < bpc < cp, ("With Joint, Carrier Pay is the total (customer + STT) and Broker "
                                            "Pays Carrier is only the part STT pays.")
    checks.append(Check(GRUPO_PAGO, "Amounts match the payment type", "ok" if ok else "fail", montos,
                        "" if ok else sol, link_ship, "Open shipment"))
    if "cod" in p:
        overrides = [r for r in s.get("requests", []) if "override" in f"{r['tipo']} {r['documento']}".lower()]
        rechazado = any(re.search(r"reject|denied|declin", r["respuesta"], re.I) for r in overrides)
        ok = bool(overrides) and not rechazado
        checks.append(Check(GRUPO_PAGO, "Override for COD", "ok" if ok else "fail",
                            ", ".join(f"{r['id']} ({r['respuesta'] or 'no response'})" for r in overrides)
                            or "There is no override request on the shipment.",
                            "" if ok else "COD requires an override approved by the supervisor in Requests.",
                            link_ship, "Open shipment"))
    if s.get("internacional"):
        ok = "broker" in p
        checks.append(Check(GRUPO_PAGO, "International load is Pay by Broker", "ok" if ok else "fail",
                            f"International shipment. Payment type: {pago}.",
                            "" if ok else "International (Naviera) loads must always be Pay by Broker.",
                            link_ship, "Open shipment"))
    return checks


def revisar_si(s: dict, da: dict | None, link_ship: str) -> list[Check]:
    si = s.get("special_instructions", "")
    if not si.strip():
        return [Check(GRUPO_SI, "Special Instructions", "fail", "Special Instructions are blank.",
                      "Complete the Special Instructions with the driver, dates, and broker contact.",
                      link_ship, "Open shipment")]
    checks = []
    add = lambda titulo, estado, detalle, sol="": checks.append(
        Check(GRUPO_SI, titulo, estado, detalle, sol if estado != "ok" else "", link_ship, "Open shipment"))

    # Fechas
    for etiqueta, claves, campo in [
        ("Pick-up date", [r"pick[\s-]?up date", r"\bPU date", r"pick[\s-]?up"], "fecha_pu"),
        ("Delivery date", [r"deliver(?:y|ies)? date", r"drop[\s-]?off date", r"\bDEL date", r"deliver"], "fecha_entrega"),
    ]:
        en_si, en_info = _fecha_cerca(si, claves), _fecha(s.get(campo))
        if en_si is None:
            add(f"{etiqueta} in Special Instructions", "fail", f"No {etiqueta.lower()} found in Special Instructions.",
                f"Add the {etiqueta.lower()} to Special Instructions. It must match Shipment Info.")
        elif en_info is None:
            add(f"{etiqueta} matches Shipment Info", "fail", f"Special Instructions: {_fmt(en_si)}. Shipment Info: blank.",
                f"Complete the estimated {etiqueta.lower()} in Shipment Info.")
        else:
            add(f"{etiqueta} matches Shipment Info", "ok" if en_si == en_info else "fail",
                f"Special Instructions: {_fmt(en_si)}. Shipment Info: {_fmt(en_info)}.",
                f"The {etiqueta.lower()} must be the same in Special Instructions and Shipment Info.")

    # Driver: nombre y teléfono iguales al Driver Assignment
    if da:
        m = (re.search(r"driver\s*[:\-]?\s*\(((?:[^()]|\([^()]*\))*)\)", si, re.I)
             or re.search(r"driver\s*[:\-]\s*([^\n]+)", si, re.I))
        tramo = m.group(1) if m else si
        nombres = [t for t in norm(da.get("driver")).split() if len(t) >= 3]
        ok_nombre = bool(nombres) and any(re.search(rf"\b{t}\b", norm(tramo)) for t in nombres)
        add("Driver name matches the Driver Assignment", "ok" if ok_nombre else "fail",
            f"Driver Assignment: {da.get('driver') or 'blank'}." + (f" Special Instructions: {tramo.strip()[:80]}." if m else ""),
            "Write the driver's name in Special Instructions exactly as on the Driver Assignment.")
        tel = digitos(da.get("telefono"))[-10:]
        ok_tel = bool(tel) and tel in _telefonos(si)
        add("Driver phone matches the Driver Assignment", "ok" if ok_tel else "fail",
            f"Driver Assignment: {da.get('telefono') or 'blank'}.",
            "Write the driver's phone in Special Instructions. It must match the Driver Phone on the Driver Assignment.")

    # Contacto del broker o dispatcher
    correos = re.findall(r"[\w.+\-]+@[\w\-]+\.[\w.]+", si)
    tel_driver = digitos((da or {}).get("telefono"))[-10:]
    otros_tel = [t for t in _telefonos(si) if t != tel_driver]
    permitidos = {"Shipment Owner": s.get("owner", ""), "Dispatcher": s.get("dispatcher", "")}
    sup = norm(s.get("supervisor"))
    if "CANDI FUENTES" in sup:
        permitidos["Calvin (Candi Fuentes franchise)"] = "Calvin"
    if "RON SANCHEZ" in sup:
        permitidos["Gabriela Salazar (Ron Sanchez franchise)"] = "Gabriela Salazar"
    if not correos or not otros_tel:
        falta = " and ".join(x for x, ok in [("email", correos), ("phone", otros_tel)] if not ok)
        add("Broker or dispatcher contact", "fail", f"No broker or dispatcher {falta} found in Special Instructions.",
            "Add the email and phone of the broker or dispatcher handling the shipment to Special Instructions.")
    else:
        quien = next((rol for rol, nombre in permitidos.items()
                      if nombre and any(len(t) >= 3 and t.lower() in c.lower() for c in correos for t in nombre.split())), None)
        if quien:
            add("Broker or dispatcher contact", "ok", f"{', '.join(correos)} ({quien}).")
        else:
            add("Broker or dispatcher contact", "warn", f"Email found: {', '.join(correos)}.",
                "BackOffice will confirm this contact belongs to the broker or dispatcher handling the shipment.")

    # Truck type mencionado en Special Instructions
    en_si = tipo_camion(si)
    campo = tipo_camion(s.get("truck_type", ""))
    if en_si and campo and not set(en_si) & set(campo):
        add("Truck type matches Special Instructions", "fail",
            f"Truck Specifications: {s.get('truck_type')}. Special Instructions mention: {', '.join(en_si)}.",
            "The truck type in Special Instructions must match Truck Specifications.")
    return checks


def revisar_coi(archivos: list[dict] | None, leer_pdf, m: dict, s: dict, link_car: str) -> Check:
    if archivos is None:
        return Check(GRUPO_DOCS, "Certificate of Insurance (COI)", "warn", "The carrier's files could not be read.",
                     "You can submit the request. BackOffice will review the COI.", link_car, "View carrier files")
    cands = [f for f in archivos if "COI" in f["tipo"].upper() or "INSURANCE" in f["tipo"].upper()
             or re.search(r"\bCOI\b|INSURANCE|CERTIFICATE", f["archivo"].upper())]
    if not cands:
        return Check(GRUPO_DOCS, "Certificate of Insurance (COI)", "fail", "There is no COI in the carrier's files.",
                     "Upload the carrier's current COI to the carrier's files.", link_car, "Open carrier")
    entrega = _fecha(s.get("fecha_entrega"))
    camion = s.get("truck_type", "").upper()
    if "CAR HAULER" in camion or "AUTO" in camion:
        coberturas = ["AUTOMOBILE LIABILITY"]
    elif "DRIVE AWAY" in camion:
        coberturas = ["DRIVE AWAY"]
    elif "POWER ONLY" in camion:
        coberturas = ["ON HOOK", "CARGO"]
    else:
        coberturas = ["CARGO"]
    mejor = None
    for f in cands:
        texto = leer_pdf(f["guid"]) if f["guid"] else None
        if texto is None:
            continue
        t = norm(texto)
        dir_fis = bool(m["calle"]) and norm(m["calle"]) in t
        dir_corr = bool(m.get("calle_correo")) and norm(m["calle_correo"]) in t
        fechas = [d for d in (_fecha(x) for x in re.findall(r"\d{1,2}/\d{1,2}/\d{2,4}", texto)) if d]
        vence = max(fechas) if fechas else None
        revision = [
            ("legal name", bool(m["legal"]) and norm(m["legal"]) in t),
            ("MOTUS address" + (" (mailing)" if dir_corr and not dir_fis else ""), dir_fis or dir_corr),
            (f"{' or '.join(c.title() for c in coberturas)} coverage", any(norm(c) in t for c in coberturas)),
            (f"valid 10+ days after delivery (latest date {_fmt(vence)})",
             bool(vence and entrega and vence >= entrega + timedelta(days=10)) if entrega else bool(vence)),
        ]
        if not (dir_fis or dir_corr):
            revision.append(("STT as certificate holder", "STT LOGISTICS" in t))
        malos = [n for n, ok in revision if not ok]
        resumen = ". ".join(f"{n[0].upper() + n[1:]}: {'yes' if ok else 'no'}" for n, ok in revision) + "."
        if mejor is None or len(malos) < len(mejor[1]):
            mejor = (f, malos, resumen)
        if not malos:
            break
    if mejor is None:
        return Check(GRUPO_DOCS, "Certificate of Insurance (COI)", "warn",
                     f"{', '.join(f['archivo'] for f in cands)} could not be read automatically.",
                     "You can submit the request. BackOffice will review the COI.", link_car, "View carrier files")
    f, malos, resumen = mejor
    link = f"{CRM_URL}/Admin/Download/DownloadFile?downloadGuid={f['guid']}"
    if not malos:
        return Check(GRUPO_DOCS, "Certificate of Insurance (COI)", "ok", f"{f['archivo']}. {resumen}", "", link, "Open COI")
    return Check(GRUPO_DOCS, "Certificate of Insurance (COI)", "fail", f"{f['archivo']}. {resumen}",
                 f"Get an updated COI from the carrier. Missing: {', '.join(malos)}.", link, "Open COI")


def evaluar_lc(shipment_id: int, shipment: dict | None, asignacion: dict | None, da: dict | None,
               carrier: dict | None, archivos: list[dict] | None, motus_data: dict | None, leer_pdf,
               conductores: list[dict] | None = None, loads: list[dict] | None = None,
               motus_error: str | None = None) -> ResultadoLC:
    """Aplica el procedimiento de Load Confirmation de BackOffice. Función pura (sin red)."""
    res = ResultadoLC(shipment_id, shipment, asignacion, da, carrier, loads=loads or [])
    add = res.checks.append
    if shipment is None:
        add(Check(GRUPO_INFO, "Shipment found", "fail", f"Shipment S-{shipment_id:06d} does not exist in the CRM.",
                  "Check the shipment number and try again."))
        return res
    s = shipment
    link_ship = f"{CRM_URL}/Admin/Shipments/Details/{shipment_id}"
    res.plantilla = plantilla_lc(s.get("pago_tipo"))

    # 1. Shipment info
    for etiqueta, campo in [("Estimated pick-up date", "fecha_pu"), ("Estimated delivery date", "fecha_entrega")]:
        f = _fecha(s.get(campo))
        add(Check(GRUPO_INFO, etiqueta, "ok" if f else "fail", _fmt(f) if f else "Blank.",
                  "" if f else f"Complete the {etiqueta.lower()} in Shipment Info.", link_ship, "Open shipment"))

    # 2. Loads, 3. Payment, 4-5. Special Instructions y truck
    res.checks += revisar_loads(loads, link_ship)
    res.checks += revisar_pago(s, link_ship)
    add(Check(GRUPO_TRUCK, "Truck type", "ok" if s.get("truck_type") else "fail", s.get("truck_type") or "Blank.",
              "" if s.get("truck_type") else "Select the truck type in Truck Specifications.", link_ship, "Open shipment"))
    si_checks = revisar_si(s, da if asignacion else None, link_ship)
    for c in si_checks:
        if c.titulo == "Truck type matches Special Instructions":
            c.grupo = GRUPO_TRUCK
    res.checks += si_checks

    # 6. Driver Assignment, Carrier, MOTUS y ruta: mismas reglas que la BCA
    base = _evaluar_bca_base(shipment_id, shipment, asignacion, da, carrier, archivos, motus_data, leer_pdf,
                             revisar_archivos=False, motus_error=motus_error)
    res.checks += base.checks
    res.motus, res.motus_error = base.motus, base.motus_error
    if asignacion is not None:
        tel = digitos((da or {}).get("telefono"))
        res.checks.append(Check(GRUPO_DA, "Driver phone on the Driver Assignment", "ok" if len(tel) >= 10 else "fail",
                                (da or {}).get("telefono") or "Blank.",
                                "" if len(tel) >= 10 else "Add the driver's phone. It is required (the dispatcher's phone is optional).",
                                _link_da(asignacion["da_id"]), f"Open {asignacion['da_nombre']}"))

    # 7. Documentos del carrier y app de STT
    m = base.motus
    if carrier is not None and asignacion is not None:
        link_car = _link_carrier(carrier["_id"])
        estado_app, fila = driver_firmo_en_app(conductores, asignacion, da)
        app_driver = bool((da or {}).get("send_to_app"))
        if app_driver:
            res.caso = "STT app driver"
        if s.get("internacional"):
            res.caso = "International" if res.caso == "Standard" else res.caso + ", international"
        if s.get("dispatchers_externos"):
            res.caso += ", external dispatcher"

        # BCA firmada (o términos aceptados en la app)
        if estado_app == "si":
            res.terminos_app = fila
            add(Check(GRUPO_DOCS, "Signed BCA", "ok",
                      f"{fila['nombre']} accepted the terms in the STT app (Terms Status: True). It counts as a signed BCA."))
        elif m is not None:
            mc_activo = bool(digitos(carrier.get("mc"))) and bool(
                (motus_property(m, digitos(carrier["mc"])) or {}).get("estado") == "Active")
            if archivos is None:
                add(Check(GRUPO_DOCS, "Signed BCA", "warn", "The carrier's files could not be read.",
                          "You can submit the request. BackOffice will check the BCA.", link_car, "View carrier files"))
            else:
                chk, previa = revisar_bca_previa(archivos, leer_pdf, carrier, m, mc_activo)
                if previa is not None:
                    add(Check(GRUPO_DOCS, "Signed BCA", "ok", chk.detalle, "", chk.link, chk.link_texto))
                elif chk.estado == "warn":
                    add(Check(GRUPO_DOCS, "Signed BCA", "warn", chk.detalle, chk.solucion, chk.link, chk.link_texto))
                else:
                    add(Check(GRUPO_DOCS, "Signed BCA", "fail", chk.detalle,
                              "The carrier needs a signed BCA with current MOTUS information before the LC. "
                              "Request the BCA first (use the Verify BCA tab).", chk.link or link_car,
                              chk.link_texto or "Open carrier"))

        # COI
        if m is not None:
            add(revisar_coi(archivos, leer_pdf, m, s, link_car))

        # Licencia del driver
        if archivos is not None:
            lic = [f for f in archivos if "LICENSE" in f["tipo"].upper()
                   or re.search(r"\bDL\b|LICEN|CONSTANCIA|ALTERNATIVE", f["archivo"].upper())]
            if lic:
                add(Check(GRUPO_DOCS, "Driver's license", "ok", ", ".join(f["archivo"] for f in lic)))
            elif app_driver:
                add(Check(GRUPO_DOCS, "Driver's license", "info",
                          "No license in the carrier files. For STT app drivers it is not always on file."))
            else:
                add(Check(GRUPO_DOCS, "Driver's license", "fail", "No driver's license found in the carrier's files.",
                          "Upload a legible photo of the driver's license. If the driver will not share it, upload "
                          "the alternative (driver name and phone, truck and trailer number, plate number, and a truck "
                          "photo showing the DOT#) as a file named 'Driver License Alternative'.",
                          link_car, "Open carrier"))

        # Caso 1: driver de la app
        if app_driver:
            if fila is None:
                add(Check(GRUPO_APP, "Driver listed in Driver Information", "fail",
                          "Send To App is checked, but the driver is not in the carrier's Driver Information.",
                          "Set up the driver in the STT app so it appears in Driver Information.", link_car, "Open carrier"))
            else:
                for etiqueta, valor in [("DOT Status", fila.get("dot_status")), ("Stripe Status", fila.get("stripe_status")),
                                        ("Terms Status", fila.get("terms"))]:
                    ok = _es_positivo(valor)
                    add(Check(GRUPO_APP, etiqueta, "ok" if ok else "fail", f"{etiqueta}: {valor or 'blank'}.",
                              "" if ok else f"{etiqueta} must be positive to work with an app driver.",
                              link_car, "View Driver Information"))
                ok = (fila.get("estado") or "").strip().lower() == "approved"
                add(Check(GRUPO_APP, "Driver Status is Approved", "ok" if ok else "fail",
                          f"Driver Status: {fila.get('estado') or 'blank'}.",
                          "" if ok else "Only drivers with Driver Status: Approved can work through the STT app.",
                          link_car, "View Driver Information"))

    # 8. Lo que BackOffice revisa a mano (todavía no automatizado)
    manual = [
        ("Order status and signed Shipper Agreement",
         "Quote or Order: no LC yet. Awaiting Customer Signature: needs an approved override in Requests. "
         "Work in Progress: the signed SA must show the shipment's addresses, load, and full amount."),
        ("Amounts match the order", "The shipment amounts must match the order's payment details."),
        ("Addresses match the order and signed SA",
         "Pick-up and delivery must match the order and SA, or be listed in Special Terms."
         + (" International load: the delivery can differ (land leg only)." if s.get("internacional") else "")),
        ("Load is not repeated in another shipment", "Allowed only for Naviera or TONU shipments of the same order."),
        ("COI looks original and is signed", "Suspicious format or a pasted signature needs a supervisor override."),
        ("Driver's license photo is legible", "Name and photo must be readable."),
    ]
    if s.get("dispatchers_externos"):
        manual.append(("External dispatcher", "The carrier must appear in Carrier Management, or the driver's "
                                              "profile must be added in the shipment's Chatter."))
    for titulo, detalle in manual:
        add(Check(GRUPO_MANUAL, titulo, "info", detalle))
    return res


def pre_verificar_lc(crm: CRM, shipment_id: int, motus_fn=motus_consultar) -> list[ResultadoLC]:
    """Recorre el CRM y MOTUS para un Shipment y aplica las reglas de Load Confirmation."""
    shipment = crm.shipment(shipment_id)
    if shipment is None:
        return [evaluar_lc(shipment_id, None, None, None, None, None, None, None)]
    try:
        loads = []
        for l in crm.loads_shipment(shipment_id, shipment["_token"])[:20]:
            detalle = crm.load(l["id"])
            detalle["_id"] = l["id"]
            detalle["numero"] = detalle["numero"] or l["numero"]
            loads.append(detalle)
    except Exception:
        loads = None
    if not shipment["asignaciones"]:
        return [evaluar_lc(shipment_id, shipment, None, None, None, None, None, None, None, loads)]

    resultados = []
    for asig in shipment["asignaciones"]:
        da = crm.driver_assignment(asig["da_id"])
        carrier = archivos = motus_data = conductores = motus_error = None
        if asig["carrier_id"]:
            carrier = crm.carrier(asig["carrier_id"])
            carrier["_id"] = asig["carrier_id"]
            try:
                archivos = crm.archivos_carrier(asig["carrier_id"], carrier["_token"])
            except Exception:
                archivos = None
            try:
                conductores = crm.conductores_carrier(asig["carrier_id"], carrier["_token"])
            except Exception:
                conductores = None
            dot = digitos(carrier["dot"])
            try:
                motus_data = motus_fn(dot) if dot else None
            except MotusNoDisponible as ex:
                motus_error = str(ex) or "no response"

        def leer_pdf(guid):
            try:
                return texto_pdf(crm.descargar(guid))
            except Exception:
                return None

        resultados.append(evaluar_lc(shipment_id, shipment, asig, da, carrier, archivos, motus_data,
                                     leer_pdf, conductores, loads, motus_error))
    return resultados


# ============================================================
# Registro de verificaciones (Supabase)
# ============================================================
def nuevo_codigo(shipment_id: int, documento: str = "BCA") -> str:
    alfabeto = "ABCDEFGHJKMNPQRSTUVWXYZ23456789"
    return f"{documento}-{shipment_id}-" + "".join(secrets.choice(alfabeto) for _ in range(4))


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

    def recientes(self, dias: int = 30, documento: str = "BCA") -> list[dict]:
        desde = (datetime.now(timezone.utc) - timedelta(days=dias)).isoformat()
        r = requests.get(f"{self.url}/rest/v1/prechecks",
                         params={"created_at": f"gte.{desde}", "documento": f"eq.{documento}",
                                 "select": "created_at,resultado,motivos", "limit": "10000"},
                         headers=self._h(), timeout=20)
        r.raise_for_status()
        return r.json()
