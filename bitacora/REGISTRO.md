# Registro

Historial de versiones y entradas de la bitácora. La guía de uso y el diagnóstico rápido están en el [README](README.md).

## Versiones

| Versión | Fecha | Cambios | Entradas |
|---|---|---|---|
| 2.2.0 | 2026-10-02 | Toda la app en inglés de EE. UU. con fechas en hora de Guatemala. Banda de cumplimiento de políticas (verde, amarillo y rojo). Shipment Owner, Dispatcher y "Requested by" leídos del CRM. Nuevas columnas en Supabase. | B-016, B-017, B-018 |
| 2.1.0 | 2026-10-02 | Diseño 100 % responsivo (teléfono, tablet, computadora y TV). Pestañas renombradas: Verificar BCA, Validar código, Consulta USDOT. Versión visible en el pie de página. Se crea esta bitácora. | B-012, B-013, B-015 |
| 2.0.1 | 2026-10-02 | Corrige la comparación de direcciones con formato `CIUDAD, ST ZIP`. El campo de nombre pasa a "Broker o dispatcher que pide la BCA". | B-010, B-011 |
| 2.0.0 | 2026-10-02 | Pre-verificación de BCA con las reglas de BackOffice, diseño con la imagen de STT, código de pre-verificación, registro en Supabase y estadísticas. | B-008, B-009 |
| 1.1.0 | 2026-09-30 | Pestaña CRM frente a MOTUS: la app entra al CRM y compara el Carrier del Shipment con MOTUS. | B-006, B-007 |
| 1.0.0 | 2026-09-30 | Validador de USDOT contra MOTUS (uno o varios DOT). | B-001 a B-005 |

## Entradas

### B-018 · App en inglés y fechas en hora de Guatemala
- **Fecha:** 2026-10-02
- **Tipo:** Decisión
- **Estado:** Vigente
- **Versión:** 2.2.0
- **Síntoma:** La app la usan brokers y dispatchers que trabajan en inglés.
- **Causa:** Pedido de BackOffice.
- **Solución:** Todo lo visible está en inglés de EE. UU. (pestañas: Verify BCA, Validate code, USDOT lookup). Las fechas se muestran como `Oct 2, 2026, 3:22 PM (Guatemala time)` con la zona `America/Guatemala` (UTC−6, sin horario de verano), sin importar dónde esté el servidor o el usuario. El código interno y la bitácora siguen en español. Los motivos guardados en Supabase antes de esta versión quedaron en español; desde la 2.2.0 se guardan en inglés.
- **Cómo verificar:** El pie de página dice "Version 2.2.0" y la hora de "Verified" coincide con la hora de Guatemala.

### B-017 · Banda de cumplimiento de políticas
- **Fecha:** 2026-10-02
- **Tipo:** Decisión
- **Estado:** Vigente
- **Versión:** 2.2.0
- **Síntoma:** BackOffice quiere que la app le hable directamente a quien pide el documento, con un mensaje según qué tanto cumple.
- **Causa:** Pedido de Sofía para presentar a gerencia.
- **Solución:** Entre el veredicto y los datos del Shipment aparece una banda. Se calcula en `stt_core.nivel_cumplimiento`, que es genérica y sirve para cualquier documento futuro:
  - **Verde (Fully compliant):** cumple todos los requisitos. Agradece por seguir las políticas.
  - **Amarillo (Partially compliant):** cumple más del 25 % pero no todo. Explica que faltan datos según las políticas y SOPs.
  - **Rojo (Not compliant):** cumple el 25 % o menos. Recuerda que los requisitos son obligatorios y que las solicitudes incompletas se devuelven y retrasan la carga.
  - Los requisitos que no se pudieron revisar porque faltaba algo antes (por ejemplo, sin driver asignado) cuentan como no cumplidos. Para la BCA la base es 11 requisitos sin MC y 13 con MC.
  - Que ya exista una BCA vigente no cuenta en contra del broker: la banda queda verde y le agradece por verificar antes de pedir.
  - El umbral del rojo está en `UMBRAL_ROJO` (0.25) dentro de `stt_core.py`.
- **Cómo verificar:** Un Shipment sin driver asignado debe salir en rojo; uno con 1 a 3 datos faltantes, en amarillo.

