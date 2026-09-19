# Atodosol Solar

Integración local de Home Assistant para inversores Deye y equipos compatibles con los perfiles de Solarman. Basada en [davidrapan/ha-solarman](https://github.com/davidrapan/ha-solarman), versión v25.08.16, con licencia MIT y atribución conservada.

## Qué añade Atodosol

- **Buscar en mi red**: detecta la red de Home Assistant y busca inversores Deye por Modbus TCP. Muestra el número de serie y la IP para elegir el equipo.
- **Introducir IP manualmente**: alternativa disponible desde el inicio y cuando la búsqueda no encuentra dispositivos.
- Comprobación de una lectura real antes de guardar la configuración.
- Recepción de tramas Modbus TCP fragmentadas, reconexión con esperas progresivas y cancelación limpia. No repite escrituras si se pierde su confirmación.
- Configuración en español e inglés, identificación por número de serie y protección contra duplicados.
- La detección y la conexión no modifican la configuración de red del dongle.

## Instalación con HACS

1. En HACS, abre **Repositorios personalizados** y añade `https://github.com/Atodosol/ha-solarman-atodosol` como **Integración**.
2. Descarga **Atodosol Solar** y reinicia Home Assistant.
3. Ve a **Ajustes → Dispositivos y servicios → Añadir integración → Atodosol Solar**.
4. Elige **Buscar en mi red** o **Introducir IP manualmente**.

También puedes extraer el contenido de `atodosol_solarman.zip` en `/config/custom_components/atodosol_solarman/` y reiniciar.

El dominio de esta integración es `atodosol_solarman`, distinto de `solarman`. No reemplaza automáticamente otra integración ni migra sus entidades o históricos. Si cambias desde Solarman, deshabilita su entrada para ese inversor y revisa tus paneles y automatizaciones.

## Deye DYDA WiBLE

El firmware y la configuración del dongle deben ofrecer **Modbus TCP**. En el equipo probado se utiliza DYDA WiBLE 1.7.1 con Link2 activado en ese modo. Esta integración no actualiza el firmware ni activa Link2.

| Ajuste | Valor habitual |
|---|---|
| Protocolo | Modbus TCP |
| Puerto | 502 |
| Dirección Modbus | 1 |
| Perfil | Auto |

La búsqueda automática hace únicamente una lectura FC03 de identificación en el puerto 502, dirección Modbus 1. Valida el tipo y el número de serie del inversor: un puerto abierto no basta. Identifica **inversores compatibles con Deye**, no prueba por sí sola que el adaptador sea un DYDA.

Para otros puertos, direcciones Modbus o protocolos, utiliza la entrada manual. Los perfiles heredados se conservan, pero la búsqueda nueva está orientada a Deye Modbus TCP. Selecciona **Solarman V5 (TCP)** y el puerto correspondiente para loggers V5.

## Identificación del equipo

Se muestra el número de serie real del inversor y su firmware. Cuando los registros no proporcionan el modelo comercial completo, se muestran los datos disponibles. En los perfiles de familia `SG0*`, el asterisco se sustituye por la potencia detectada en kW: por ejemplo, `SG10LP1` para 10 kW. Esta es una etiqueta derivada del perfil, no una referencia comercial SUN confirmada. El campo opcional **Modelo exacto** permite conservar íntegra la referencia de la etiqueta (SUN-…). No se ocultan caracteres ni se inventa una referencia a partir de la potencia.

## Alcance de la búsqueda

Se ejecuta desde Home Assistant, bajo demanda. Solo acepta redes IPv4 privadas, con un máximo de 1024 direcciones, 24 consultas simultáneas y 45 segundos por búsqueda. En redes grandes propone el tramo /24 de la interfaz; puedes indicar otro tramo de hasta /22. No busca por Internet ni usa credenciales del dongle.

Las VLAN, el aislamiento Wi-Fi y Docker sin acceso a la LAN pueden impedir la detección. No encontrar un inversor no demuestra que esté desconectado: prueba la IP manual o la red correcta. No se utiliza Bluetooth. Un cambio posterior de IP se puede corregir en las opciones; esta versión no hace barridos periódicos para relocalizar equipos.

## Validación y límites

Consulta [VALIDATION.md](docs/VALIDATION.md). La monitorización se ha comprobado con un DYDA y un inversor Deye monofásico de 10 kW. Las potencias con carga real y las órdenes de control requieren validación específica del equipo. Los controles y servicios de escritura heredados siguen disponibles; las pruebas físicas de esta versión usan únicamente lecturas.

## Desarrollo

```sh
python -m venv .venv
.venv/bin/pip install -r requirements_test.txt
.venv/bin/python -m pytest
.venv/bin/python scripts/build_release.py
```

Las pruebas usan Home Assistant 2026.9.2 y Python 3.14. Los tests de transporte levantan servidores Modbus simulados en localhost; no consultan equipos reales.

## Origen y licencia

Copyright © 2024 David Rapan y colaboradores. Modificaciones Atodosol Solar © 2026. Se conserva la [licencia MIT](LICENSE), los perfiles y el [README de origen](docs/UPSTREAM_README.md). Atodosol Solar es una distribución independiente; no es software oficial de Deye ni de Home Assistant.

Los componentes incluidos conservan sus licencias: [pysolarman (MIT)](custom_components/atodosol_solarman/pysolarman/license) y [umodbus (MPL 2.0)](custom_components/atodosol_solarman/pysolarman/umodbus/license). Estos avisos también se incluyen en la descarga para HACS.
