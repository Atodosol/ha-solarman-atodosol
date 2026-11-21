# Validación de Atodosol Instalaciones Fotovoltaicas

## 1.1.0: placa domótica Atodosol (ATS-DOM)

Contrato: API local del firmware (`local_web.h` y `local_portal.h`): `GET /api/state` y `POST /api/settings` en formulario. `POST /api/pin` no se usa.

### Pruebas automatizadas

Suite completa con Python 3.14 y Home Assistant 2026.9.2: 102 pruebas, todas correctas (55 anteriores y 47 de la placa). Las de la placa usan una placa simulada en localhost que aplica las reglas del firmware: cabecera Host, rechazo de Origin ajeno, formulario obligatorio, PIN con intentos y bloqueo, validación de campos, 409 sin regulación configurada, respuesta 202 pendiente y `Connection: close`. Su estado de partida es la respuesta real de la placa de laboratorio, anonimizada (`tests/fixtures/board_state_lab.json`).

- Lectura del estado real sin configurar, valores nulos, tipos incorrectos, instalación aislada y respuestas de otros equipos.
- Cuerpos exactos de cada cambio: modo, protección por SOC, mínimo, potencia contratada (texto que acepta el firmware) y PIN solo si la placa lo tiene.
- Peticiones en serie: nunca dos a la vez a la misma placa, aunque vengan de flujos y entidades distintos. Sin cabecera Origin.
- Lecturas con un reintento suave tras un tiempo de espera. Un cambio no se repite nunca: sin respuesta, se avisa de que puede haberse aplicado.
- Flujo de configuración: menú con la placa, entrada manual, errores (dirección no válida, sin conexión, Host rechazado, no es una placa), duplicados, descubrimiento por zeroconf con confirmación, anuncio de una dirección conocida sin peticiones, placa que cambia de IP (zeroconf y DHCP) y equipos que no son placas.
- Opciones: misma dirección sin petición, PIN no válido, otra placa en la nueva dirección rechazada e intervalo de lectura aplicado.
- Entidades de una placa configurada, de la placa sin configurar (controles no disponibles con el motivo), instalación aislada, «Solo solar» con y sin potencia de batería, alarma de cargador, placa equivocada en la dirección guardada y placa que no responde.
- Cambios: modo con lectura cada 5 s mientras está pendiente, protección desactivada y activada de nuevo con su mínimo, mínimo y potencia contratada.
- PIN: sin PIN guardado no se envía nada; PIN correcto; PIN incorrecto enviado una sola vez; bloqueo (423 y `locked_s`); PIN puesto en la placa después de la última lectura.
- Rechazos de la placa (409) con su texto, placa que no responde a un cambio y diagnóstico sin PIN, direcciones, MAC, red Wi-Fi ni números de serie. El PIN tampoco aparece en el registro, ni en modo depuración.
- Textos en español e inglés para todos los errores, estados y avisos.

### Prueba con la placa real (solo lectura)

Placa de laboratorio, firmware 0.5.193, sin configurar (`configured` y `writable` falsos), durante su prueba de firmware de 24 h. Solo se enviaron `GET /api/state`, con al menos 5 s entre peticiones; cualquier otro método o dirección hacía fallar la prueba antes de salir del equipo. La segunda placa de la red no recibió ninguna petición.

- mDNS: las placas se anuncian como `_http._tcp` con instancias `ats-dom` y `ats-dom-2`, puerto 80 y TXT `version=2026.2.4`. La instancia no coincide con el nombre: `ats-dom-2` es `atodosol.local`. Por eso la integración identifica cada placa por su `id`, nunca por el nombre.
- Home Assistant temporal: descubrimiento por zeroconf con los datos reales del anuncio, confirmación y alta con la IP; primer refresco correcto; dispositivo con firmware y MAC; entidades con los valores reales (batería, consumo, red y cargador sin datos, «Regulación» activa, «Ajustes» sin configurar con el aviso de la placa); los cuatro controles no disponibles; un cambio se rechaza sin enviar nada; un segundo anuncio no genera peticiones; diagnóstico sin datos privados.
- Por nombre: una lectura con el cliente de la integración a `atodosol.local` (cabecera Host `atodosol.local`) fue aceptada por la placa.

### Límites

- **No se ha enviado ningún cambio a una placa real.** Los cambios de ajustes, el PIN, el bloqueo, el 409 y el estado pendiente solo se han comprobado con la placa simulada, construida a partir del firmware. Hace falta probarlos en una placa configurada desde Miledy.
- La placa real estaba sin configurar: no se han visto datos reales de batería, consumo, red ni de un coche cargando.
- Estado del cargador: los valores 0, 1 y 2 (sin coche, conectado, cargando) son los del V2C; 3 a 5 se interpretan como los estados D, E y F de IEC 61851 (ventilación y error), sin verlos en una placa real.
- En las pruebas de Home Assistant, la resolución de `atodosol.local` con el resolvedor de Home Assistant falla por una limitación del entorno de pruebas (aiodns en otro bucle de eventos); en Home Assistant real se resuelve con mDNS. La lectura por nombre se comprobó con el cliente de la integración y el resolvedor del sistema.
- Con dos placas y direcciones por nombre, si los nombres se intercambian al reiniciar, la integración deja de mostrar datos hasta corregir la dirección; no lo corrige sola. Con la IP no ocurre.

## 1.0.0: inversores

Base: davidrapan/ha-solarman v25.08.16 (`e8db5da48a348794be86c5d9780a2468272b71cb`).

### Pruebas automatizadas

Suite con Python 3.14 y Home Assistant 2026.9.2:

- Respuestas Modbus fragmentadas, bloques de 125 registros y reutilización de conexión.
- Reconexión después de una respuesta incompleta.
- Una escritura con confirmación perdida no se repite automáticamente.
- Rechazo de longitud, identificador de transacción, protocolo y dirección incorrectos.
- Cancelación del cliente y del escaneo sin tareas o conexiones pendientes.
- Límites de red, concurrencia, deduplicación y selección de múltiples dispositivos.
- Rechazo de equipos que solo tienen abierto Modbus pero no identifican un inversor Deye.
- Flujos reales de configuración de Home Assistant: manual, automático, errores, ausencia de resultados y duplicados.
- Cambio de IP en opciones: se comprueba que sigue siendo el mismo inversor.
- Sustitución del inversor tras el mismo dongle: número de serie nuevo detectado en funcionamiento, al arrancar y desde opciones con confirmación; se conservan la entrada, el dispositivo y las entidades, sin identificadores duplicados ni conflicto con otra entrada.

### Prueba física y de interfaz

Equipo: Deye DYDA WiBLE 1.7.1 con Modbus TCP activado, inversor Deye monofásico de 10 kW y 3 MPPT.

La búsqueda sobre una LAN /24 encontró el inversor y descartó otros equipos. El recorrido en un navegador sobre un Home Assistant temporal permitió buscar, seleccionar y configurar el inversor sin introducir su IP. La integración completó el primer refresco y creó el dispositivo.

Todas las consultas al equipo físico fueron lecturas. Los tests que envían escrituras usan exclusivamente un servidor simulado en localhost.

### Límites

No se ha validado el control de equipos reales ni la escala de potencia bajo carga. La compatibilidad física se ha comprobado con un único modelo de dongle. La búsqueda se limita a IPv4 privada, puerto 502 y dirección Modbus 1; para otros casos existe configuración manual. No hay recuperación automática mediante barridos periódicos cuando cambia la IP.

Las versiones antiguas de Home Assistant y otros perfiles heredados necesitan validación específica. Las pruebas no demuestran disponibilidad indefinida de la red.