### B-016 · Quién pide la solicitud: Shipment Owner y Dispatcher
- **Fecha:** 2026-10-02
- **Tipo:** Decisión
- **Estado:** Vigente
- **Versión:** 2.2.0
- **Síntoma:** BackOffice necesita ver quién pidió el documento. En el CRM, el que pide suele ser el Shipment Owner o el Dispatcher, y muy de vez en cuando otra persona con acceso.
- **Causa:** En la página del Shipment, los campos son `OrderOwner` (Shipment Owner) y `DispatcherId` (Dispatcher Id). El Dispatcher Id a menudo está vacío.
- **Solución:** La app lee los dos campos y los muestra. El campo "Requested by (optional)" solo se llena cuando el que pide no es ninguno de los dos. "Requested by" se registra así: el nombre escrito a mano; si no hay, el Shipment Owner; si tampoco, el Dispatcher. La banda de políticas saluda a quien escribió su nombre o, si no, al Owner y al Dispatcher. Se agregaron las columnas `shipment_owner`, `dispatcher` y `cumplimiento` a `supabase.sql`.
- **Cómo verificar:** Con S-039981, "Shipment Owner" debe decir Carlos Zavala. Si la tabla de Supabase ya existía, hay que volver a correr `supabase.sql` para agregar las columnas nuevas; si no, aparece "The verification could not be saved to the log".

### B-015 · Estructura de archivos del repo
- **Fecha:** 2026-10-02
- **Tipo:** Hallazgo
- **Estado:** Vigente
- **Versión:** 2.0.0
- **Síntoma:** Al subir los archivos uno por uno no quedaba claro qué va en la raíz y qué en carpetas.
- **Causa:** El zip de entrega trae una carpeta `backoffice/` que no debe existir en el repo.
- **Solución:** Así debe quedar el repo:
  ```
  raíz del repo
  ├── app.py
  ├── stt_core.py
  ├── requirements.txt
  ├── supabase.sql
  ├── assets/
  │   ├── stt_logo.png
  │   └── stt_icon.png
  ├── .streamlit/
  │   └── config.toml
  └── bitacora/
      ├── README.md
      └── REGISTRO.md
  ```
  En GitHub, una carpeta se crea con **Add file → Create new file** escribiendo el nombre con barra, por ejemplo `.streamlit/config.toml`.
- **Cómo verificar:** La app muestra el logo y los botones en rojo STT.

### B-014 · Lectura de BCAs previas sin probar contra el CRM real
- **Fecha:** 2026-10-02
- **Tipo:** Hallazgo
- **Estado:** Pendiente
- **Versión:** 2.0.0
- **Síntoma:** La lista de archivos del Carrier y el contenido de los PDF de BCA se probaron solo con copias guardadas de las páginas y con PDFs de prueba, no contra el CRM en vivo.
- **Causa:** Desde el entorno de desarrollo no hay acceso al CRM.
- **Solución:** Pendiente de confirmar con un Shipment cuyo Carrier tenga una BCA firmada, por ejemplo S-039245 (VELARDE TRUCKING LLC, archivo `BCA_Signature_22244__21404.pdf`). Si el PDF es una imagen escaneada, la app no puede leerlo y lo marca en amarillo para revisión manual; eso es lo esperado.
- **Cómo verificar:** Verificar S-039245. El grupo "BCA existente" debe decir "Ya existe una BCA vigente", "BCA previa desactualizada" o "BCA previa sin poder leer". Si dice "No se pudo leer la sección de archivos del Carrier", hay que revisar la consulta interna (B-006).

### B-013 · Las pestañas pueden desplazarse en teléfonos si no carga Google Fonts
- **Fecha:** 2026-10-02
- **Tipo:** Hallazgo
- **Estado:** Vigente
- **Versión:** 2.1.0
- **Síntoma:** En teléfonos de 320 a 375 px, la pestaña "Consulta USDOT" puede quedar parcialmente fuera y aparece una flecha para desplazar.
- **Causa:** Solo pasa cuando Google Fonts no carga y el navegador usa su letra del sistema, que es más ancha que Barlow Condensed. Con Barlow las tres pestañas caben desde 320 px.
- **Solución:** No requiere cambio. La página nunca se desborda; solo la barra de pestañas se desplaza.
- **Cómo verificar:** Con conexión normal, las tres pestañas se ven completas en el teléfono.

