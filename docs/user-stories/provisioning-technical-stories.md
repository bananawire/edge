# Historias de Usuario - Provisioning

Esta sección presenta las Épicas y las Historias Técnicas (Technical Stories) identificadas para el bounded context de aprovisionamiento (`provisioning`). Cada historia está redactada desde la perspectiva de un rol de la plataforma (las Historias Técnicas usan al Desarrollador para habilitar capacidades técnicas) e incluye criterios de aceptación detallados mediante escenarios con el formato Dado que / Cuando / Entonces.

## Épicas

| Epic / Story ID | Título | Descripción | Criterios de Aceptación | Relacionado con (Epic ID) |
| --------------- | ------ | ----------- | ----------------------- | ------------------------- |
| ES-EP-02 | Gestión de Aprovisionamiento de Dispositivos | Como Usuario, quiero gestionar la sincronización y el aprovisionamiento de dispositivos en tiempo real desde el sistema central, para que el caché de dispositivos locales esté actualizado y permita la autenticación correcta en el edge. | La Épica se completa cuando todas las historias de usuario relacionadas han sido implementadas, validadas y aceptadas. | - |

## Historias Técnicas (Technical Stories)

| Epic / Story ID | Título | Descripción | Criterios de Aceptación | Relacionado con (Epic ID) |
| --------------- | ------ | ----------- | ----------------------- | ------------------------- |
| ES-US-03 | Sincronizar Evento de Aprovisionamiento de Dispositivos | Como Desarrollador, quiero que el sistema consuma y procese eventos de cambio de dispositivos desde un endpoint HTTP, para que la información del dispositivo esté disponible para mis aplicaciones en el caché local. | **Escenario: Procesar roster de aprovisionamiento con datos válidos**<br>Dado que el `DeviceRosterPoller` está activo y consulta GET "/api/v1/edge/devices?since=" en clair-core<br>Cuando se recibe una página con los campos device_id, hardware_id, api_key y status válidos<br>Entonces el sistema normaliza el payload al esquema local<br>Y actualiza o inserta el registro del dispositivo en la tabla de caché local "devices" con los valores correspondientes.<br><br>**Escenario: Rechazar procesamiento del roster por campos obligatorios faltantes**<br>Dado que el `DeviceRosterPoller` está activo y consulta el roster HTTP<br>Cuando se recibe un registro que carece de alguno de los campos requeridos (device_id, hardware_id, api_key o status)<br>Entonces el sistema lanza un error de validación (ValueError)<br>Y descarta el mensaje sin guardar cambios en la base de datos local.<br><br>**Escenario: Soportar formatos camelCase y snake_case en el roster**<br>Dado que el `DeviceRosterPoller` está activo y consulta el roster HTTP<br>Cuando se recibe un registro en formato camelCase (deviceId, hardwareId, apiKey) o snake_case (device_id, hardware_id, api_key)<br>Entonces el sistema normaliza las claves correctamente al formato local<br>Y realiza el registro en la base de datos local de manera exitosa. | ES-EP-02 |
