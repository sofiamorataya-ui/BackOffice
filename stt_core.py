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
import unicodedata
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
    """Mayúsculas, sin tildes ni puntuación y con espacios simples (Quiñonez = QUINONEZ).
    No cambia palabras (LANE sigue siendo LANE)."""
    sin_tildes = unicodedata.normalize("NFKD", texto or "").encode("ascii", "ignore").decode()
    return " ".join(re.sub(r"[^A-Z0-9]+", " ", sin_tildes.upper()).split())


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
    zip_correo = ((correo or {}).get("zipCode") or "")[:5]
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
        "zip_correo": zip_correo,
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


def _items_tarjeta(item) -> dict:
    """Pares 'Etiqueta : valor' de un recuadro de una tarjeta del CRM (Requests, Shipments, Contact Roles…)."""
    campos = {}
    for li in item.find_all("li"):
        lab = li.find("label")
        if lab:
            campos[lab.get_text(strip=True).rstrip(" :").strip()] = \
                li.get_text(" ", strip=True)[len(lab.get_text(" ", strip=True)):].strip()
    return campos


def _requests(soup: BeautifulSoup, id_tarjeta: str = "order-Request") -> list[dict]:
    """Bloque Requests (del Shipment: order-Request; de la Orden: quote-request)."""
    salida = []
    bloque = soup.find(id=id_tarjeta)
    for item in (bloque.select(".request-info-itembox") if bloque else []):
        a = item.find("a", href=re.compile(r"/Admin/Request/Details/\d+"))
        campos = _items_tarjeta(item)
        salida.append({"id": a.get_text(strip=True) if a else "",
                       "num": int(re.search(r"/Details/(\d+)", a["href"]).group(1)) if a else 0,
                       "tipo": campos.get("Request Type", ""),
                       "documento": campos.get("Document", ""), "respuesta": campos.get("Response", "")})
    return salida


def _requests_shipment(soup: BeautifulSoup) -> list[dict]:
    return _requests(soup, "order-Request")


def parse_sign_docs(soup: BeautifulSoup) -> list[dict]:
    """Tarjeta Sign Documents (Orden o Shipment). El primero de arriba es el más reciente."""
    salida = []
    bloque = soup.find(id="quote-sign-document")
    for fila in (bloque.select("tr.request-info-itembox-tr") if bloque else []):
        nombre = ""
        for td in fila.find_all("td"):
            if td.get_text(strip=True) == "File Name :":
                sig = td.find_next_sibling("td")
                nombre = sig.get_text(strip=True) if sig else ""
                break
        a = fila.find("a", href=re.compile(r"downloadGuid="))
        guid = re.search(r"downloadGuid=([\w\-]+)", a["href"]).group(1) if a else ""
        if nombre or guid:
            salida.append({"archivo": nombre, "guid": guid})
    return salida


def _sin_notificaciones(soup: BeautifulSoup) -> BeautifulSoup:
    """Quita la lista de notificaciones del encabezado, que trae enlaces a otros Shipments."""
    for n in soup.select("#notificationList, #notificationStackWrapper, .notification-item"):
        n.decompose()
    return soup


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
        tarjeta = _items_tarjeta(item)
        asignaciones.append({
            "da_id": int(re.search(r"/Details/(\d+)", a_da["href"]).group(1)),
            "da_nombre": a_da.get_text(strip=True),
            "carrier_id": int(re.search(r"/Details/(\d+)", a_car["href"]).group(1)) if a_car else None,
            "carrier_nombre": a_car.get_text(strip=True) if a_car else tarjeta.get("Company Name", ""),
            "driver": a_drv.get_text(" ", strip=True) if a_drv else "",
            "driver_id": int(re.search(r"/DriverDetails/(\d+)", a_drv["href"]).group(1)) if a_drv else None,
            "status": tarjeta.get("Status", ""),
        })

    return {
        "numero": texto.get("Shipment #", ""),
        "orden": texto.get("Order", ""),
        "owner": por_for.get("OrderOwner", "") or texto.get("Shipment Owner", ""),
        "dispatcher": por_for.get("DispatcherId", "") or texto.get("Dispatcher Id", ""),
        "status": texto.get("Status", ""),
        "origen_ciudad": por_for.get("BillingAddress_City", ""),
        "origen_estado": por_for.get("BillingAddress_State", ""),
        "origen_zip": por_for.get("BillingAddress_ZipPostalCode", ""),
        "origen_pais": por_for.get("BillingAddress_Country", ""),
        "destino_ciudad": por_for.get("ShippingAddress_City", ""),
        "destino_estado": por_for.get("ShippingAddress_State", ""),
        "destino_zip": por_for.get("ShippingAddress_ZipPostalCode", ""),
        "destino_pais": por_for.get("ShippingAddress_Country", ""),
        "asignaciones": asignaciones,
        # --- datos para Load Confirmation ---
        "fecha_pu": por_for.get("EstimatedPickUpDate", ""),
        "fecha_entrega": por_for.get("EstimatedDeliveryDate", ""),
        "internacional": "✔" in por_for.get("International", ""),
        "supervisor": por_for.get("Supervisor", ""),
        "pago_tipo": por_for.get("PaymentType", ""),
        "carrier_pay": por_for.get("CarrierPay", ""),
        "broker_pays_carrier": por_for.get("BrokerPaysCarrier", ""),
        "carrier_pays_broker": por_for.get("CarrierPaysBroker", ""),
        "truck_type": por_for.get("TruckType", ""),
        "special_instructions": _texto_campo(soup, "SpecialInstruction"),
        "orden_id": _id_enlace(soup, r"/Admin/Orders/Details/(\d+)"),
        "requests": _requests_shipment(soup),
        "sign_docs": parse_sign_docs(soup),
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
        "dispatch_nombre": texto.get("Dispatch Name", ""),
        "dispatch_telefono": texto.get("Dispatch Phone", ""),
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
        "hitch": f.get("Hitch", ""), "lot": f.get("LotNumber", ""), "notas": f.get("Notes", ""),
    }


def parse_order(soup: BeautifulSoup) -> dict | None:
    """Página de la Orden (/Admin/Orders/Details/<id>)."""
    titulo = soup.title.get_text(strip=True) if soup.title else ""
    if "Order Detail" not in titulo and not soup.find(id="load-steps"):
        return None
    soup = _sin_notificaciones(soup)
    texto, f = leer_campos(soup)
    actual = soup.select_one("#load-steps li.current span")
    contactos = []
    bloque = soup.find(id="quote-contact-Role")
    for item in (bloque.select(".request-info-itembox") if bloque else []):
        a = item.find("a", href=re.compile(r"/Admin/Customer/Details/\d+"))
        contactos.append({"nombre": a.get_text(" ", strip=True) if a else "",
                          "rol": _items_tarjeta(item).get("Role", "")})
    tarjeta_ship = []
    bloque = soup.find(id="quote-order")
    for item in (bloque.select(".request-info-itembox") if bloque else []):
        a = item.find("a", href=re.compile(r"/Admin/Shipments/Details/\d+"))
        if a:
            campos = _items_tarjeta(item)
            tarjeta_ship.append({"id": int(re.search(r"/Details/(\d+)", a["href"]).group(1)),
                                 "numero": a.get_text(strip=True), "status": campos.get("Status", ""),
                                 "carrier_pay": campos.get("Carrier Pay", "")})
    ver_todos = soup.find("a", href=re.compile(r"/Admin/Order/ViewAllOrder\?orderId=\d+"))
    id_input = soup.find("input", {"id": "Id"})
    return {
        "numero": f.get("CustomOpportunityId", ""),
        "id": int(id_input["value"]) if id_input and (id_input.get("value") or "").isdigit() else None,
        "nombre": f.get("OpportunityName", ""),
        "etapa": actual.get_text(strip=True).upper() if actual else "",
        "estado_campo": f.get("QuoteStatusId", "").upper(),
        "cliente": f.get("CustomerId", ""),
        "contactos": contactos,
        "agreed_payments": f.get("AgreedPayments", ""),
        "customer_pays_otr": f.get("CustomerPaysOTR", ""),
        "otr_pays_carrier": f.get("OTRPaysCarrier", ""),
        "carrier_pays_broker": f.get("CarrierPaysBroker", ""),
        "pickup": f.get("CustomerPaysCarrierOnPickUp", ""),
        "dropoff": f.get("CustomerPaysCarrierOnDropOff", ""),
        "carrier_pay": f.get("CarrierPay", ""),
        "origen": {"direccion": f.get("OriginAddress1", ""), "ciudad": f.get("OriginCity", ""),
                   "estado": f.get("OriginState", ""), "zip": f.get("OriginZip", ""), "pais": f.get("OriginCountry", "")},
        "destino": {"direccion": f.get("DestinationAddress1", ""), "ciudad": f.get("DestinationCity", ""),
                    "estado": f.get("DestinationState", ""), "zip": f.get("DestinationZip", ""),
                    "pais": f.get("DestinationCountry", "")},
        "special_terms": _texto_campo(soup, "SpecialTerms"),
        "sign_docs": parse_sign_docs(soup),
        "requests": _requests(soup, "quote-request"),
        "shipments_tarjeta": tarjeta_ship,
        "ver_todos": ver_todos["href"] if ver_todos else "",
        "_token": (soup.find("input", {"name": "__RequestVerificationToken"}) or {}).get("value", ""),
    }


def parse_ids_shipments(soup: BeautifulSoup) -> list[int]:
    """Ids de Shipment de la página View All de la Orden, en orden y sin repetir."""
    soup = _sin_notificaciones(soup)
    raiz = soup.find("main") or soup
    ids = [int(x) for x in re.findall(r"/Admin/Shipments/Details/(\d+)", str(raiz))]
    return list(dict.fromkeys(ids))