### B-012 · La app no era responsiva en teléfonos, tablets ni TV
- **Fecha:** 2026-10-02
- **Tipo:** Falla
- **Estado:** Resuelto
- **Versión:** 2.1.0
- **Síntoma:** En teléfonos la tercera pestaña quedaba oculta y palabras como "TRUCKING" se cortaban a la mitad. En tablet el panel "CRM frente a MOTUS" quedaba muy angosto. En TV 4K todo se veía diminuto.
- **Causa:** Faltaban reglas CSS para esos tamaños. Además, **Streamlit 1.64 cambió las pestañas**: los selectores `[data-baseweb="tab"]` ya no existen y ahora son `[role="tab"]` y `[role="tablist"]`. Por eso el estilo de las pestañas no se aplicaba en ningún tamaño.
- **Solución:** En `app.py`, bloque "Responsivo" del CSS:
  - Hasta 1024 px, el checklist y la comparación se apilan y la comparación va primero.
  - Hasta 900 px, la franja de datos pasa a 2 columnas; hasta 480 px, a 1 columna, y el encabezado se apila.
  - Hasta 640 px se ajustan el veredicto, las acciones y las pestañas.
  - Desde 1800 px el contenido se agranda proporcionalmente (`zoom` 1.2, 1.55 y 2 a partir de 1800, 2300 y 3000 px).
  - Las palabras largas solo se cortan si no caben en la línea.
- **Cómo verificar:** Probado sin desborde horizontal en 320, 375, 414, 768, 1024, 1366, 1920, 2560 y 3840 px. Si Streamlit vuelve a cambiar sus componentes, revisar primero los selectores de pestañas.

### B-011 · El campo "Tu nombre" era ambiguo
- **Fecha:** 2026-10-02
- **Tipo:** Falla
- **Estado:** Resuelto
- **Versión:** 2.0.1
- **Síntoma:** No quedaba claro si iba el nombre de quien usa la app, de BackOffice o del broker.
- **Causa:** Etiqueta poco específica.
- **Solución:** La etiqueta pasa a "Broker o dispatcher que pide la BCA", con el ejemplo "Tu nombre y apellido". Es el nombre que BackOffice ve al validar el código.
- **Cómo verificar:** La etiqueta aparece en la pestaña Verificar BCA.

### B-010 · Dirección marcada como distinta cuando era igual (S-039981)
- **Fecha:** 2026-10-02
- **Tipo:** Falla
- **Estado:** Resuelto
- **Versión:** 2.0.1
- **Síntoma:** En S-039981 (GOREMOTE TRANSPORT LLC, DOT 4409578) la app dijo "Todavía no se puede enviar" por la Principal Place of Business, aunque CRM y MOTUS mostraban lo mismo: `8212 NE 13TH AVE APT C7, VANCOUVER, WA 98665`.
- **Causa:** El CRM no guarda las direcciones siempre igual. VELARDE estaba como `CALLE, CIUDAD, ESTADO, ZIP, US`, separada en partes por comas; GOREMOTE está como `CALLE, CIUDAD, ESTADO ZIP`, sin "US". La app solo entendía el primer formato.
- **Solución:** En `stt_core.py`, `partir_direccion_crm` entiende los dos formatos y el ZIP+4, y la comparación se centralizó en `direccion_igual`. La regla de BackOffice se mantiene: no importan mayúsculas, comas ni el "US" final, pero una abreviatura distinta sí es diferencia.
- **Cómo verificar:** Probado con 9 casos: los formatos válidos dan igual; `LANE` frente a `LN`, `AVE` frente a `AVENUE` y una ciudad distinta dan diferente. S-039981 debe salir autorizado en la dirección.

