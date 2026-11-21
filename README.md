# Atodosol Instalaciones Fotovoltaicas

Integración local de Home Assistant para inversores Deye y equipos compatibles con los perfiles de Solarman, y para la **placa domótica Atodosol**, que regula la carga del coche. Basada en [davidrapan/ha-solarman](https://github.com/davidrapan/ha-solarman), versión v25.08.16, con licencia MIT y atribución conservada.

## Funciones

- **Buscar en mi red**: detecta la red de Home Assistant y busca inversores Deye por Modbus TCP. Muestra el número de serie y la IP para elegir el equipo.
- **Introducir IP manualmente**: alternativa disponible desde el inicio y cuando la búsqueda no encuentra dispositivos.
- Comprobación de una lectura real antes de guardar la configuración.
- Recepción de tramas Modbus TCP fragmentadas, reconexión con esperas progresivas y cancelación limpia. No repite escrituras si se pierde su confirmación.
- Configuración en español e inglés, identificación por número de serie y protección contra duplicados.
- La detección y la conexión no modifican la configuración de red del dongle.
- **Placa domótica Atodosol**: la casa en directo y el control de la carga del coche, como en la app de clientes. Consulta [su apartado](#placa-domótica-atodosol).

## Instalación con HACS

[![Añadir a Home Assistant](docs/assets/add-to-home-assistant.svg)](https://my.home-assistant.io/redirect/hacs_repository/?owner=Atodosol&repository=ha-solarman-atodosol&category=integration)

Pulsa **Añadir a Home Assistant** para abrir el repositorio en tu HACS. Necesitas tener HACS instalado. Descarga la integración y reinicia Home Assistant; después, [abre su configuración](https://my.home-assistant.io/redirect/config_flow_start/?domain=atodosol_solarman).

También puedes seguir estos pasos manualmente:

1. En HACS, abre **Repositorios personalizados** y añade `https://github.com/Atodosol/ha-solarman-atodosol` como **Integración**.
2. Descarga **Atodosol Instalaciones Fotovoltaicas** y reinicia Home Assistant.
3. Ve a **Ajustes → Dispositivos y servicios → Añadir integración → Atodosol Instalaciones Fotovoltaicas**.
4. Para un inversor, elige **Inversor: buscar en mi red** o **Inversor: introducir IP manualmente**. Para la placa, elige **Placa domótica Atodosol**.

También puedes extraer el contenido de `atodosol_solarman.zip` en `/config/custom_components/atodosol_solarman/` y reiniciar.

El dominio de esta integración es `atodosol_solarman`, distinto de `solarman`. No reemplaza automáticamente otra integración ni migra sus entidades o históricos. Si cambias desde Solarman, deshabilita su entrada para ese inversor y revisa tus paneles y automatizaciones.

## Deye DYDA WiBLE

El dongle debe tener **Modbus TCP habilitado**. Esta integración no actualiza el firmware ni activa Link2.

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

### Sustitución del inversor

Si se cambia el inversor y se mantienen el dongle y su dirección, no hace falta borrar la integración. Cuando el inversor informa de un número de serie distinto (al arrancar o en la lectura de identificación, cada hora en los perfiles Deye), la entrada se recarga, vuelve a detectar el perfil Auto y pasa el dispositivo al número de serie nuevo. Se conservan el dispositivo, las entidades y su historial. En **Opciones**, la misma dirección con otro número de serie pide confirmar la sustitución; una dirección distinta con otro inversor se sigue tratando como un equipo diferente. El modelo exacto, un perfil manual y las opciones adicionales se mantienen: revísalos si describían el inversor anterior.

## Alcance de la búsqueda

Se ejecuta desde Home Assistant, bajo demanda. Solo acepta redes IPv4 privadas, con un máximo de 1024 direcciones, 24 consultas simultáneas y 45 segundos por búsqueda. En redes grandes propone el tramo /24 de la interfaz; puedes indicar otro tramo de hasta /22. No busca por Internet ni usa credenciales del dongle.

Las VLAN, el aislamiento Wi-Fi y Docker sin acceso a la LAN pueden impedir la detección. No encontrar un inversor no demuestra que esté desconectado: prueba la IP manual o la red correcta. No se utiliza Bluetooth. Un cambio posterior de IP se puede corregir en las opciones; esta versión no hace barridos periódicos para relocalizar equipos.

## Placa domótica Atodosol

La placa domótica Atodosol (ATS-DOM) regula la carga del coche con la energía de tu instalación. Con esta integración ves en Home Assistant la batería, el consumo de la casa, la red y el cargador, y puedes cambiar los mismos ajustes de carga que en la app de clientes. Todo funciona en tu red local, sin la nube.

### Añadir la placa

- **Sola**: si la placa está en la misma red, Home Assistant la encuentra y muestra **Placa domótica Atodosol encontrada**. Pulsa **Añadir**. Se guarda su IP, y si la placa cambia de IP, Home Assistant la sigue.
- **A mano**: **Añadir integración → Atodosol Instalaciones Fotovoltaicas → Placa domótica Atodosol** y escribe `atodosol.local` o su IP. Si en casa hay dos placas, la segunda se llama `atodosol-2.local`.

Antes de guardar, Home Assistant lee la placa para comprobar que responde. Cada placa se reconoce por su identificador, no por la dirección, así que la misma placa no se añade dos veces.

Con dos placas, los nombres `atodosol.local` y `atodosol-2.local` pueden intercambiarse al reiniciarlas. Si pasa, Home Assistant lo detecta, deja de mostrar datos y no toca la otra placa hasta que corrijas la dirección en las opciones. Para evitarlo, usa la IP (mejor con una IP fija reservada en el router) o añádelas cuando Home Assistant las encuentre.

### Opciones

- **Dirección de la placa**: solo se acepta si en la nueva dirección responde la misma placa.
- **PIN de la placa**: déjalo vacío si la placa no tiene PIN. La placa no deja comprobarlo sin cambiar un ajuste, así que se comprueba la primera vez que cambias algo. Si no es correcto, Home Assistant no lo vuelve a enviar hasta que lo corrijas, porque cinco PIN incorrectos bloquean los cambios durante 5 minutos. El PIN se pone, se cambia o se quita en la página de la placa, no desde Home Assistant.
- **Lectura cada**: 15 segundos por defecto, entre 10 y 300. La placa atiende una conexión cada vez; Home Assistant nunca le envía dos peticiones a la vez.

### Qué ves y qué puedes cambiar

| Entidad | Qué es |
|---|---|
| Batería | Carga de la batería, en %. |
| Consumo de la casa | Potencia que consume la casa, en W. |
| Potencia de la red | En W: positiva cuando compras a la red, negativa cuando viertes. No aparece en instalaciones aisladas. |
| Potencia del cargador | Potencia que está cargando el coche, en W. |
| Estado del cargador | Sin coche, coche conectado, cargando, cargando con ventilación o error del cargador. |
| Límite del cargador | Corriente máxima que la placa deja al cargador, en A. |
| Regulación | Qué está haciendo la placa: regulación activa, esperando sol que sobre, detenida por batería baja… |
| Ajustes | Si puedes cambiar los ajustes desde Home Assistant y, si no, por qué. |
| Cargador bloqueado, Cambio pendiente, Alarma del cargador | Avisos del cargador. **Cambio pendiente** está encendido mientras la placa confirma un cambio con el cargador. |
| Modo de carga | **Carga máxima** o **Solo solar**. |
| Detener la carga por SOC mínimo | En **Carga máxima**, para el coche cuando la batería baja del mínimo. |
| SOC mínimo | Nivel de batería en el que se para el coche, de 1 a 95 %. |
| Potencia contratada | La potencia que tienes contratada, en kW. No aparece en instalaciones aisladas. |

**Antigüedad de los datos del cargador** está desactivada por defecto; puedes activarla para diagnóstico.

- Mientras Atodosol no haya configurado la regulación de tu placa, los controles aparecen como no disponibles y **Ajustes** explica por qué.
- **Solo solar** solo se puede elegir si el inversor informa de la potencia de la batería. En **Solo solar** la placa protege la batería por su cuenta, así que **Detener la carga por SOC mínimo** y **SOC mínimo** no están disponibles; vuelven al elegir **Carga máxima**, con el mínimo que tenías.
- Si la placa tiene un modo de carga que esta versión no conoce (una versión posterior del firmware puede añadir otro), **Modo de carga** aparece como desconocido. Cambiar el SOC mínimo, la protección o la potencia contratada no lo toca: el modo solo se envía cuando lo eliges tú.
- Cada cambio se envía una sola vez. Si la placa no responde a tiempo, Home Assistant no lo repite, porque podría aplicarse dos veces: lo verás en la siguiente lectura. Mientras hay un cambio pendiente, Home Assistant lee la placa cada 5 segundos.
- Si la placa rechaza un cambio, Home Assistant muestra el motivo que da la placa.

## Validación y límites

Consulta [VALIDATION.md](docs/VALIDATION.md). La placa domótica se ha comprobado solo con lecturas de una placa real; los cambios de ajustes se han probado contra una placa simulada. La monitorización del inversor se ha comprobado con un DYDA y un inversor Deye monofásico de 10 kW. Las potencias con carga real y las órdenes de control requieren validación específica del equipo. Los controles y servicios de escritura heredados siguen disponibles; las pruebas físicas de esta versión usan únicamente lecturas.

## Desarrollo

```sh
python -m venv .venv
.venv/bin/pip install -r requirements_test.txt
.venv/bin/python -m pytest
.venv/bin/python scripts/build_release.py
```

Las pruebas usan Home Assistant 2026.9.2 y Python 3.14. Los tests de transporte levantan servidores Modbus simulados en localhost, y los de la placa, una placa simulada con las reglas de su firmware; no consultan equipos reales.

## Origen y licencia

Copyright © 2024 David Rapan y colaboradores. Modificaciones Atodosol Instalaciones Fotovoltaicas © 2026. Se conserva la [licencia MIT](LICENSE), los perfiles y el [README de origen](docs/UPSTREAM_README.md). Atodosol Instalaciones Fotovoltaicas es una distribución independiente; no es software oficial de Deye ni de Home Assistant.

Los componentes incluidos conservan sus licencias: [pysolarman (MIT)](custom_components/atodosol_solarman/pysolarman/license) y [umodbus (MPL 2.0)](custom_components/atodosol_solarman/pysolarman/umodbus/license). Estos avisos también se incluyen en la descarga para HACS.