def parse_request_detail(soup: BeautifulSoup) -> dict:
    """Página de un Request (/Admin/Request/Details/<id>): tipo, comentario, respuesta y quién lo modificó."""
    texto, _ = leer_campos(soup)
    modifico = texto.get("Last Modified By", "")
    return {
        "id": texto.get("Request ID", ""),
        "tipo": texto.get("Request Type", ""),
        "documento": texto.get("Document", ""),
        "comentario": texto.get("Comment", ""),
        "respuesta": texto.get("Response", ""),
        "razon": texto.get("Reason", ""),
        "modifico": re.split(r",?\s*\d{1,2}/\d{1,2}/\d{4}", modifico)[0].strip(" ,"),
        "modifico_completo": modifico,
        "creo": re.split(r",?\s*\d{1,2}/\d{1,2}/\d{4}", texto.get("Created By", ""))[0].strip(" ,"),
        "leido": bool(texto),
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

    def orden(self, orden_id: int) -> dict | None:
        return parse_order(self._pagina(f"/Admin/Orders/Details/{orden_id}"))

    def documentos_orden(self, orden_id: int, token: str) -> list[dict]:
        """Tarjeta Documents de la Orden (POST /Admin/LogisticsQuote/OrderDocumentList)."""
        r = self.s.post(
            f"{CRM_URL}/Admin/LogisticsQuote/OrderDocumentList?LogisticQuoteId={orden_id}",
            data={"draw": "1", "start": "0", "length": "200", "__RequestVerificationToken": token},
            headers={"X-Requested-With": "XMLHttpRequest", "Referer": f"{CRM_URL}/Admin/Orders/Details/{orden_id}"},
            timeout=30,
        )
        r.raise_for_status()
        filas = r.json().get("Data") or []
        return [{"tipo": x.get("TypeName") or "", "archivo": x.get("FileName") or "", "creado": x.get("CreatedOn") or "",
                 "creado_por": x.get("CreatedBy") or "", "guid": x.get("DownloadGuid") or ""} for x in filas]

    def ids_shipments_orden(self, url_ver_todos: str) -> list[int]:
        ruta = url_ver_todos.replace(CRM_URL, "") if url_ver_todos.startswith("http") else url_ver_todos
        return parse_ids_shipments(self._pagina(ruta))

    def request(self, num: int) -> dict:
        return parse_request_detail(self._pagina(f"/Admin/Request/Details/{num}"))


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

# Estados de cada punto revisado (regla de Sofía, pregunta 78, bitácora B-027):
#   ok         cumple.
#   fail       rojo: el broker debe corregirlo. Frena la solicitud.
#   warn       amarillo: falta algo que el broker debe conseguir (por ejemplo, un Override). También frena.
#   exception  el dueño de la franquicia pide la solicitud: no necesita Override, pero debe escribir la
#              excepción en el comentario del request de LC. No frena.
#   manual     caso que la app no puede leer o que no está mapeado: pasa a BackOffice con código y
#              BackOffice lo revisa a mano. Solo BackOffice ve estos puntos (pregunta 79).
#   info       solo informativo.
EVALUADOS = ("ok", "fail", "warn", "exception")
FRENAN = ("fail", "warn")


def nivel_cumplimiento(checks: list, requisitos_base: int) -> tuple[str, int, int]:
    """Devuelve (nivel, cumplidos, total) con nivel 'green' | 'yellow' | 'red'.

    Los requisitos que no se llegaron a evaluar (porque faltaba algo antes) cuentan como no cumplidos,
    por eso el total nunca es menor que `requisitos_base`.
    """
    evaluados = [c for c in checks if c.estado in EVALUADOS]
    cumplidos = sum(c.estado not in FRENAN for c in evaluados)
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
        """Todo lo que frena la solicitud (rojo y amarillo), en el orden del checklist."""
        return [c for c in self.checks if c.estado in FRENAN]

    @property
    def manuales(self) -> list[Check]:
        """Lo que pasa a BackOffice para revisión manual. Solo lo ve BackOffice."""
        return [c for c in self.checks if c.estado == "manual"]

    @property
    def excepciones(self) -> list[Check]:
        return [c for c in self.checks if c.estado == "exception"]

    @property
    def veredicto(self) -> str:
        if any(c.grupo == GRUPO_BCA and c.estado == "fail" for c in self.checks):
            return "YA_EXISTE"
        if self.motus_error:
            return "SIN_VERIFICAR"
        return "NO_ENVIAR" if self.fallas else "AUTORIZADO"

    @property
    def requisitos(self) -> tuple[int, int]:
        evaluados = [c for c in self.checks if c.estado in EVALUADOS]
        return sum(c.estado not in FRENAN for c in evaluados), len(evaluados)

    @property
    def cumplimiento(self) -> tuple[str, int, int]:
        """Nivel de cumplimiento del solicitante. La BCA ya existente no es un error del broker,
        así que no cuenta en contra. Solo es verde lo que puede recibir código."""
        checks = [c for c in self.checks if not (c.grupo == GRUPO_BCA and c.estado == "fail")]
        base = REQUISITOS_BCA - (1 if self.veredicto == "YA_EXISTE" else 0)
        nivel, cumplidos, total = nivel_cumplimiento(checks, base)
        if self.veredicto in ("AUTORIZADO", "YA_EXISTE") and not any(c.estado in FRENAN for c in checks):
            return "green", cumplidos, cumplidos
        return ("yellow" if nivel == "green" else nivel), cumplidos, total


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
        return Check(GRUPO_BCA, "Previous BCA could not be read", "manual",
                     f"The content of {nombres} could not be read automatically. It may be a scanned image.",
                     "BackOffice checks whether that BCA is current (legal name, MC or DOT, address, signature).",
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
            add(Check(GRUPO_MOTUS, "MC holds Motor Carrier of Property authority", "info",
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
        add(Check(GRUPO_BCA, "Carrier files", "manual",
                  "The carrier's files section could not be read.",
                  "BackOffice checks whether a current BCA is already on file.",
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
# Load Confirmation (LC), versión 2
# Procedimiento de BackOffice + respuestas de Sofía Morataya del 9 de octubre de 2026
# (bitácora B-027 a B-034). Los textos visibles están en inglés de EE. UU.
# ============================================================
GRUPO_ORDEN = "Order and Shipper Agreement"
GRUPO_INFO = "Shipment info"
GRUPO_LOADS = "Loads"
GRUPO_PAGO = "Payment"
GRUPO_MONTOS = "Amounts match the order"
GRUPO_DIRS = "Addresses"
GRUPO_TRUCK = "Truck"
GRUPO_SI = "Special Instructions"
GRUPO_DOCS = "Carrier documents"
GRUPO_APP = "STT app driver"
GRUPO_MANUAL = "BackOffice reviews manually"
ORDEN_GRUPOS_LC = [GRUPO_ORDEN, GRUPO_INFO, GRUPO_LOADS, GRUPO_PAGO, GRUPO_MONTOS, GRUPO_DIRS, GRUPO_TRUCK,
                   GRUPO_SI, GRUPO_DA, GRUPO_CARRIER, GRUPO_MOTUS, GRUPO_RUTA, GRUPO_DOCS, GRUPO_APP, GRUPO_MANUAL]
REQUISITOS_LC = 30   # mínimo de puntos de una LC estándar completa (ver B-027)

# Etapas de la Orden (barra de arriba de la Orden, respuestas 1 a 3)
ETAPAS_SIN_SA = ("QUOTE", "ORDER")
ETAPA_FIRMA = "AWAITING CUSTOMER SIG"
ETAPAS_FIRMADA = ("WORK IN PROGRESS", "DELIVERED", "COMMISSION PAID", "COMPLETED")
ETAPA_PERDIDA = "CLOSED LOST"

# Driver de la app: únicos valores positivos (respuesta 75)
VALORES_APP = [("DOT Status", "dot_status", "approved"), ("Stripe Status", "stripe_status", "accepted"),
               ("Terms Status", "terms", "true"), ("Driver Status", "estado", "approved")]

HITCH_VALIDOS = {"BALL HITCH": "Ball hitch", "PINTLE HITCH": "Pintle hitch", "PINTEL HITCH": "Pintle hitch",
                 "5TH WHEEL HITCH": "5th Wheel hitch", "FIFTH WHEEL HITCH": "5th Wheel hitch"}
MOTORIZADOS = (r"vehicle|\bcar\b|\bcars\b|truck|\bsuv\b|motorcycle|\bboat|\brv\b|tractor|equipment|machinery|"
               r"forklift|excavator|loader|\bbus\b|\batv\b|\butv\b|golf cart|jet ?ski|trailer")
# Vehicle + carro, pickup o moto: no necesita dimensiones (respuesta 42)
VEHICULOS_LIVIANOS = (
    r"\bcar\b|\bsedan\b|\bcoupe\b|\bsuv\b|pick ?-?up|\bmotorcycle|\bmotorbike|\bmoto\b|\bscooter\b|"
    r"\btoyota\b|\bhonda\b|\bford\b|\bchevrolet\b|\bchevy\b|\bnissan\b|\bhyundai\b|\bkia\b|\bmazda\b|\bsubaru\b|"
    r"\bvolkswagen\b|\bvw\b|\bbmw\b|\bmercedes\b|\baudi\b|\blexus\b|\bacura\b|\binfiniti\b|\bjeep\b|\bdodge\b|"
    r"\bram\b|\bgmc\b|\bcadillac\b|\bbuick\b|\blincoln\b|\bchrysler\b|\btesla\b|\bvolvo xc|\bporsche\b|"
    r"\bjaguar\b|\bland rover\b|\brange rover\b|\bmini cooper\b|\bmitsubishi\b|\bgenesis\b|\bfiat\b|\balfa romeo\b|"
    r"\bmaserati\b|\bferrari\b|\blamborghini\b|\bbentley\b|\brolls\b|\bharley\b|\byamaha\b|\bkawasaki\b|"
    r"\bsuzuki\b|\bducati\b|\btriumph\b|\bindian motorcycle|\bcamry\b|\bcorolla\b|\bcivic\b|\baccord\b|"
    r"\bf-?150\b|\bsilverado\b|\btacoma\b|\btundra\b|\bmustang\b|\bcamaro\b|\bwrangler\b")
PALABRAS_EMBALAJE = {"PALLETIZED", "PALLETISED", "PALLET", "PALLETS", "CRATED", "CRATE", "CRATES", "GOODS", "ON",
                     "IN", "OF", "AND", "THE", "A", "BOXED", "BOX", "BOXES", "LOAD", "CARGO", "FREIGHT", "ITEMS", "MISC"}


def _num(texto) -> float:
    try:
        return float(re.sub(r"[^\d.\-]", "", str(texto or "")) or 0)
    except ValueError:
        return 0.0


def _dinero(v: float) -> str:
    return f"${v:,.2f}"


def _fecha(texto, referencia: date | None = None) -> date | None:
    """Fecha MM/DD/YY, MM/DD/YYYY o MM/DD (respuesta 48). Sin año, se usa el año que deja la fecha
    más cerca de la referencia (la fecha estimada del Shipment)."""
    m = re.search(r"(\d{1,2})/(\d{1,2})(?:/(\d{2,4}))?", texto or "")
    if not m:
        return None
    mes, dia = int(m.group(1)), int(m.group(2))
    try:
        if m.group(3):
            anio = int(m.group(3))
            anio += 2000 if anio < 100 else 0
            return date(anio, mes, dia)
        if referencia is None:
            return None
        opciones = [date(referencia.year + d, mes, dia) for d in (-1, 0, 1)]
        return min(opciones, key=lambda x: abs((x - referencia).days))
    except ValueError:
        return None


def _fecha_cerca(texto: str, claves: list[str], referencia: date | None = None) -> date | None:
    """Primera fecha que aparece justo después de alguna de las palabras clave."""
    for clave in claves:
        for m in re.finditer(clave, texto, re.I):
            f = _fecha(texto[m.end(): m.end() + 60], referencia)
            if f:
                return f
    return None


def _fmt(d: date | None) -> str:
    return f"{d.month}/{d.day}/{d.year}" if d else "not found"


def _telefonos(texto: str) -> list[str]:
    return [digitos(t)[-10:] for t in re.findall(r"\(?\d{3}\)?[\s.\-]?\d{3}[\s.\-]?\d{4}", texto or "")]


def _monto_en_texto(valor: float, texto: str) -> bool:
    """True si el monto aparece en el texto: 27,200.00 · 27200.00 · 27,200 · 27200."""
    if valor <= 0:
        return False
    entero = f"{int(valor):,}".replace(",", ",?")
    centavos = f"{valor:.2f}".split(".")[1]
    patron = rf"(?<![\d,.]){entero}" + (r"(?:\.00)?" if centavos == "00" else rf"\.{centavos}") + r"(?![\d]|[.,]\d)"
    return bool(re.search(patron, texto or ""))


def _numero_regex(valor: float) -> str:
    """Patrón de un número con decimales flexibles: 18.30 acepta 18.3 y 18.30; 8.00 acepta 8 y 8.0."""
    entero, dec = f"{valor:.2f}".split(".")
    dec = dec.rstrip("0")
    entero = f"{int(entero):,}".replace(",", ",?")
    cola = rf"\.{dec}0*" if dec else r"(?:\.0+)?"
    return rf"(?<![\d.,]){entero}{cola}(?![\d])"


# ---------- Nombres y anexo editable ----------
SUFIJOS = {"JR", "SR", "II", "III", "IV"}


def _tokens_nombre(nombre: str) -> set[str]:
    return {t for t in norm(nombre).split() if t not in SUFIJOS}


def mismo_nombre(a: str, b: str) -> bool:
    """Misma persona: todas las palabras del nombre más corto están en el otro (Luis Lopez Jr = Luis Lopez).
    Hacen falta al menos dos palabras, para que un nombre suelto no coincida con cualquiera."""
    ta, tb = _tokens_nombre(a), _tokens_nombre(b)
    corto = ta if len(ta) <= len(tb) else tb
    return len(corto) >= 2 and (ta <= tb or tb <= ta)


def nombre_en_lista(nombre: str, lista) -> bool:
    return any(mismo_nombre(nombre, x) for x in lista or [])


@dataclass
class Anexo:
    """Lista editable de BackOffice (anexo.json, respuesta 82). La mantiene Sofía."""
    franquicias: list = field(default_factory=list)
    naviera: list = field(default_factory=list)
    camiones: list = field(default_factory=list)
    textos_cobertura: dict = field(default_factory=dict)
    error: str = ""

    @classmethod
    def desde_dict(cls, d: dict | None) -> "Anexo":
        d = d or {}
        return cls(d.get("franquicias") or [], d.get("transportistas_naviera") or [],
                   d.get("coberturas_por_camion") or [], d.get("textos_de_cobertura") or {})

    def franquicias_de(self, campo_supervisor: str) -> list[dict]:
        personas = [p for p in re.split(r"[,;/]", campo_supervisor or "") if p.strip()]
        return [f for f in self.franquicias
                if any(nombre_en_lista(p, (f.get("supervisores") or []) + (f.get("duenos") or [])) for p in personas)]

    def aprobadores(self, campo_supervisor: str) -> list[str] | None:
        fr = self.franquicias_de(campo_supervisor)
        if not fr:
            return None
        return list(dict.fromkeys(n for f in fr for n in f.get("aprueban_overrides") or []))

    def es_dueno(self, nombre: str) -> bool:
        return bool(nombre) and any(nombre_en_lista(nombre, f.get("duenos") or []) for f in self.franquicias)

    def contactos(self, campo_supervisor: str) -> list[dict]:
        return [c for f in self.franquicias_de(campo_supervisor) for c in f.get("contactos_autorizados") or []]

    def es_naviera(self, carrier_nombre: str) -> bool:
        return any(norm(n) and norm(n) in norm(carrier_nombre) for n in self.naviera)

    def coberturas(self, truck_type: str) -> tuple[str, list[str]] | None:
        """Cobertura del COI que exige el tipo de camión (tabla del procedimiento, respuesta 57)."""
        t = f" {norm(truck_type)} "
        mejor = None
        for fila in self.camiones:
            for alias in fila.get("alias") or []:
                a = norm(alias)
                if a and f" {a} " in t and (mejor is None or len(a) > mejor[0]):
                    mejor = (len(a), fila)
        return (mejor[1]["camion"], mejor[1]["coberturas"]) if mejor else None

    def palabras_cobertura(self, cobertura: str) -> list[str]:
        return self.textos_cobertura.get(cobertura) or [cobertura]


def cargar_anexo(ruta) -> Anexo:
    import json
    try:
        with open(ruta, encoding="utf-8") as f:
            return Anexo.desde_dict(json.load(f))
    except OSError:
        return Anexo(error="anexo.json was not found")
    except ValueError as ex:
        return Anexo(error=f"anexo.json has a formatting error ({ex})")


# ---------- Overrides ----------
def propositos_override(comentario: str) -> set[str]:
    """Para qué es un Override según su comentario (respuesta 14). Puede servir para varias cosas."""
    c = (comentario or "").lower()
    p = set()
    coi = bool(re.search(r"\bcoi\b|insurance|certificate", c))
    if coi:
        p.add("coi")
    if re.search(r"\bsa\b|shipper|agreement|customer will sign|client will sign|will sign (it )?later|"
                 r"without (the )?signature|sign(ed)? later", c):
        p.add("sa")
    if re.search(r"\bcod\b|collect on deliver|\bcop\b|collect on pick ?-?up|"
                 r"pay(s|ing)? (directly )?(to )?the (carrier|driver)|pay the (carrier|driver) directly|"
                 r"cash on deliver", c):
        p.add("pago")
    if re.search(r"address|location|zip code|drop ?-?off|delivery place|pick ?-?up place", c) \
            and not (coi and "motus" in c):
        p.add("direccion")
    return p


def override_aprobado(o: dict) -> bool:
    """Aprobado = Response Done (respuestas 12 y 13). Si Reason dice que se rechazó, no cuenta."""
    return (o.get("respuesta") or "").strip().lower() == "done" and \
        not re.search(r"reject|denied|declin|not approved", o.get("razon") or "", re.I)


@dataclass
class ContextoOverride:
    overrides: list            # Requests del Shipment con tipo Override, con su detalle
    aprobadores: list | None   # quién puede aprobar en esta franquicia (None: franquicia no identificada)
    dueno_pide: bool           # el que pide es dueño de franquicia (respuesta 17)
    supervisor: str
    link_ship: str


TEXTOS_OVERRIDE = {
    "sa": ("Override for the unsigned Shipper Agreement",
           "Pls approve to send this LC without SA signed, the customer will sign later."),
    "pago": ("Override for the payment type",
             "Approve this LC, the customer will pay directly to the carrier."),
    "coi": ("Override for the COI", "Pls approve to use the COI added …"),
    "direccion": ("Override for the address change",
                  "Pls approve the address change; the customer's authorization is in the order's Documents."),
}


def revisar_override(proposito: str, grupo: str, motivo: str, ctx: ContextoOverride) -> Check:
    titulo, ejemplo = TEXTOS_OVERRIDE[proposito]
    quienes = " / ".join(ctx.aprobadores) if ctx.aprobadores else "the supervisor of the shipment"
    candidatos = [o for o in ctx.overrides if proposito in o["propositos"]]
    aprobados = [o for o in candidatos if override_aprobado(o)]
    if aprobados:
        if ctx.aprobadores is None:
            o = aprobados[0]
            return Check(grupo, titulo, "manual",
                         f"{o['id']} is Done, last modified by {o['modifico'] or 'unknown'}. The franchise could not be "
                         f"identified from the shipment's Supervisor field ({ctx.supervisor or 'blank'}).",
                         "BackOffice confirms that person can approve overrides for this shipment.",
                         ctx.link_ship, "Open shipment")
        validos = [o for o in aprobados if nombre_en_lista(o["modifico"], ctx.aprobadores)]
        if validos:
            o = validos[0]
            return Check(grupo, titulo, "ok", f"{o['id']}: Done, approved by {o['modifico']}. {motivo}")
        o = aprobados[0]
        return Check(grupo, titulo, "fail",
                     f"{o['id']} shows Response: Done, but it was last modified by {o['modifico'] or 'someone not listed'}, "
                     f"who does not approve overrides for this franchise. {motivo}",
                     f"Ask {quienes} to approve {o['id']}. BackOffice confirms the approval with Last Modified By, "
                     "so the request must not be edited after it is approved.", ctx.link_ship, "Open shipment")
    if ctx.dueno_pide:
        return Check(grupo, f"Exception: {titulo[len('Override for '):]}", "exception",
                     f"{motivo} You are the franchise owner, so no Override request is needed.",
                     f"Write this exception in the comment of your LC request: {motivo}")
    pendientes = [o for o in candidatos if not override_aprobado(o)]
    sin_clasificar = [o for o in ctx.overrides if override_aprobado(o) and o.get("leido") and not o["propositos"]]
    sin_leer = [o for o in ctx.overrides if not o.get("leido")]
    if sin_clasificar or sin_leer:
        o = (sin_clasificar or sin_leer)[0]
        detalle = (f"{o['id']} is Done, but its comment does not say what it approves: “{o.get('comentario', '')[:120]}”."
                   if o in sin_clasificar else f"The details of {o['id']} could not be read.")
        return Check(grupo, titulo, "manual", f"{detalle} {motivo}",
                     f"BackOffice confirms that {o['id']} covers this.", ctx.link_ship, "Open shipment")
    if pendientes:
        o = pendientes[0]
        return Check(grupo, titulo, "warn",
                     f"{o['id']} is still waiting for approval (Response: {o.get('respuesta') or 'blank'}). {motivo}",
                     f"Wait until {quienes} approves {o['id']}, then verify again.", ctx.link_ship, "Open shipment")
    return Check(grupo, titulo, "warn", f"There is no approved override in the shipment's Requests. {motivo}",
                 f"Create an Override request in the shipment's Requests and have {quienes} approve it. "
                 f"In the comment, explain why, for example: “{ejemplo}”", ctx.link_ship, "Open shipment")


# ---------- Resultado ----------
@dataclass
class ResultadoLC(ResultadoBCA):
    caso: str = "Standard"
    loads: list = field(default_factory=list)
    orden: dict | None = None
    error_lectura: str | None = None     # no se pudo leer la Orden: no se puede aprobar
    documento = "LC"

    @property
    def grupos(self) -> list[str]:
        return ORDEN_GRUPOS_LC

    @property
    def veredicto(self) -> str:
        if self.motus_error or self.error_lectura:
            return "SIN_VERIFICAR"
        return "NO_ENVIAR" if self.fallas else "AUTORIZADO"

    @property
    def cumplimiento(self) -> tuple[str, int, int]:
        nivel, cumplidos, total = nivel_cumplimiento(self.checks, REQUISITOS_LC)
        if self.veredicto == "AUTORIZADO":
            return "green", cumplidos, cumplidos
        return ("yellow" if nivel == "green" else nivel), cumplidos, total


def tipo_pago(texto: str) -> str | None:
    """Payment Type del Shipment (respuesta 30): broker, cop, cod, otr o joint."""
    p = (texto or "").lower()
    if "otr" in p:
        return "otr"
    if "joint" in p:
        return "joint"
    if "pick" in p or re.search(r"\bcop\b", p):
        return "cop"
    if "deliver" in p or re.search(r"\bcod\b", p):
        return "cod"
    if "broker" in p:
        return "broker"
    return None


NOMBRE_PAGO = {"broker": "Pay by Broker", "cop": "Collect on Pick Up (COP)", "cod": "Collect on Delivery (COD)",
               "otr": "OTR", "joint": "Joint"}


def es_tonu(s: dict) -> bool:
    """TONU: se identifica por las Special Instructions (respuesta 40)."""
    texto = " ".join([s.get("special_instructions") or ""] + [r.get("documento", "") for r in s.get("requests") or []])
    return bool(re.search(r"\bTONU\b", texto, re.I))


def es_cancelado(s: dict) -> bool:
    return "CANCEL" in (s.get("status") or "").upper()


# ---------- Loads ----------
def revisar_loads(loads: list[dict] | None, link_ship: str) -> list[Check]:
    if loads is None:
        return [Check(GRUPO_LOADS, "Loads", "manual", "The shipment's Loads section could not be read.",
                      "BackOffice reviews the loads.", link_ship, "Open shipment")]
    if not loads:
        return [Check(GRUPO_LOADS, "At least one load", "fail", "The shipment has no loads.",
                      "Add the load in the Loads section of the shipment.", link_ship, "Open shipment")]
    checks = [Check(GRUPO_LOADS, "At least one load", "ok", ", ".join(l["numero"] for l in loads))]
    for l in loads:
        n = l["numero"] or "Load"
        link = f"{CRM_URL}/Admin/Loads/Details/{l.get('_id')}" if l.get("_id") else link_ship
        tipo = (l["tipo"] or "").lower()
        desc = f"{l['make']} {l['model']}".strip()

        def add(titulo, ok, detalle, sol, estado_mal="fail"):
            checks.append(Check(GRUPO_LOADS, f"{n}: {titulo}", "ok" if ok else estado_mal, detalle,
                                "" if ok else sol, link, f"Open {n}"))

        add("load type and quantity", bool(l["tipo"]) and _num(l["cantidad"]) > 0,
            f"Load type: {l['tipo'] or 'blank'}. Quantity: {l['cantidad'] or 'blank'}.",
            f"Complete Load Type and Quantity on {n}.")
        add("make or model", bool(desc), desc or "Make and Model are blank.",
            f"Describe what is being moved in Make or Model on {n}.")
        if "pallet" in tipo or "crate" in tipo:
            concreto = [t for t in norm(desc).split() if t not in PALABRAS_EMBALAJE and not t.isdigit()]
            add("contents of the pallets or crates", bool(concreto), desc or "blank",
                f"The load type is {l['tipo']}. Make or Model must say exactly what is on the pallets or in the "
                f"crates. Words like “Palletized” or “Crated” alone are not enough.")
        if re.search(MOTORIZADOS, tipo):
            add("year", _num(l["year"]) > 1900, f"Year: {l['year'] or 'blank'}.",
                f"Add the year on {n}. It is required for loads with an engine.")
        vehiculo = "vehicle" in tipo
        liviano = vehiculo and bool(re.search(VEHICULOS_LIVIANOS, desc, re.I))
        dims = {"Length": l["length"], "Width": l["width"], "Height": l["height"], "Weight": l["weight"]}
        faltan = [k for k, v in dims.items() if _num(v) <= 0]
        medidas = (f"{_num(l['length']):g} ft × {_num(l['width']):g} ft × {_num(l['height']):g} ft, "
                   f"{_num(l['weight']):,.0f} lbs.")
        if liviano:
            checks.append(Check(GRUPO_LOADS, f"{n}: dimensions and weight", "info",
                                f"Vehicle ({desc}): dimensions are not required for cars, pickups, and motorcycles."))
        elif vehiculo and faltan:
            checks.append(Check(GRUPO_LOADS, f"{n}: dimensions and weight", "manual",
                                f"Load type Vehicle, described as “{desc or 'blank'}”, without {', '.join(faltan)}. "
                                "The app could not tell whether it is a car, pickup, or motorcycle.",
                                "BackOffice confirms whether dimensions are needed for this vehicle.", link, f"Open {n}"))
        else:
            add("dimensions and weight", not faltan, medidas,
                f"Add {', '.join(faltan)} on {n}. Dimensions must be greater than 0 for every load except cars, "
                "pickups, and motorcycles.")
        if "container" in tipo:
            vacio = (l["empty"] or "").strip().upper()
            add("empty or loaded", vacio in ("YES", "NO"), f"Empty: {l['empty'] or 'blank'}.",
                f"Set Empty to YES or NO on {n}.")
            if vacio == "NO":
                add("container contents", bool((l["cargo"] or "").strip() or desc), l["cargo"] or desc or "blank",
                    f"Describe what the container carries in Cargo or in Make or Model on {n}.")
        hitch = (l["hitch"] or "").strip()
        if hitch and norm(hitch) not in ("NO", "NONE", "N A", "0", "FALSE"):
            valido = HITCH_VALIDOS.get(norm(hitch).replace("PINTEL", "PINTLE")) or \
                next((v for k, v in HITCH_VALIDOS.items() if k in norm(hitch)), None)
            add("hitch type", bool(valido), f"Hitch: {hitch}.",
                f"Specify the hitch type on {n}: Ball hitch, Pintle hitch, or 5th Wheel hitch.")
    return checks


# ---------- Pago del Shipment ----------
def revisar_pago(s: dict, link_ship: str, internacional: bool) -> list[Check]:
    pago = s.get("pago_tipo", "")
    t = tipo_pago(pago)
    cp, bpc = _num(s.get("carrier_pay")), _num(s.get("broker_pays_carrier"))
    if not t:
        return [Check(GRUPO_PAGO, "Payment type", "fail", f"Payment type: {pago or 'blank'}.",
                      "Select the payment type in Transfer Specifications.", link_ship, "Open shipment")]
    checks = [Check(GRUPO_PAGO, "Payment type", "ok", pago)]
    montos = f"Carrier Pay {_dinero(cp)}. Broker Pays Carrier {_dinero(bpc)}."
    if t == "broker":
        ok, sol = cp > 0 and abs(cp - bpc) < 0.005, \
            "With Pay by Broker, Carrier Pay and Broker Pays Carrier must be the same amount."
    elif t in ("cod", "cop"):
        ok, sol = cp > 0 and bpc == 0, f"With {NOMBRE_PAGO[t]}, only Carrier Pay is filled. Broker Pays Carrier must be $0."
    elif t == "otr":
        ok, sol = cp > 0 and bpc == 0, "With OTR, only Carrier Pay is filled. STT does not pay the driver."
    else:
        ok, sol = cp > 0 and 0 < bpc < cp, ("With Joint, Carrier Pay is the total (customer plus STT) and Broker "
                                            "Pays Carrier is only the part STT pays.")
    checks.append(Check(GRUPO_PAGO, "Amounts match the payment type", "ok" if ok else "fail", montos,
                        "" if ok else sol, link_ship, "Open shipment"))
    if internacional:
        ok = t == "broker"
        checks.append(Check(GRUPO_PAGO, "International (Naviera) load is Pay by Broker", "ok" if ok else "fail",
                            f"International load. Payment type: {pago}.",
                            "" if ok else "International (Naviera) loads must always be Pay by Broker.",
                            link_ship, "Open shipment"))
    return checks


# ---------- Montos frente a la Orden (respuestas 23 a 29 y 32) ----------
def revisar_montos(actual: dict, otros: list[dict] | None, orden: dict, completos: bool,
                   link_ship: str, link_orden: str) -> list[Check]:
    checks = []
    todos = [actual] + [o for o in (otros or []) if o.get("numero") != actual.get("numero")]
    contados = []
    for s in todos:
        if es_cancelado(s) and _num(s.get("carrier_pay")) > 0 and not es_tonu(s):
            checks.append(Check(GRUPO_MONTOS, f"{s['numero']} is Canceled and still has an amount", "fail",
                                f"{s['numero']} is Canceled with Carrier Pay {_dinero(_num(s.get('carrier_pay')))} "
                                "and no TONU was sent. It is left out of the order totals.",
                                f"Remove the amount from {s['numero']} so the order's shipments add up.",
                                f"{CRM_URL}/Admin/Shipments/Details/{s.get('_id')}" if s.get("_id") else link_orden,
                                f"Open {s['numero']}"))
            continue
        contados.append(s)
    cubetas = {"broker": [], "cod": [], "cop": [], "otr": [], "joint": []}
    for s in contados:
        t = tipo_pago(s.get("pago_tipo"))
        if t:
            cubetas[t].append(s)
    lista = lambda ss, campo="carrier_pay": " + ".join(f"{s['numero']} {_dinero(_num(s.get(campo)))}" for s in ss)
    suma = lambda ss, campo="carrier_pay": round(sum(_num(s.get(campo)) for s in ss), 2)
    comparaciones = []
    if cubetas["broker"] or cubetas["joint"]:
        esperado = suma(cubetas["broker"]) + suma(cubetas["joint"], "broker_pays_carrier")
        partes = [lista(cubetas["broker"])] + ([lista(cubetas["joint"], "broker_pays_carrier") + " (STT part of Joint)"]
                                               if cubetas["joint"] else [])
        comparaciones.append(("Payment Details: Carrier Pay", "carrier_pay", esperado, " + ".join(p for p in partes if p)))
    if cubetas["cod"] or cubetas["joint"]:
        esperado = suma(cubetas["cod"]) + round(sum(_num(s.get("carrier_pay")) - _num(s.get("broker_pays_carrier"))
                                                    for s in cubetas["joint"]), 2)
        partes = [lista(cubetas["cod"])] + ([" + ".join(
            f"{s['numero']} {_dinero(_num(s.get('carrier_pay')) - _num(s.get('broker_pays_carrier')))}"
            for s in cubetas["joint"]) + " (customer part of Joint)"] if cubetas["joint"] else [])
        comparaciones.append(("Agreed Payments: Customer Pays Carrier On Drop Off", "dropoff", esperado,
                              " + ".join(p for p in partes if p)))
    if cubetas["cop"]:
        comparaciones.append(("Agreed Payments: Customer Pays Carrier On Pick Up", "pickup", suma(cubetas["cop"]),
                              lista(cubetas["cop"])))
    if cubetas["otr"]:
        comparaciones.append(("OTR: OTR Pays Carrier", "otr_pays_carrier", suma(cubetas["otr"]), lista(cubetas["otr"])))
    for etiqueta, campo, esperado, detalle in comparaciones:
        en_orden = _num(orden.get(campo))
        ok = abs(en_orden - esperado) < 0.005
        texto = f"Shipments: {detalle} = {_dinero(esperado)}. Order {etiqueta}: {_dinero(en_orden)}."
        if ok:
            checks.append(Check(GRUPO_MONTOS, f"Shipments match {etiqueta}", "ok", texto))
        elif not completos:
            checks.append(Check(GRUPO_MONTOS, f"Shipments match {etiqueta}", "manual",
                                texto + " Not all of the order's shipments could be read.",
                                "BackOffice adds up every shipment of the order.", link_orden, "Open order"))
        else:
            checks.append(Check(GRUPO_MONTOS, f"Shipments match {etiqueta}", "fail", texto,
                                f"The shipments' amounts must add up to the order's {etiqueta} to the cent. "
                                "Correct the shipment or the order.", link_orden, "Open order"))
    cpb_ship, cpb_orden = suma(contados, "carrier_pays_broker"), _num(orden.get("carrier_pays_broker"))
    if cpb_ship > 0 or cpb_orden > 0:
        ok = abs(cpb_ship - cpb_orden) < 0.005
        texto = (f"Shipments: {lista([s for s in contados if _num(s.get('carrier_pays_broker')) > 0], 'carrier_pays_broker') or '$0.00'}"
                 f" = {_dinero(cpb_ship)}. Order Agreed Payments: Carrier Pays Broker {_dinero(cpb_orden)}.")
        estado = "ok" if ok else ("manual" if not completos else "fail")
        checks.append(Check(GRUPO_MONTOS, "Carrier Pays Broker matches the order", estado, texto,
                            "" if ok else ("BackOffice adds up every shipment of the order." if not completos else
                                           "Carrier Pays Broker on the shipments must match Carrier Pays Broker in the "
                                           "order's Agreed Payments."), link_orden, "Open order"))
    return checks


# ---------- Direcciones (respuestas 33 a 38) ----------
def _mismo_lugar(ciudad, estado, zip_, otro: dict) -> bool:
    return (norm(ciudad) == norm(otro.get("ciudad")) and norm(estado) == norm(otro.get("estado"))
            and digitos(zip_)[:5] == digitos(otro.get("zip"))[:5] and bool(digitos(zip_)))


def _lugar_en_texto(ciudad, estado, zip_, texto) -> list[str]:
    """Partes del lugar (city, state, ZIP) que NO aparecen en el texto."""
    faltan = []
    if not (ciudad and norm(ciudad) in norm(texto)):
        faltan.append("city")
    if not (estado and re.search(rf"\b{re.escape(estado.strip().upper())}\b", (texto or "").upper())):
        faltan.append("state")
    z = digitos(zip_)[:5]
    if not (z and re.search(rf"(?<!\d){z}(?!\d)", texto or "")):
        faltan.append("ZIP")
    return faltan


def zips_special_terms(texto: str) -> list[str]:
    """ZIP de las direcciones escritas en Special Terms (estado de dos letras seguido de 5 dígitos)."""
    return list(dict.fromkeys(re.findall(r"\b[A-Za-z]{2},?\s+(\d{5})\b", texto or "")))


def es_internacional(s: dict, orden: dict | None, otros: list[dict] | None, anexo: Anexo) -> bool:
    """Naviera o internacional (respuestas 37 y 40): check International del Shipment, destino de la
    Orden fuera de EE. UU., o un Shipment de la Orden asignado a la naviera (AES COMPANY)."""
    pais = norm((orden or {}).get("destino", {}).get("pais"))
    return bool(s.get("internacional")) or (pais not in ("", "US", "USA", "UNITED STATES")) or \
        any(anexo.es_naviera(a.get("carrier_nombre", "")) for o in (otros or []) for a in o.get("asignaciones", []))


def revisar_direcciones(s: dict, orden: dict, loads: list[dict], internacional: bool, ctx: ContextoOverride,
                        link_ship: str) -> list[Check]:
    checks = []
    st = orden.get("special_terms") or ""
    notas = " ".join(l.get("notas") or "" for l in loads or [])
    for etiqueta, pre, clave in [("Pick-up", "origen", "origen"), ("Delivery", "destino", "destino")]:
        ciudad, estado, zip_ = s.get(f"{pre}_ciudad"), s.get(f"{pre}_estado"), s.get(f"{pre}_zip")
        o = orden.get(clave) or {}
        lugar = f"{ciudad or '?'}, {estado or '?'} {zip_ or ''}".strip()
        lugar_o = f"{o.get('ciudad') or '?'}, {o.get('estado') or '?'} {o.get('zip') or ''}".strip()
        titulo = f"{etiqueta} city, state, and ZIP match the order"
        if _mismo_lugar(ciudad, estado, zip_, o):
            checks.append(Check(GRUPO_DIRS, titulo, "ok", lugar))
        elif clave == "destino" and internacional:
            checks.append(Check(GRUPO_DIRS, titulo, "info",
                                f"International load: the LC covers the land leg only, so the delivery ({lugar}) can "
                                f"differ from the order ({lugar_o})."))
        elif not _lugar_en_texto(ciudad, estado, zip_, st):
            checks.append(Check(GRUPO_DIRS, titulo, "ok", f"Shipment: {lugar}. It is listed in the order's Special Terms."))
        elif not _lugar_en_texto(ciudad, estado, zip_, notas):
            checks.append(Check(GRUPO_DIRS, titulo, "manual",
                                f"Shipment: {lugar}. Order: {lugar_o}. The address is only in the load notes.",
                                "BackOffice confirms the address is authorized in the order and the signed SA.",
                                link_ship, "Open shipment"))
        else:
            motivo = f"The {etiqueta.lower()} ({lugar}) does not match the order ({lugar_o}) or its Special Terms."
            chk = revisar_override("direccion", GRUPO_DIRS, motivo, ctx)
            if chk.estado == "ok":
                checks.append(chk)
                checks.append(Check(GRUPO_DIRS, "Customer's authorization for the address change", "manual",
                                    "The customer's authorization (photo) must be in the order's Documents, usually "
                                    "named “Proof of address” or “photo”.",
                                    "BackOffice finds and checks the customer's authorization in the order's Documents.",
                                    link_ship, "Open shipment"))
            else:
                if chk.estado in FRENAN:
                    chk.solucion = ("Upload the customer's authorization for the new address (a photo) to the order's "
                                    "Documents, then " + chk.solucion[0].lower() + chk.solucion[1:])
                checks.append(chk)
    return checks


# ---------- Special Instructions (respuestas 48 a 56) ----------
def revisar_si(s: dict, da: dict | None, link_ship: str, permitidos: dict, contactos: list[dict],
               una_sola: bool, zips_st: list[str]) -> list[Check]:
    si = s.get("special_instructions", "")
    if not si.strip():
        return [Check(GRUPO_SI, "Special Instructions", "fail", "Special Instructions are blank.",
                      "Complete the Special Instructions with the driver, dates, and broker contact.",
                      link_ship, "Open shipment")]
    checks = []

    def add(titulo, estado, detalle, sol=""):
        checks.append(Check(GRUPO_SI, titulo, estado, detalle, sol if estado != "ok" else "", link_ship, "Open shipment"))

    # Fechas: se comparan con Estimated Pickup Date y Estimated Delivery Date
    for etiqueta, claves, campo in [
        ("Pick-up date", [r"pick[\s-]?up date", r"\bPU date", r"pick[\s-]?up"], "fecha_pu"),
        ("Delivery date", [r"deliver(?:y|ies)? date", r"drop[\s-]?off date", r"\bDEL date", r"deliver"], "fecha_entrega"),
    ]:
        en_info = _fecha(s.get(campo))
        en_si = _fecha_cerca(si, claves, en_info)
        campo_txt = "Estimated Pickup Date" if campo == "fecha_pu" else "Estimated Delivery Date"
        if en_si is None:
            add(f"{etiqueta} in Special Instructions", "fail", f"No {etiqueta.lower()} found in Special Instructions.",
                f"Add the {etiqueta.lower()} to Special Instructions (MM/DD/YY or MM/DD). It must match {campo_txt}.")
        elif en_info is None:
            add(f"{etiqueta} matches {campo_txt}", "fail", f"Special Instructions: {_fmt(en_si)}. {campo_txt}: blank.",
                f"Complete {campo_txt} in Shipment Info.")
        else:
            add(f"{etiqueta} matches {campo_txt}", "ok" if en_si == en_info else "fail",
                f"Special Instructions: {_fmt(en_si)}. {campo_txt}: {_fmt(en_info)}.",
                f"The {etiqueta.lower()} must be the same in Special Instructions and in {campo_txt}.")

    tel_driver = digitos((da or {}).get("telefono"))[-10:]
    tel_dispatch = digitos((da or {}).get("dispatch_telefono"))[-10:]

    # Driver: al menos un nombre igual al del Driver Assignment, nunca apodos; teléfono igual
    if da:
        m = (re.search(r"driver\s*[:\-]?\s*\(((?:[^()]|\([^()]*\))*)\)", si, re.I)
             or re.search(r"driver\s*[:\-]\s*([^\n]+)", si, re.I))
        tramo = m.group(1) if m else si
        nombres = [t for t in norm(da.get("driver")).split() if len(t) >= 2 and t not in SUFIJOS]
        ok_nombre = bool(nombres) and any(re.search(rf"\b{t}\b", norm(tramo)) for t in nombres)
        add("Driver name matches the Driver Assignment", "ok" if ok_nombre else "fail",
            f"Driver Assignment: {da.get('driver') or 'blank'}." + (f" Special Instructions: {tramo.strip()[:80]}." if m else ""),
            "Write at least one of the driver's names in Special Instructions exactly as on the Driver Assignment and "
            "the license. Nicknames are not accepted.")
        ok_tel = bool(tel_driver) and tel_driver in _telefonos(si)
        add("Driver phone matches the Driver Assignment", "ok" if ok_tel else "fail",
            f"Driver Assignment: {da.get('telefono') or 'blank'}.",
            "Write the driver's phone in Special Instructions. It must be the Driver Phone on the Driver Assignment.")

        # Dispatcher del carrier: si lo escriben, tiene que coincidir (respuesta 54)
        md = re.search(r"dispatch(?:er)?\s*[:\-]?\s*\(((?:[^()]|\([^()]*\))*)\)", si, re.I) or \
            re.search(r"dispatch(?:er)?\s*[:\-]\s*([^\n]+)", si, re.I)
        if md:
            tramo_d = md.group(1)
            de_stt = any(norm(n) and all(re.search(rf"\b{t}\b", norm(tramo_d)) for t in _tokens_nombre(n))
                         for n in permitidos.values() if n)
            if not de_stt:
                nombres_d = [t for t in norm(da.get("dispatch_nombre")).split() if len(t) >= 2]
                tels_d = _telefonos(tramo_d)
                ok_n = not nombres_d or any(re.search(rf"\b{t}\b", norm(tramo_d)) for t in nombres_d)
                ok_t = not tels_d or (bool(tel_dispatch) and tel_dispatch in tels_d)
                vacio = not nombres_d and not tel_dispatch
                ok = ok_n and ok_t and not vacio
                add("Carrier dispatcher matches the Driver Assignment", "ok" if ok else "fail",
                    f"Special Instructions: {tramo_d.strip()[:80]}. Driver Assignment: "
                    f"{da.get('dispatch_nombre') or 'no Dispatch Name'}, {da.get('dispatch_telefono') or 'no Dispatch Phone'}.",
                    "The carrier's dispatcher in Special Instructions must match Dispatch Name and Dispatch Phone on "
                    "the Driver Assignment.")

    # Contacto del broker o dispatcher de STT: email y teléfono obligatorios (respuesta 52)
    correos = re.findall(r"[\w.+\-]+@[\w\-]+\.[\w.]+", si)
    otros_tel = [t for t in _telefonos(si) if t not in (tel_driver, tel_dispatch)]
    if not correos or not otros_tel:
        falta = " and ".join(x for x, ok in [("email", correos), ("phone", otros_tel)] if not ok)
        add("Broker or dispatcher email and phone", "fail", f"No broker or dispatcher {falta} found in Special Instructions.",
            "Add both the email and the phone of the broker or dispatcher handling the shipment.")
    else:
        quien = None
        for c in contactos:
            if c.get("email") and any(c["email"].lower() == x.lower() for x in correos):
                quien = c["nombre"]
        if quien is None:
            for rol, nombre in permitidos.items():
                tokens = [t.lower() for t in _tokens_nombre(nombre) if len(t) >= 3]
                if tokens and any(t in x.split("@")[0].lower() for x in correos for t in tokens):
                    quien = f"{nombre}, {rol}"
                    break
        if quien is None:
            for c in contactos:
                tokens = [t.lower() for t in _tokens_nombre(c["nombre"]) if len(t) >= 3]
                if tokens and any(t in x.split("@")[0].lower() for x in correos for t in tokens):
                    quien = c["nombre"]
        if quien:
            add("Broker or dispatcher email and phone", "ok", f"{', '.join(correos)} ({quien}).")
        else:
            add("Broker or dispatcher email and phone", "fail",
                f"Email found: {', '.join(correos)}. It does not belong to the Shipment Owner or the Dispatcher.",
                "Use the email and phone of the broker or dispatcher handling the shipment (Shipment Owner or "
                "Dispatcher Id).")

    # Truck type mencionado en Special Instructions
    en_si = tipo_camion(si)
    campo = tipo_camion(s.get("truck_type", ""))
    if en_si and campo and not set(en_si) & set(campo):
        add("Truck type matches Special Instructions", "fail",
            f"Truck Specifications: {s.get('truck_type')}. Special Instructions mention: {', '.join(en_si)}.",
            "The truck type in Special Instructions must match Truck Specifications.")

    # Varias direcciones en la Orden y un solo Shipment (respuesta 38)
    if una_sola and len(zips_st) >= 2:
        faltan = [z for z in zips_st if not re.search(rf"(?<!\d){z}(?!\d)", si)]
        add("Every address in the order's Special Terms is listed", "ok" if not faltan else "fail",
            f"Special Terms list {len(zips_st)} addresses (ZIP {', '.join(zips_st)})."
            + (f" Missing in Special Instructions: {', '.join(faltan)}." if faltan else ""),
            "This is the order's only shipment, so every pick-up and delivery address from the Special Terms must be "
            "broken down in Special Instructions.")
    return checks


TIPOS_CAMION = {
    "RGNE": [r"\bRGNE\b"], "RGN": [r"\bRGN\b"], "DOUBLE DROP": [r"double[\s-]?drop"],
    "STEP DECK": [r"step[\s-]?deck", r"\bSDL\b"], "DRY VAN": [r"dry[\s-]?van"],
    "REEFER": [r"\breefer\b", r"\brefeer\b", r"refrigerated"], "FLATBED": [r"flat[\s-]?bed"],
    "POWER ONLY": [r"power[\s-]?only"], "LOWBOY": [r"low[\s-]?boy"], "HOT SHOT": [r"hot[\s-]?shot"],
    "FLAT RACK": [r"flat[\s-]?rack"], "BEAM TRAILER": [r"beam[\s-]?trailer"], "BOX TRUCK": [r"box[\s-]?truck"],
    "STRAIGHT VAN": [r"straight[\s-]?van"], "TILT BED": [r"tilt[\s-]?bed"], "CAR HAULER": [r"car[\s-]?hauler"],
    "CONESTOGA": [r"conestoga"], "DRIVE AWAY": [r"drive[\s-]?away"],
}


def tipo_camion(texto: str) -> list[str]:
    return [canon for canon, pats in TIPOS_CAMION.items() if any(re.search(p, texto or "", re.I) for p in pats)]


# ---------- COI (respuestas 57 a 67) ----------
ABREVIATURAS = {"STREET": "ST", "AVENUE": "AVE", "AV": "AVE", "ROAD": "RD", "DRIVE": "DR", "LANE": "LN",
                "BOULEVARD": "BLVD", "HIGHWAY": "HWY", "HWY": "HWY", "COURT": "CT", "PLACE": "PL", "PARKWAY": "PKWY",
                "CIRCLE": "CIR", "TERRACE": "TER", "SUITE": "STE", "APARTMENT": "APT", "NORTH": "N", "SOUTH": "S",
                "EAST": "E", "WEST": "W", "NORTHEAST": "NE", "NORTHWEST": "NW", "SOUTHEAST": "SE", "SOUTHWEST": "SW",
                "TRAIL": "TRL", "WAY": "WAY", "SQUARE": "SQ", "EXPRESSWAY": "EXPY", "FREEWAY": "FWY", "ROUTE": "RTE",
                "PO": "PO", "P O": "PO", "BOX": "BOX", "FIRST": "1ST", "SECOND": "2ND", "THIRD": "3RD"}


def norm_dir(texto: str) -> str:
    """Dirección normalizada: acepta abreviaturas distintas (STREET = ST, LANE = LN), como pide la respuesta 61."""
    t = norm(texto).replace("P O BOX", "PO BOX")
    return " ".join(ABREVIATURAS.get(p, p) for p in t.split())


def _direccion_en_coi(calle: str, zip_: str, texto: str) -> bool:
    return bool(calle) and norm_dir(calle) in norm_dir(texto) and \
        (not zip_ or bool(re.search(rf"(?<!\d){zip_}(?!\d)", texto)))


def _fechas_cobertura(texto: str, palabras: list[str]) -> tuple[date | None, date | None, str]:
    """Fechas de inicio y fin de la línea de la cobertura (por ejemplo: Cargo … 07/11/2026 07/11/2027 Limit…)."""
    up = texto.upper()
    for p in palabras:
        for m in re.finditer(re.escape(p.upper()), up):
            ventana = texto[m.end(): m.end() + 140]
            fs = [d for d in (_fecha(x) for x in re.findall(r"\d{1,2}/\d{1,2}/\d{2,4}", ventana)) if d]
            if len(fs) >= 2 and fs[1] > fs[0]:
                return fs[0], fs[1], ventana
    return None, None, ""


def _pares_fechas(texto: str) -> list[tuple[date, date]]:
    pares = []
    for m in re.finditer(r"(\d{1,2}/\d{1,2}/\d{2,4})\s+(\d{1,2}/\d{1,2}/\d{2,4})", texto):
        a, b = _fecha(m.group(1)), _fecha(m.group(2))
        if a and b and b > a:
            pares.append((a, b))
    return pares


def _limites(ventana: str) -> list[float]:
    """Límites de la línea de cobertura: 'Limit: 250000.00' o montos con signo de dólar. No toma números
    pegados a fechas o al número de póliza."""
    xs = re.findall(r"limit[^0-9$\n]{0,15}\$?\s?(\d[\d,]*(?:\.\d{2})?)", ventana or "", re.I)
    xs = xs or re.findall(r"\$\s?(\d{1,3}(?:,\d{3})+(?:\.\d{2})?|\d{4,}(?:\.\d{2})?)", ventana or "")
    return [_num(x) for x in xs]


def monto_cobertura_requerido(special_terms: str) -> float | None:
    """Monto exacto de cobertura pedido en Special Terms de la Orden (respuesta 59)."""
    for m in re.finditer(r"insurance|coverage|cargo|liability|\bCOI\b", special_terms or "", re.I):
        ventana = special_terms[max(0, m.start() - 60): m.end() + 60]
        dinero = re.findall(r"\$\s?(\d{1,3}(?:,\d{3})+|\d{4,})(?:\.\d{2})?", ventana)
        if dinero:
            return max(_num(x) for x in dinero)
    return None


def _vins(loads: list[dict]) -> list[str]:
    """VIN de las loads de Drive Away: casi siempre en Notes, a veces en Lot Number (respuesta 47)."""
    vins = []
    for l in loads or []:
        for t in (l.get("notas") or "", l.get("lot") or ""):
            vins += re.findall(r"\b[A-HJ-NPR-Z0-9]{17}\b", t.upper())
    return list(dict.fromkeys(vins))


def evaluar_coi(texto: str, m: dict, s: dict, loads: list[dict], anexo: Anexo, hoy: date,
                requerido: float | None) -> dict:
    """Revisa un COI. Devuelve las fallas separadas en: graves (no se pueden aprobar con Override),
    con_override (se pueden aprobar con Override) y manuales (BackOffice las mira)."""
    t = norm(texto)
    graves, con_override, manuales, bien = [], [], [], []
    pu, entrega = _fecha(s.get("fecha_pu")), _fecha(s.get("fecha_entrega"))

    # Nombre: Legal Name o DBA de MOTUS (respuesta 60)
    if (m["legal"] and norm(m["legal"]) in t) or (m.get("dba") and norm(m["dba"]) in t):
        bien.append("name matches MOTUS")
    else:
        graves.append(f"the insured name is not {m['legal']}" + (f" or {m['dba']}" if m.get("dba") else ""))

    # Dirección física o de correo de MOTUS, con abreviaturas (respuesta 61)
    dir_ok = _direccion_en_coi(m["calle"], m["zip"], texto) or \
        _direccion_en_coi(m.get("calle_correo", ""), m.get("zip_correo", ""), texto)
    stt_holder = "STT LOGISTICS" in t
    if re.search(r"FREIGHT CONNECT|\bFCL\b", t):
        graves.append("Freight Connect Logistics (FCL) appears on the COI")
    if dir_ok:
        bien.append("address matches MOTUS")
    elif stt_holder:
        bien.append("STT is the certificate holder")
    else:
        con_override.append("the address is not the MOTUS address and STT is not the certificate holder")

    # Cobertura según el tipo de camión (respuesta 57 y 58)
    camion = anexo.coberturas(s.get("truck_type", ""))
    etiquetas_en_texto = bool(re.search(r"GENERAL LIABILITY|TYPE OF INSURANCE|WORKERS COMPENSATION|UMBRELLA", t))
    palabras = []
    if camion is None:
        manuales.append(f"the truck type “{s.get('truck_type') or 'blank'}” is not in the coverage table")
    else:
        nombre_camion, coberturas = camion
        palabras = [p for c in coberturas for p in anexo.palabras_cobertura(c)]
        tiene = any(norm(p) in t for p in palabras)
        if not tiene and nombre_camion == "DRIVE AWAY":
            vin = next((v for v in _vins(loads) if v in texto.upper().replace(" ", "")), None)
            if vin:
                tiene = True
                bien.append(f"covers VIN {vin} of the load")
            else:
                manuales.append("no Drive Away coverage and no VIN of the load on the COI (check the insurance "
                                "agency's call on the driver's profile)")
        if tiene:
            bien.append(f"{' or '.join(c.title() for c in coberturas)} coverage")
        elif nombre_camion != "DRIVE AWAY":
            texto_falta = f"no {' or '.join(c.title() for c in coberturas)} coverage for a {nombre_camion}"
            (graves if etiquetas_en_texto else manuales).append(texto_falta)

    # Vigencia: empieza antes del pick-up y dura 10 días después del delivery (respuestas 63, 64 y 66)
    ini, fin, ventana = _fechas_cobertura(texto, palabras) if palabras else (None, None, "")
    if fin is None:
        pares = _pares_fechas(texto)
        if pares:
            ini, fin = max(pares, key=lambda p: p[1])
    if fin is None:
        manuales.append("the coverage dates could not be read")
    else:
        if pu and ini and ini > pu:
            graves.append(f"the coverage starts {_fmt(ini)}, after the pick-up date ({_fmt(pu)})")
        limite_vencido = max(d for d in (hoy, pu) if d)
        if fin < limite_vencido:
            graves.append(f"the COI expired on {_fmt(fin)}")
        elif entrega and fin < entrega + timedelta(days=10):
            con_override.append(f"the coverage ends {_fmt(fin)}, less than 10 days after the delivery date "
                                f"({_fmt(entrega)})")
        else:
            bien.append(f"valid {_fmt(ini) if ini else '?'} to {_fmt(fin)}")

    # Monto exacto pedido en Special Terms (respuesta 59)
    if requerido:
        limites = _limites(ventana) if ventana else []
        if limites and max(limites) >= requerido:
            bien.append(f"limit {_dinero(max(limites))} meets the {_dinero(requerido)} in Special Terms")
        elif limites:
            graves.append(f"the coverage limit ({_dinero(max(limites))}) is below the {_dinero(requerido)} required in "
                          "the order's Special Terms")
        else:
            manuales.append(f"the order's Special Terms require {_dinero(requerido)} of coverage; the limit could "
                            "not be read")
    return {"graves": graves, "con_override": con_override, "manuales": manuales, "bien": bien}


def revisar_coi(archivos: list[dict] | None, leer_pdf, m: dict, s: dict, loads: list[dict], anexo: Anexo,
                hoy: date, requerido: float | None, ctx: ContextoOverride, link_car: str) -> list[Check]:
    titulo = "Certificate of Insurance (COI)"
    if archivos is None:
        return [Check(GRUPO_DOCS, titulo, "manual", "The carrier's files could not be read.",
                      "BackOffice reviews the COI.", link_car, "View carrier files")]
    cands = [f for f in archivos if "COI" in f["tipo"].upper() or "INSURANCE" in f["tipo"].upper()
             or re.search(r"\bCOI\b|INSURANCE|CERTIFICATE", f["archivo"].upper())]
    if not cands:
        return [Check(GRUPO_DOCS, titulo, "fail", "There is no COI in the carrier's files.",
                      "Upload the carrier's current COI to the carrier's files (Carrier Management Detail).",
                      link_car, "Open carrier")]
    evaluados = []
    for f in cands:
        texto = leer_pdf(f["guid"]) if f["guid"] else None
        if texto is None:
            continue
        r = evaluar_coi(texto, m, s, loads, anexo, hoy, requerido)
        evaluados.append((len(r["graves"]), len(r["con_override"]), len(r["manuales"]), f, r))
    if not evaluados:
        return [Check(GRUPO_DOCS, titulo, "manual",
                      f"{', '.join(f['archivo'] for f in cands)} could not be read automatically (it may be a scan).",
                      "BackOffice reviews the COI.", link_car, "View carrier files")]
    evaluados.sort(key=lambda x: x[:3])
    _, _, _, f, r = evaluados[0]
    link = f"{CRM_URL}/Admin/Download/DownloadFile?downloadGuid={f['guid']}"
    resumen = lambda xs: "; ".join(xs)
    detalle = f"{f['archivo']}." + (f" OK: {resumen(r['bien'])}." if r["bien"] else "")
    if r["graves"]:
        nunca = any("expired" in g or "starts" in g for g in r["graves"])
        return [Check(GRUPO_DOCS, titulo, "fail", f"{detalle} Problem: {resumen(r['graves'] + r['con_override'])}.",
                      "Get a current COI from the carrier and upload it to the carrier's files."
                      + (" An expired COI, or one whose coverage starts after the load starts moving, can never be "
                         "approved with an override." if nunca else ""), link, "Open COI")]
    checks = []
    if r["con_override"]:
        motivo = f"COI: {resumen(r['con_override'])}."
        checks.append(Check(GRUPO_DOCS, titulo, "ok", detalle, "", link, "Open COI"))
        checks.append(revisar_override("coi", GRUPO_DOCS, motivo, ctx))
        if checks[-1].estado in FRENAN:
            checks[-1].solucion = ("Get an updated COI from the carrier, or " + checks[-1].solucion[0].lower()
                                   + checks[-1].solucion[1:])
    else:
        checks.append(Check(GRUPO_DOCS, titulo, "ok", detalle, "", link, "Open COI"))
    if r["manuales"]:
        checks.append(Check(GRUPO_DOCS, "COI details the app could not confirm", "manual",
                            f"{f['archivo']}: {resumen(r['manuales'])}.", "BackOffice confirms these COI details.",
                            link, "Open COI"))
    return checks


# ---------- Shipper Agreement firmado (respuestas 4 a 10) ----------
def etapa_orden(orden: dict) -> str:
    conocidas = ETAPAS_SIN_SA + (ETAPA_FIRMA,) + ETAPAS_FIRMADA + (ETAPA_PERDIDA,)

    def limpia(e):
        e = (e or "").strip().upper()
        return ETAPA_FIRMA if e.startswith("AWAITING") else e

    barra, campo = limpia(orden.get("etapa")), limpia(orden.get("estado_campo"))
    if barra in conocidas:
        return barra
    return campo if campo in conocidas else (barra or campo)


def sa_revocado(orden: dict, s: dict) -> dict | None:
    """El último request del SA es un Revoke hecho: el SA firmado anterior ya no vale (respuesta 7)."""
    del_sa = [r for r in (orden.get("requests") or []) + (s.get("requests") or [])
              if re.search(r"shipper|\bSA\b", r.get("documento", ""), re.I)]
    if not del_sa:
        return None
    ultimo = max(del_sa, key=lambda r: r.get("num") or 0)
    return ultimo if "revoke" in ultimo.get("tipo", "").lower() and \
        (ultimo.get("respuesta") or "").strip().lower() == "done" else None


def es_cliente_confidencial(orden: dict, confidenciales) -> bool:
    """Cliente con crédito interno (respuestas 19 a 22). El nombre solo vive en los secrets de la app."""
    nombres = [orden.get("cliente", "")] + [c["nombre"] for c in orden.get("contactos", [])
                                             if "signer" in (c.get("rol") or "").lower()]
    return any(norm(x) and norm(x) in norm(n) for x in confidenciales or [] for n in nombres)


def revisar_texto_sa(texto: str, s: dict, loads: list[dict], internacional: bool, monto: float,
                     etiqueta_monto: str) -> tuple[list[str], list[str]]:
    """Qué le falta al SA firmado frente al Shipment. Devuelve (fallas, revisión manual)."""
    fallas, manuales = [], []
    for etiqueta, pre in [("pick-up", "origen"), ("delivery", "destino")]:
        if pre == "destino" and internacional:
            continue
        faltan = _lugar_en_texto(s.get(f"{pre}_ciudad"), s.get(f"{pre}_estado"), s.get(f"{pre}_zip"), texto)
        if faltan:
            fallas.append(f"the {etiqueta} {', '.join(faltan)} ({s.get(f'{pre}_ciudad')}, {s.get(f'{pre}_estado')} "
                          f"{s.get(f'{pre}_zip')})")
    for l in loads or []:
        n = l.get("numero") or "load"
        for etiqueta, valor in [("make", l.get("make")), ("model", l.get("model"))]:
            if (valor or "").strip() and norm(valor) not in norm(texto):
                fallas.append(f"the {etiqueta} of {n} ({valor})")
        if not (l.get("make") or "").strip() and not (l.get("model") or "").strip():
            fallas.append(f"the description of {n} (Make and Model are blank on the shipment)")
        if _num(l.get("year")) > 1900 and not re.search(rf"(?<!\d){int(_num(l['year']))}(?!\d)", texto):
            fallas.append(f"the year of {n} ({int(_num(l['year']))})")
        sin_ver = [etq for etq, v in [("weight", l.get("weight")), ("length", l.get("length")),
                                      ("width", l.get("width")), ("height", l.get("height"))]
                   if _num(v) > 0 and not re.search(_numero_regex(_num(v)), texto)]
        if sin_ver:
            manuales.append(f"the {', '.join(sin_ver)} of {n} were not found on the SA")
    if monto > 0 and not _monto_en_texto(monto, texto):
        fallas.append(f"the order's current full amount ({etiqueta_monto} {_dinero(monto)})")
    return fallas, manuales


def revisar_orden(orden: dict, s: dict, loads: list[dict], internacional: bool, otros: list[dict] | None,
                  docs_orden: list[dict] | None, leer_pdf, confidenciales, ctx: ContextoOverride,
                  link_orden: str) -> list[Check]:
    checks = []
    etapa = etapa_orden(orden)
    add = checks.append
    if etapa in ETAPAS_SIN_SA:
        add(Check(GRUPO_ORDEN, "Order status allows the LC", "fail",
                  f"Order {orden['numero']} is in {etapa}: the Shipper Agreement has not been sent yet.",
                  "Send the Shipper Agreement to the customer first. The LC can be requested once the order is in "
                  "Awaiting Customer Signature (with an approved override) or Work in Progress.", link_orden, "Open order"))
        return checks
    if etapa == ETAPA_PERDIDA:
        add(Check(GRUPO_ORDEN, "Order status allows the LC", "fail",
                  f"Order {orden['numero']} is Closed Lost. No SA or LC can be sent for its shipments.",
                  "Do not request the LC. If the order is active again, update the order's stage first.",
                  link_orden, "Open order"))
        return checks
    if etapa == ETAPA_FIRMA:
        add(Check(GRUPO_ORDEN, "Order status allows the LC", "ok", f"{orden['numero']}: Awaiting Customer Signature."))
        if es_cliente_confidencial(orden, confidenciales):
            add(Check(GRUPO_ORDEN, "Unsigned SA: internal-credit customer", "manual",
                      "Confidential internal-credit customer: the LC can be sent without the signed SA and without an "
                      "override (this exception covers only the unsigned SA).",
                      "BackOffice confirms the customer.", link_orden, "Open order"))
        else:
            add(revisar_override("sa", GRUPO_ORDEN, "The customer has not signed the Shipper Agreement yet.", ctx))
        return checks
    if etapa not in ETAPAS_FIRMADA:
        add(Check(GRUPO_ORDEN, "Order status allows the LC", "manual",
                  f"Order {orden['numero']} shows a stage the app does not know: {etapa or 'blank'}.",
                  "BackOffice reviews the order's stage and the signed SA.", link_orden, "Open order"))
        return checks

    add(Check(GRUPO_ORDEN, "Order status allows the LC", "ok", f"{orden['numero']}: {etapa.title()}."))
    revocado = sa_revocado(orden, s)
    if revocado:
        add(Check(GRUPO_ORDEN, "Signed Shipper Agreement is current", "fail",
                  f"The Shipper Agreement was revoked ({revocado['id']}) and no new SA was requested after it, so the "
                  "previously signed SA is no longer valid.",
                  "Send the updated Shipper Agreement and have the customer sign it before requesting the LC.",
                  link_orden, "Open order"))
        return checks
    firmados = orden.get("sign_docs") or []
    if not firmados:
        posibles = [d for d in docs_orden or []]
        if docs_orden is None:
            add(Check(GRUPO_ORDEN, "Signed Shipper Agreement", "manual",
                      "There is no signed SA in the order's Sign Documents, and the order's Documents could not be read.",
                      "BackOffice looks for the signed SA and the customer's email.", link_orden, "Open order"))
        elif posibles:
            add(Check(GRUPO_ORDEN, "Signed Shipper Agreement", "manual",
                      "There is no signed SA in Sign Documents. The order's Documents have: "
                      + ", ".join(d["archivo"] for d in posibles[:6]) + ".",
                      "BackOffice checks the signed SA in Documents and the proof that the customer sent it (a "
                      "screenshot showing the Order ID, or the customer's email forwarded to backoffice@sttlg.us).",
                      link_orden, "Open order"))
        else:
            add(Check(GRUPO_ORDEN, "Signed Shipper Agreement", "fail",
                      "There is no signed SA in the order's Sign Documents or Documents.",
                      "If the customer signed outside the CRM, upload the signed SA to the order's Documents with proof "
                      "that the customer sent it (a screenshot showing the Order ID), and forward the customer's email "
                      "to backoffice@sttlg.us.", link_orden, "Open order"))
        return checks

    sa = firmados[0]   # el primero de arriba es el más reciente (respuesta 6)
    link_sa = f"{CRM_URL}/Admin/Download/DownloadFile?downloadGuid={sa['guid']}" if sa["guid"] else link_orden
    add(Check(GRUPO_ORDEN, "Signed Shipper Agreement", "ok", f"{sa['archivo']} (most recent in Sign Documents).",
              "", link_sa, "Open the signed SA"))
    texto = leer_pdf(sa["guid"]) if sa["guid"] else None
    otr = any(tipo_pago(x.get("pago_tipo")) == "otr" for x in [s] + list(otros or [])) or \
        _num(orden.get("customer_pays_otr")) > 0
    monto, etiqueta = ((_num(orden.get("customer_pays_otr")), "Customer Pays OTR") if otr
                       else (_num(orden.get("agreed_payments")), "Agreed Payments"))
    if texto is None:
        add(Check(GRUPO_ORDEN, "Signed SA matches the shipment", "manual",
                  f"{sa['archivo']} could not be read automatically.",
                  f"BackOffice checks that the SA shows the shipment's pick-up and delivery (city, state, ZIP), its "
                  f"load, and the order's full amount ({etiqueta} {_dinero(monto)}).", link_sa, "Open the signed SA"))
        return checks
    fallas, manuales = revisar_texto_sa(texto, s, loads, internacional, monto, etiqueta)
    if fallas:
        add(Check(GRUPO_ORDEN, "Signed SA matches the shipment", "fail",
                  f"{sa['archivo']} does not show: {'; '.join(fallas)}.",
                  "The signed SA must show the shipment's pick-up and delivery (city, state, ZIP), its load, and the "
                  "order's current full amount. If anything changed after the customer signed, send an updated SA "
                  "for signature.", link_sa, "Open the signed SA"))
    else:
        add(Check(GRUPO_ORDEN, "Signed SA matches the shipment", "ok",
                  f"Pick-up, delivery{' (land leg)' if internacional else ''}, load, and full amount "
                  f"({etiqueta} {_dinero(monto)}) are on the SA.", "", link_sa, "Open the signed SA"))
    if manuales:
        add(Check(GRUPO_ORDEN, "SA load details", "manual", "; ".join(manuales) + ".",
                  "BackOffice compares the rest of the load details on the SA.", link_sa, "Open the signed SA"))
    return checks


# ---------- Licencia (respuestas 70 a 72) ----------
def revisar_licencia(archivos: list[dict] | None, da: dict | None, s: dict, app_driver: bool,
                     link_car: str) -> list[Check]:
    titulo = "Driver's license"
    if archivos is None:
        return [Check(GRUPO_DOCS, titulo, "manual", "The carrier's files could not be read.",
                      "BackOffice reviews the driver's license.", link_car, "View carrier files")]
    lic = [f for f in archivos if "LICENSE" in f["tipo"].upper()
           or re.search(r"\bDL\b|LICEN|CONSTANCIA", f["archivo"].upper())]
    nombres = [t for t in norm((da or {}).get("driver")).split() if len(t) >= 2 and t not in SUFIJOS]
    del_driver = [f for f in lic if any(re.search(rf"\b{t}\b", norm(f["archivo"])) for t in nombres)]
    if lic:
        elegidos = del_driver or lic
        checks = [Check(GRUPO_DOCS, titulo, "ok", ", ".join(f["archivo"] for f in elegidos))]
        checks.append(Check(GRUPO_DOCS, "Driver's license is legible and belongs to the driver", "manual",
                            f"{', '.join(f['archivo'] for f in elegidos)}. The driver in Special Instructions and on the "
                            f"Driver Assignment is {(da or {}).get('driver') or 'not set'}.",
                            "BackOffice confirms the photo and name are legible and match the driver.",
                            link_car, "View carrier files"))
        return checks
    if app_driver:
        return [Check(GRUPO_DOCS, titulo, "info",
                      "No license in the carrier's files. It is not required for STT app drivers.")]
    si = (s.get("special_instructions") or "").lower()
    alternativa = all(re.search(p, si) for p in (r"truck", r"trailer", r"plate"))
    if alternativa:
        fotos = [f for f in archivos if re.search(r"\.(png|jpe?g|heic|webp)$", f["archivo"].lower())
                 or "PHOTO" in f["tipo"].upper() or "PICTURE" in f["tipo"].upper()]
        if fotos:
            return [Check(GRUPO_DOCS, titulo, "ok", "No license photo. Special Instructions list the truck, trailer, "
                                                    "and plate numbers instead."),
                    Check(GRUPO_DOCS, "Truck photo shows the DOT#", "manual",
                          f"Photos in the carrier's files: {', '.join(f['archivo'] for f in fotos[:4])}.",
                          "BackOffice checks the truck photo and the DOT# in SaferWeb.", link_car, "View carrier files")]
        return [Check(GRUPO_DOCS, titulo, "fail",
                      "No license photo. Special Instructions list the truck, trailer, and plate numbers, but there is no "
                      "truck photo in the carrier's files.",
                      "Upload a photo of the truck showing the DOT# to the carrier's files.", link_car, "Open carrier")]
    return [Check(GRUPO_DOCS, titulo, "fail", "No driver's license found in the carrier's files.",
                  "Upload a legible photo of the driver's license (Document Type: Driver's License) to the carrier's "
                  "files. If the driver will not share it, add the driver's name and phone, truck and trailer numbers, "
                  "and plate number to Special Instructions, and upload a truck photo showing the DOT# to the "
                  "carrier's files.", link_car, "Open carrier")]


# ---------- Motor de la LC ----------
def evaluar_lc(shipment_id: int, shipment: dict | None, asignacion: dict | None, da: dict | None,
               carrier: dict | None, archivos: list[dict] | None, motus_data: dict | None, leer_pdf,
               conductores: list[dict] | None = None, loads: list[dict] | None = None,
               motus_error: str | None = None, orden: dict | None = None, otros: list[dict] | None = None,
               otros_completos: bool = True, docs_orden: list[dict] | None = None,
               overrides: list[dict] | None = None, anexo: Anexo | None = None, confidenciales=(),
               solicitante: str = "", solicitante_origen: str = "", hoy: date | None = None,
               nota_da: Check | None = None, error_orden: str | None = None) -> ResultadoLC:
    """Aplica el procedimiento de Load Confirmation de BackOffice. Función pura (sin red)."""
    anexo = anexo or Anexo()
    leer_pdf = leer_pdf or (lambda guid: None)
    hoy = hoy or datetime.now(timezone.utc).date()
    res = ResultadoLC(shipment_id, shipment, asignacion, da, carrier, loads=loads or [], orden=orden)
    add = res.checks.append
    if shipment is None:
        add(Check(GRUPO_INFO, "Shipment found", "fail", f"Shipment S-{shipment_id:06d} does not exist in the CRM.",
                  "Check the shipment number and try again."))
        return res
    s = shipment
    link_ship = f"{CRM_URL}/Admin/Shipments/Details/{shipment_id}"
    if orden is None:
        res.error_lectura = error_orden or "the shipment's order could not be read"
        return res
    link_orden = f"{CRM_URL}/Admin/Orders/Details/{orden.get('id') or s.get('orden_id')}"

    internacional = es_internacional(s, orden, otros, anexo)
    dueno_pide = solicitante_origen in ("manual", "owner") and anexo.es_dueno(solicitante)
    for o in overrides or []:
        o["propositos"] = propositos_override(o.get("comentario", ""))
    ctx = ContextoOverride(overrides or [], anexo.aprobadores(s.get("supervisor", "")), dueno_pide,
                           s.get("supervisor", ""), link_ship)
    casos = []
    if internacional:
        casos.append("International (Naviera)")

    # 1. Orden y SA firmado
    res.checks += revisar_orden(orden, s, loads or [], internacional, otros, docs_orden, leer_pdf,
                                confidenciales, ctx, link_orden)

    # 2. Shipment info
    for etiqueta, campo in [("Estimated pick-up date", "fecha_pu"), ("Estimated delivery date", "fecha_entrega")]:
        f = _fecha(s.get(campo))
        add(Check(GRUPO_INFO, etiqueta, "ok" if f else "fail", _fmt(f) if f else "Blank.",
                  "" if f else f"Complete the {etiqueta.lower()} in Shipment Info.", link_ship, "Open shipment"))

    # 3. Loads (y que no se repitan en otro Shipment de la Orden, respuesta 39)
    res.checks += revisar_loads(loads, link_ship)
    if loads and otros is not None and not es_tonu(s) and \
            not any(anexo.es_naviera(a.get("carrier_nombre", "")) for a in s.get("asignaciones", [])):
        mios = {l["numero"] for l in loads if l.get("numero")}
        repetidas = []
        for o in otros:
            if o.get("numero") == s.get("numero") or es_tonu(o) or \
                    any(anexo.es_naviera(a.get("carrier_nombre", "")) for a in o.get("asignaciones", [])):
                continue
            for n in sorted(mios & set(o.get("loads_numeros") or [])):
                repetidas.append(f"{n} is also on {o['numero']}")
        add(Check(GRUPO_LOADS, "Loads are not repeated in another shipment", "fail" if repetidas else "ok",
                  "; ".join(repetidas) + "." if repetidas else "None of the loads is on another shipment of the order.",
                  "A load can only be on one shipment of the order, unless the other shipment is for the Naviera or "
                  "a TONU. Remove the repeated load." if repetidas else "", link_orden, "Open order"))

    # 4. Pago, montos y overrides de COD y COP (respuestas 23 a 32)
    res.checks += revisar_pago(s, link_ship, internacional)
    t = tipo_pago(s.get("pago_tipo"))
    if t in ("cod", "cop"):
        res.checks.append(revisar_override("pago", GRUPO_PAGO, f"{NOMBRE_PAGO[t]} always needs an approved override.",
                                           ctx))
    actual = dict(s, numero=s.get("numero"), _id=shipment_id)
    res.checks += revisar_montos(actual, otros, orden, otros_completos and otros is not None, link_ship, link_orden)

    # 5. Direcciones frente a la Orden
    res.checks += revisar_direcciones(s, orden, loads or [], internacional, ctx, link_ship)

    # 6. Truck y Special Instructions
    add(Check(GRUPO_TRUCK, "Truck type", "ok" if s.get("truck_type") else "fail", s.get("truck_type") or "Blank.",
              "" if s.get("truck_type") else "Select the truck type in Truck Specifications.", link_ship, "Open shipment"))
    permitidos = {"Shipment Owner": s.get("owner", ""), "Dispatcher": s.get("dispatcher", "")}
    if solicitante_origen == "manual" and solicitante:
        permitidos["requester"] = solicitante
    contados = [x for x in (otros or [s]) if not (es_cancelado(x) and not es_tonu(x))]
    una_sola = otros is not None and len(contados) <= 1
    si_checks = revisar_si(s, da if asignacion else None, link_ship, permitidos, anexo.contactos(s.get("supervisor", "")),
                           una_sola, zips_special_terms(orden.get("special_terms", "")))
    for c in si_checks:
        if c.titulo == "Truck type matches Special Instructions":
            c.grupo = GRUPO_TRUCK
    res.checks += si_checks

    # 7. Driver Assignment, Carrier, MOTUS y ruta: mismas reglas que la BCA
    if nota_da is not None:
        add(nota_da)
    base = _evaluar_bca_base(shipment_id, shipment, asignacion, da, carrier, archivos, motus_data, leer_pdf,
                             revisar_archivos=False, motus_error=motus_error)
    res.checks += base.checks
    res.motus, res.motus_error = base.motus, base.motus_error
    if asignacion is not None:
        tel = digitos((da or {}).get("telefono"))
        add(Check(GRUPO_DA, "Driver phone on the Driver Assignment", "ok" if len(tel) >= 10 else "fail",
                  (da or {}).get("telefono") or "Blank.",
                  "" if len(tel) >= 10 else "Add the driver's phone. It is required (the dispatcher's phone is optional).",
                  _link_da(asignacion["da_id"]), f"Open {asignacion['da_nombre']}"))

    # 8. Documentos del carrier, app de STT y External Dispatcher
    m = base.motus
    if carrier is not None and asignacion is not None:
        link_car = _link_carrier(carrier["_id"])
        estado_app, fila = driver_firmo_en_app(conductores, asignacion, da)
        app_driver = bool((da or {}).get("send_to_app"))
        if app_driver:
            casos.insert(0, "STT app driver")
        if s.get("dispatchers_externos"):
            casos.append("External dispatcher")
            add(Check(GRUPO_APP, "External Dispatcher Assignment", "manual",
                      "The shipment has an External Dispatcher Assignment.",
                      "BackOffice confirms the carrier appears in Carrier Management, or that the driver's profile was "
                      "added to the shipment's Chatter.", link_ship, "Open shipment"))

        # BCA: basta con que esté en los archivos del Carrier (respuestas 68, 69 y 80)
        if estado_app == "si":
            res.terminos_app = fila
            add(Check(GRUPO_DOCS, "Signed BCA", "ok",
                      f"{fila['nombre']} accepted the terms in the STT app (Terms Status: True). It counts as a signed BCA."))
        elif m is not None:
            mc_activo = bool(digitos(carrier.get("mc"))) and bool(
                (motus_property(m, digitos(carrier["mc"])) or {}).get("estado") == "Active")
            if archivos is None:
                add(Check(GRUPO_DOCS, "Signed BCA", "manual", "The carrier's files could not be read.",
                          "BackOffice checks the BCA.", link_car, "View carrier files"))
            else:
                chk, previa = revisar_bca_previa(archivos, leer_pdf, carrier, m, mc_activo)
                bca_en_ship = [d for d in s.get("sign_docs") or [] if "BCA" in d["archivo"].upper()]
                if previa is not None:
                    add(Check(GRUPO_DOCS, "Signed BCA", "ok", chk.detalle, "", chk.link, chk.link_texto))
                elif chk.estado == "manual":
                    add(Check(GRUPO_DOCS, "Signed BCA", "manual", chk.detalle, chk.solucion, chk.link, chk.link_texto))
                elif bca_en_ship and chk.titulo == "No previous BCA for this carrier":
                    add(Check(GRUPO_DOCS, "Signed BCA", "fail",
                              f"The signed BCA ({bca_en_ship[0]['archivo']}) is only in the shipment's Sign Documents.",
                              "Add the signed BCA to the carrier's files (Carrier Management Detail) before requesting "
                              "the LC.", link_car, "Open carrier"))
                else:
                    add(Check(GRUPO_DOCS, "Signed BCA", "fail", chk.detalle,
                              "The carrier needs a signed BCA with current MOTUS information in the carrier's files "
                              "before the LC. Request the BCA first (use the Verify BCA tab).",
                              chk.link or link_car, chk.link_texto or "Open carrier"))

        if m is not None:
            res.checks += revisar_coi(archivos, leer_pdf, m, s, loads or [], anexo, hoy,
                                      monto_cobertura_requerido(orden.get("special_terms", "")), ctx, link_car)
            add(Check(GRUPO_DOCS, "COI looks original and is signed", "manual",
                      "A suspicious format or a pasted signature needs an approved override.",
                      "BackOffice checks that the COI looks original and the signature is not pasted.",
                      link_car, "View carrier files"))
        res.checks += revisar_licencia(archivos, da, s, app_driver, link_car)

        # Caso 1: driver de la app (Send To App marcado, respuestas 75 y 76)
        if app_driver:
            if fila is None:
                add(Check(GRUPO_APP, "Driver listed in Driver Information", "fail",
                          "Send To App is checked, but the driver is not in the carrier's Driver Information.",
                          "Set up the driver in the STT app so it appears in Driver Information.", link_car, "Open carrier"))
            else:
                for etiqueta, clave, esperado in VALORES_APP:
                    valor = (fila.get(clave) or "").strip()
                    ok = valor.lower() == esperado
                    add(Check(GRUPO_APP, f"{etiqueta} is {esperado.title()}", "ok" if ok else "fail",
                              f"{etiqueta}: {valor or 'blank'}.",
                              "" if ok else f"{etiqueta} must be {esperado.title()} to work with an STT app driver.",
                              link_car, "View Driver Information"))

    # 9. Lo que BackOffice siempre revisa a mano
    if loads:
        add(Check(GRUPO_MANUAL, "Load description makes sense with its dimensions", "manual",
                  "Dimensions only need to be greater than 0.",
                  "BackOffice checks that the description and the dimensions make sense together.", link_ship,
                  "Open shipment"))
    res.caso = ", ".join(casos) if casos else "Standard"
    return res


def solicitante_lc(shipment: dict | None, manual: str) -> tuple[str, str]:
    """Quién pide la LC: (nombre, origen). El origen es manual, owner, owner+dispatcher o dispatcher."""
    s = shipment or {}
    owner, disp = (s.get("owner") or "").strip(), (s.get("dispatcher") or "").strip()
    if manual.strip():
        return manual.strip(), "manual"
    if owner and not disp:
        return owner, "owner"
    if owner:
        return owner, "owner+dispatcher"
    return disp, "dispatcher" if disp else ""


def elegir_asignaciones(asignaciones: list[dict], das: dict, link_ship: str) -> tuple[list[dict], Check | None]:
    """Respuestas 73 y 74: solo se le manda LC al Driver Assignment en Dispatched. Si hay más de uno en
    Dispatched o Interested, el broker debe confirmar con cuál trabaja."""
    estado = lambda a: (das.get(a["da_id"], {}).get("status") or a.get("status") or "").strip().lower()
    activos = [a for a in asignaciones if estado(a) in ("dispatched", "interested")]
    if len(activos) > 1:
        nombres = ", ".join(f"{a['da_nombre']} ({estado(a).title()})" for a in activos)
        return activos[:1], Check(GRUPO_DA, "Only one Driver Assignment is active", "fail",
                                  f"More than one driver is Dispatched or Interested: {nombres}.",
                                  "Confirm which driver you will work with. Only that Driver Assignment stays Dispatched; "
                                  "set the others to Canceled.", link_ship, "Open shipment")
    despachados = [a for a in asignaciones if estado(a) == "dispatched"]
    if despachados:
        return despachados, None
    vivos = [a for a in asignaciones if "cancel" not in estado(a)]
    return (vivos or asignaciones)[:1], None


def pre_verificar_lc(crm: CRM, shipment_id: int, motus_fn=motus_consultar, anexo: Anexo | None = None,
                     confidenciales=(), manual: str = "") -> list[ResultadoLC]:
    """Recorre el CRM y MOTUS para un Shipment y aplica las reglas de Load Confirmation."""
    anexo = anexo or Anexo()
    shipment = crm.shipment(shipment_id)
    if shipment is None:
        return [evaluar_lc(shipment_id, None, None, None, None, None, None, None)]
    link_ship = f"{CRM_URL}/Admin/Shipments/Details/{shipment_id}"

    def leer_pdf(guid):
        try:
            return texto_pdf(crm.descargar(guid))
        except Exception:
            return None

    try:
        loads = []
        for l in crm.loads_shipment(shipment_id, shipment["_token"])[:20]:
            detalle = crm.load(l["id"])
            detalle["_id"] = l["id"]
            detalle["numero"] = detalle["numero"] or l["numero"]
            loads.append(detalle)
    except Exception:
        loads = None

    # Orden, sus documentos y sus otros Shipments
    orden = docs_orden = otros = None
    error_orden = None
    completos = True
    if shipment.get("orden_id"):
        try:
            orden = crm.orden(shipment["orden_id"])
        except Exception as ex:
            error_orden = f"the order could not be read ({type(ex).__name__})"
        if orden is not None:
            try:
                docs_orden = crm.documentos_orden(orden.get("id") or shipment["orden_id"], orden["_token"])
            except Exception:
                docs_orden = None
            ids = []
            if orden.get("ver_todos"):
                try:
                    ids = crm.ids_shipments_orden(orden["ver_todos"])
                except Exception:
                    ids = []
            if not ids:
                ids = [x["id"] for x in orden.get("shipments_tarjeta", [])]
                completos = len(ids) < 3     # la tarjeta muestra como máximo 3
            otros = []
            for sid in ids[:15]:
                if sid == shipment_id:
                    otros.append(dict(shipment, _id=sid, loads_numeros=[l["numero"] for l in loads or []]))
                    continue
                try:
                    o = crm.shipment(sid)
                    if o is None:
                        completos = False
                        continue
                    try:
                        o["loads_numeros"] = [x["numero"] for x in crm.loads_shipment(sid, o["_token"])]
                    except Exception:
                        o["loads_numeros"] = []
                    o["_id"] = sid
                    otros.append(o)
                except Exception:
                    completos = False
            if len(ids) > 15:
                completos = False
            if all(o.get("_id") != shipment_id for o in otros):
                otros.insert(0, dict(shipment, _id=shipment_id, loads_numeros=[l["numero"] for l in loads or []]))
    else:
        error_orden = "the shipment is not linked to an order"

    # Overrides del Shipment, con su comentario y quién lo modificó
    overrides = []
    for r in shipment.get("requests", []):
        if "override" not in f"{r['tipo']} {r['documento']}".lower():
            continue
        o = {"id": r["id"], "num": r.get("num"), "respuesta": r["respuesta"], "comentario": "", "modifico": "",
             "razon": "", "leido": False}
        if r.get("num"):
            try:
                d = crm.request(r["num"])
                o.update({k: d[k] for k in ("comentario", "modifico", "razon", "leido")})
                o["respuesta"] = d.get("respuesta") or r["respuesta"]
            except Exception:
                pass
        overrides.append(o)

    solicitante, origen = solicitante_lc(shipment, manual)
    if not shipment["asignaciones"]:
        return [evaluar_lc(shipment_id, shipment, None, None, None, None, None, leer_pdf, None, loads,
                           orden=orden, otros=otros, otros_completos=completos, docs_orden=docs_orden,
                           overrides=overrides, anexo=anexo, confidenciales=confidenciales, solicitante=solicitante,
                           solicitante_origen=origen, error_orden=error_orden)]

    das = {a["da_id"]: crm.driver_assignment(a["da_id"]) for a in shipment["asignaciones"]}
    elegidas, nota = elegir_asignaciones(shipment["asignaciones"], das, link_ship)
    resultados = []
    for asig in elegidas:
        da = das[asig["da_id"]]
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
        resultados.append(evaluar_lc(shipment_id, shipment, asig, da, carrier, archivos, motus_data, leer_pdf,
                                     conductores, loads, motus_error, orden=orden, otros=otros,
                                     otros_completos=completos, docs_orden=docs_orden, overrides=overrides,
                                     anexo=anexo, confidenciales=confidenciales, solicitante=solicitante,
                                     solicitante_origen=origen, nota_da=nota, error_orden=error_orden))
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