### B-009 · Acceso al CRM y registro de verificaciones
- **Fecha:** 2026-10-02
- **Tipo:** Decisión
- **Estado:** Vigente
- **Versión:** 2.0.0
- **Síntoma:** La app la van a usar brokers y dispatchers, pero entra al CRM con una sola cuenta.
- **Causa:** Es una prueba para presentar a gerencia, y por ahora no se involucra a TI.
- **Solución:**
  - Por ahora la app usa la cuenta de Sofía (secrets `STT_EMAIL` y `STT_PASSWORD`), con una sola sesión compartida. **Antes de pasar a uso real, pedirle a TI un usuario de solo lectura para la app.** Mientras tanto, cada consulta queda en el CRM como hecha por Sofía.
  - Cuando la verificación sale autorizada, la app genera un código (por ejemplo `BCA-39981-K7QM`). La propuesta es que BackOffice solo acepte solicitudes que traigan un código válido.
  - Cada verificación se guarda en la tabla `prechecks` de Supabase (`supabase.sql`), que alimenta las estadísticas de la pestaña Validar código.
  - El 2026-10-02 se confirmó el login real al CRM y la consulta a MOTUS con S-039981.
- **Cómo verificar:** La pestaña Validar código encuentra un código recién generado.

### B-008 · Reglas para autorizar una BCA
- **Fecha:** 2026-10-02
- **Tipo:** Decisión
- **Estado:** Vigente
- **Versión:** 2.0.0
- **Síntoma:** Hacía falta convertir el procedimiento "Verifying Information Before Sending a BCA" en reglas automáticas.
- **Causa:** Definido por Sofía Morataya, supervisora de BackOffice.
- **Solución:** Reglas aplicadas en `stt_core.evaluar_bca`:
  1. Hay un driver en Drivers Assignments.
  2. El Driver Assignment está en Dispatched.
  3. El Driver Assignment tiene email.
  4. El Carrier tiene Company Name, DOT y Address. El DOT es obligatorio; el MC es opcional.
  5. El USDOT está activo en MOTUS y sin Out of Service.
  6. El Company Name es igual al Legal Name de MOTUS. Si solo coincide con el DBA, se rechaza.
  7. La dirección es **exactamente** la Principal Place of Business de MOTUS. No se aceptan abreviaturas distintas ni la dirección de correo.
  8. Si el Carrier tiene MC, debe existir en MOTUS como Motor Carrier of Property. Si está activo, se permite mover carga entre estados.
  9. **Mismo estado:** solo cuando el Carrier no tiene MC o el MC está INACTIVE en MOTUS. En ese caso, el estado de origen debe ser igual al de destino.
  10. **BCA previa:** se busca en los archivos del Carrier. Si hay una firmada que coincide con MOTUS (nombre, DOT, dirección y MC), la respuesta es "No enviar, ya existe". Si no coincide, se permite pedir una nueva.
  11. El Certificate Expiration Date **no** aplica para la BCA.
- **Cómo verificar:** Escenarios probados: dirección LN frente a LANE, sin MC con ruta CA a AZ, MC inactivo en el mismo estado y entre estados, Driver Assignment sin email o sin Dispatched, BCA vigente, desactualizada e ilegible, y DOT inexistente.

### B-007 · ModuleNotFoundError: bs4 en Streamlit Cloud
- **Fecha:** 2026-09-30
- **Tipo:** Falla
- **Estado:** Resuelto
- **Versión:** 1.1.0
- **Síntoma:** `ModuleNotFoundError: No module named 'bs4'` al abrir la app.
- **Causa:** El archivo se llamaba `Requirements.txt`, con R mayúscula. Streamlit Cloud corre en Linux, que distingue mayúsculas, y solo reconoce `requirements.txt`. En los logs solo aparecían los paquetes de Streamlit; `requests` y `pandas` venían incluidos con Streamlit, pero `beautifulsoup4` no.
- **Solución:** Renombrar el archivo a `requirements.txt` y hacer Reboot app.
- **Cómo verificar:** En los logs aparece la instalación de `beautifulsoup4` y `pypdf`.

### B-006 · Cómo está armado el CRM de STT
- **Fecha:** 2026-09-30
- **Tipo:** Hallazgo
- **Estado:** Vigente
- **Versión:** 1.1.0
- **Síntoma:** Hacía falta saber de dónde leer cada dato del CRM.
- **Causa:** El CRM (`sttcrm.com`) está hecho sobre nopCommerce (ASP.NET).
- **Solución:** Lo que se sabe:
  - El login es email y contraseña, sin captcha ni verificación en dos pasos.
  - El ID del Shipment es el número sin "S-" ni ceros: S-039245 es `/Admin/Shipments/Details/39245`.
  - La página del Shipment trae los Driver Assignments con el link al Carrier, así que no hace falta abrir la Load.
  - En Route Information, `BillingAddress_*` es el origen y `ShippingAddress_*` el destino.
  - Los archivos del Carrier no vienen en el HTML: se cargan con POST a `/Admin/CarrierManagement/CarrierManagementPictureList?DriverId=<carrierId>`, con el token `__RequestVerificationToken` de la página. Devuelve `Data` con `TypeName`, `Filename`, `CreatedOn` y `DownloadGuid`.
  - Las BCAs viejas pueden estar subidas con tipo "Other" y nombre `BCA_Signature_<shipment>__<id>.pdf`.
  - Plantillas de documentos que tiene el CRM: BCA, Load Confirmation (BB, CC, COD, COP, JL, OTR) y TONU.
