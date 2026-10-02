# Bitácora de la app BackOffice de STT

La app está en inglés para los brokers y dispatchers; esta bitácora queda en español para el equipo que la mantiene. Los mensajes de error se citan en inglés, tal como aparecen en la app.

Aquí queda documentado todo lo que falló, lo que descubrimos y cómo se resolvió. La idea es que, cuando algo no funcione, se pueda descartar rápido lo que ya sabemos, sin investigar desde cero ni suponer.

La bitácora tiene dos archivos:

- **README.md** (este): diagnóstico rápido, de dónde sale cada dato y cómo registrar algo nuevo.
- **[REGISTRO.md](REGISTRO.md)**: historial de versiones y todas las entradas (B-001, B-002…), de la más reciente a la más antigua.

## Cuando algo falla

1. Mirá la **versión** en el pie de página de la app y comparala con la última del [REGISTRO](REGISTRO.md#versiones). Si no coinciden, Streamlit todavía no tomó los cambios: hacé **Manage app → ⋮ → Reboot app**.
2. Buscá el síntoma en la tabla de abajo.
3. Si no aparece, abrí **Manage app** en Streamlit Cloud, descargá los logs y registrá una entrada nueva con la plantilla del final.

## Diagnóstico rápido

| Síntoma | Qué revisar primero | Entrada |
|---|---|---|
| `ModuleNotFoundError` al abrir la app | Que exista `requirements.txt` en minúscula, en la raíz del repo, con todas las librerías. En los logs debe aparecer la instalación de cada una. | B-007 |
| Botones y pestañas sin los colores de STT | Que `config.toml` esté dentro de la carpeta `.streamlit` (con punto). | B-015 |
| No aparece el logo o sale error con `stt_logo.png` | Que `stt_logo.png` y `stt_icon.png` estén dentro de la carpeta `assets`. | B-015 |
| "STT_EMAIL and STT_PASSWORD are missing from the secrets" | Secrets de la app en Streamlit Cloud. | B-009 |
| "The CRM rejected the username or password" | Si cambió la contraseña de la cuenta, actualizá el secret. Probá entrar manualmente al CRM con esa cuenta. | B-009 |
| "The CRM sign-in form was not found" | El CRM cambió su pantalla de login. Guardá el HTML de `sttcrm.com/login` y revisalo. | B-006 |
| "Shipment S-xxxxxx does not exist in the CRM" | Abrí `sttcrm.com/Admin/Shipments/Details/<número>` en el navegador. El número va sin "S-" ni ceros a la izquierda. | B-006 |
| Todos los DOT salen "does not exist in MOTUS" o "Could not connect" | Abrí `https://motus.dot.gov/api/carriers/3692487` en el navegador. Si no devuelve texto con `"entityId"`, MOTUS cambió su servicio. | B-003 |
| La dirección sale en rojo pero se ve igual | Compará letra por letra: abreviaturas (LANE/LN, AVE/AVENUE), APT/STE, ZIP. Si de verdad son idénticas, es un formato del CRM que la app no reconoce. | B-010 |
| La app compara contra el P O BOX y no contra la dirección física | Identificadores de tipo de dirección de MOTUS. | B-005 |
| "The carrier's files section could not be read" | La consulta interna `CarrierManagementPictureList` del CRM. Guardá un HAR del Carrier y revisalo. | B-006, B-014 |
| "Previous BCA could not be read" | Normal si el PDF es una imagen escaneada. BackOffice la revisa a mano. | B-014 |
| "Validate code" dice "The code log is not set up" | Secrets `SUPABASE_URL` y `SUPABASE_KEY`, y que la tabla exista (`supabase.sql`). | B-009 |
| "The verification could not be saved to the log" | Que la key sea la **service_role** y que se haya corrido la versión más reciente de `supabase.sql` (la 2.2.0 agregó columnas). | B-009, B-016 |
| "Requested by" dice "Not identified" | En el CRM el Shipment no tiene Shipment Owner ni Dispatcher Id. El broker puede escribir su nombre en "Requested by (optional)". | B-016 |
| La banda sale en un color que no corresponde | Revisar `UMBRAL_ROJO` y `REQUISITOS_BCA` en `stt_core.py`. | B-017 |
| La hora no coincide con Guatemala | La app usa `America/Guatemala`. Revisar `ZONA` en `app.py`. | B-018 |
| Las pestañas se cortan en un teléfono | Google Fonts no cargó; la app usa la letra del sistema, que es más ancha. | B-013 |
| Errores 401 en DevTools al abrir MOTUS | Normal: son datos privados de MOTUS. No afectan a la app. | B-004 |

## De dónde sale cada dato

| Dato | Fuente | Dónde está en el código |
|---|---|---|
| Driver Assignments y link al Carrier | Página del Shipment, bloque "Drivers Assignments" (`order-DriverAssignment`) | `stt_core.parse_shipment` |
| Estado de origen y destino | Página del Shipment, Route Information: `BillingAddress_*` = origen, `ShippingAddress_*` = destino | `stt_core.parse_shipment` |
| Status y email del Driver Assignment | `/Admin/DriverAssignment/Details/<id>` | `stt_core.parse_driver_assignment` |
| Company Name, MC, DOT y Address | `/Admin/Carrier/Details/<id>` | `stt_core.parse_carrier` |
| Archivos del Carrier (BCAs previas) | POST `/Admin/CarrierManagement/CarrierManagementPictureList?DriverId=<id>` | `stt_core.CRM.archivos_carrier` |
| Descarga de un archivo | `/Admin/Download/DownloadFile?downloadGuid=<guid>` | `stt_core.CRM.descargar` |
| USDOT, Legal Name, dirección y MC | `https://motus.dot.gov/api/carriers/<DOT>` | `stt_core.motus_resumen` |
| Reglas de la BCA | Procedimiento de BackOffice (ver B-008) | `stt_core.evaluar_bca` |
| Códigos y estadísticas | Tabla `prechecks` en Supabase | `stt_core.Registro` |

## Secrets que necesita la app

| Secret | Para qué |
|---|---|
| `STT_EMAIL`, `STT_PASSWORD` | Entrar al CRM (por ahora con la cuenta de Sofía, ver B-009). |
| `SUPABASE_URL`, `SUPABASE_KEY` | Guardar verificaciones, validar códigos y mostrar estadísticas. Opcionales. |

## Cómo registrar algo nuevo

Agregá la entrada **arriba de todo** en la sección Entradas de [REGISTRO.md](REGISTRO.md), con el número siguiente. Si cambiaste código, subí la versión en `app.py` (`VERSION = ...`) y anotala en la tabla de versiones. Si el síntoma puede repetirse, agregalo también a la tabla de diagnóstico rápido.

```markdown
### B-0XX · Título corto de lo que pasó
- **Fecha:** AAAA-MM-DD
- **Tipo:** Falla | Hallazgo | Decisión
- **Estado:** Resuelto | Pendiente | Vigente
- **Versión:** x.y.z
- **Síntoma:** Qué se vio, con el mensaje exacto y el Shipment o DOT de ejemplo.
- **Causa:** Por qué pasó. Si no se sabe todavía, escribí "En investigación" y lo que ya se descartó.
- **Solución:** Qué se cambió y en qué archivo.
- **Cómo verificar:** Qué hacer para confirmar que quedó resuelto.
```
