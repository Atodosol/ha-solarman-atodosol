# Validación de Atodosol Solar 1.0.0

Base: davidrapan/ha-solarman v25.08.16 (`e8db5da48a348794be86c5d9780a2468272b71cb`).

## Pruebas automatizadas

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

## Prueba física y de interfaz

Equipo: Deye DYDA WiBLE 1.7.1 con Modbus TCP activado, inversor Deye monofásico de 10 kW y 3 MPPT.

La búsqueda sobre una LAN /24 encontró el inversor y descartó otros equipos. El recorrido en un navegador sobre un Home Assistant temporal permitió buscar, seleccionar y configurar el inversor sin introducir su IP. La integración completó el primer refresco y creó el dispositivo.

Todas las consultas al equipo físico fueron lecturas. Los tests que envían escrituras usan exclusivamente un servidor simulado en localhost.

## Límites

No se ha validado el control de equipos reales ni la escala de potencia bajo carga. La compatibilidad física se ha comprobado con un único modelo de dongle. La búsqueda se limita a IPv4 privada, puerto 502 y dirección Modbus 1; para otros casos existe configuración manual. No hay recuperación automática mediante barridos periódicos cuando cambia la IP.

Las versiones antiguas de Home Assistant y otros perfiles heredados necesitan validación específica. Las pruebas no demuestran disponibilidad indefinida de la red.