- **Cómo verificar:** Si el CRM cambia, guardar las páginas con Ctrl+S o un HAR y compararlas con esto.

### B-005 · MOTUS devuelve varias direcciones por carrier
- **Fecha:** 2026-09-30
- **Tipo:** Hallazgo
- **Estado:** Vigente
- **Versión:** 1.0.0
- **Síntoma:** VELARDE TRUCKING LLC (DOT 2499310) tenía en MOTUS la dirección física y un `P O BOX 1696`.
- **Causa:** MOTUS separa los tipos con `addressTypeId`:
  - `eef9bd53-0da3-4b96-b462-8e2711a009ef` = Principal Place of Business
  - `34878d0c-cf18-46ce-a23e-60bfcaf558db` = Mailing Address
- **Solución:** La app usa solo la Principal Place of Business. Si MOTUS cambiara esos identificadores, usa la dirección que no sea de correo.
- **Cómo verificar:** Con DOT 2499310 la app muestra `395 GARCIA LANE, San Luis, AZ 85349`.

### B-004 · Errores 401 en DevTools al abrir MOTUS
- **Fecha:** 2026-09-30
- **Tipo:** Hallazgo
- **Estado:** Vigente
- **Versión:** 1.0.0
- **Síntoma:** En la pestaña Network de MOTUS aparecen en rojo `system-properties`, `Users/me`, `phmsa`, `applications` y `entity-certifications`, con error 401.
- **Causa:** Son datos privados que exigen iniciar sesión en MOTUS.
- **Solución:** Ninguna. La app no los usa.
- **Cómo verificar:** No aplica.

### B-003 · Fuente principal: servicio interno de MOTUS
- **Fecha:** 2026-09-30
- **Tipo:** Hallazgo
- **Estado:** Vigente
- **Versión:** 1.0.0
- **Síntoma:** MOTUS es una app JavaScript; su HTML viene vacío y no se puede leer directamente.
- **Causa:** La página pide los datos a `https://motus.dot.gov/api/carriers/<DOT>`, que responde JSON público sin iniciar sesión, en tiempo real.
- **Solución:** La app consulta esa dirección. **No es una API oficial documentada**: si FMCSA la cambia, hay que volver a buscarla con DevTools (pestaña Network, filtro Fetch/XHR) mientras se busca un DOT en MOTUS.
- **Cómo verificar:** Abrir `https://motus.dot.gov/api/carriers/3692487` en el navegador debe mostrar texto que empieza con `{"entityId"`.

### B-002 · SAFER descartado por datos atrasados
- **Fecha:** 2026-09-30
- **Tipo:** Hallazgo
- **Estado:** Vigente
- **Versión:** 1.0.0
- **Síntoma:** SAFER Company Snapshot funciona sin registro, pero el 2026-09-30 mostraba datos al 07/11/2026.
- **Causa:** SAFER se actualiza con varios meses de atraso.
- **Solución:** No se usa. Un carrier revocado recientemente seguiría apareciendo como autorizado.
- **Cómo verificar:** No aplica.

### B-001 · API oficial de FMCSA descartada
- **Fecha:** 2026-09-30
- **Tipo:** Decisión
- **Estado:** Vigente
- **Versión:** 1.0.0
- **Síntoma:** La API oficial (QCMobile, `mobile.fmcsa.dot.gov/QCDevsite`) exige registrarse para obtener un webKey, y desde Guatemala el sitio solo abre con VPN.
- **Causa:** El uso de VPN está prohibido.
- **Solución:** No se usa. Se reemplazó por el servicio interno de MOTUS (B-003).
- **Cómo verificar:** No aplica.
