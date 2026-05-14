# 💡 Features Pendientes — saldos.py

Lista de ideas para implementar en el futuro.

---

## 🤖 Sistema auto-reparable ante cambios de los bancos
Cuando un banco cambia su página web (como pasó con Banco de Chile en feb 2026),
el sistema debería detectarlo y corregirse solo, sin que el usuario tenga que avisar.

**Flujo ideal:**
1. Banco falla → el script ya guarda screenshot + HTML automáticamente
2. El script llama a la API de Claude con ese HTML + el selector que falló
3. Claude analiza qué cambió en la estructura de la página
4. Claude sugiere el selector nuevo y lo aplica al código
5. El banco se reintenta automáticamente con el fix

**Requiere:** API key de Anthropic guardada en Bitwarden

**Nota:** Banco de Chile cambió su login en feb 2026 — este feature hubiera detectado eso automáticamente.

---

## 🏦 Bancos nuevos por agregar
- Falabella
- BCI
- BBVA
- Banco Security
- Actinver

---
