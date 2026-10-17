"""Prueba de punta a punta: un agente guionado opera SOLO a través de las herramientas MCP
y el corrector le pone 1.0. Requiere el SDK de HUD (Linux): ``pytest tests/test_mcp.py``."""
from __future__ import annotations

import asyncio
import json

import pytest
from fastmcp import Client

import env as E


async def _tool(c, nombre, **kw):
    r = await c.call_tool(nombre, kw)
    txt = r.content[0].text
    assert not txt.startswith("ERROR"), txt
    return json.loads(txt) if txt[:1] in "[{" else txt


async def _episodio(familia, semilla, agente):
    gen = E._correr(familia, semilla)
    prompt = await gen.__anext__()
    async with Client(E.server) as c:
        respuesta = await agente(c, prompt)
    res = await gen.asend(respuesta)
    return res


async def _agente_stock(c, prompt):
    prods = await _tool(c, "buscar_productos")
    neg = next(p for p in prods if p["stock"] < 0)
    movs = await _tool(c, "movimientos_de_stock", sku=neg["sku"])
    vistos = {}
    for m in movs:
        k = (m["tipo"], m["cantidad"], m["referencia"])
        if k in vistos and not m["anulado"]:
            await _tool(c, "anular_movimiento_stock", movimiento_id=m["id"])
            return f"El egreso {m['referencia']} estaba duplicado; anulé el repetido."
        vistos[k] = m
    return "no encontré"


async def _agente_devolucion(c, prompt):
    import re
    numero = re.search(r"F[AB] 0003-\d{8}", prompt).group(0)
    comps = await _tool(c, "listar_comprobantes")
    f = next(x for x in comps if x["numero"] == numero)
    det = await _tool(c, "ver_comprobante", comprobante_id=f["id"])
    prods = await _tool(c, "buscar_productos")
    desc = re.search(r'"([^"]+)"', prompt).group(1)
    sku = next(p["sku"] for p in prods if p["descripcion"] == desc)
    n = int(re.search(r"devolvió (\d+) unidades", prompt).group(1))
    item = next(i for i in det["items"] if i["sku"] == sku)
    nc = await _tool(c, "emitir_comprobante", tipo="NC" + det["tipo"][-1], cliente_id=det["cliente_id"],
                     items=[{"sku": sku, "cantidad": n, "precio_unitario": item["precio_unitario"]}],
                     factura_asociada_id=det["id"], motivo="Devolución")
    if "vino bien" in prompt:
        await _tool(c, "registrar_movimiento_stock", sku=sku, tipo="ingreso", cantidad=n, referencia=nc["numero"])
    return f"Emití {nc['numero']}."


async def _agente_precios(c, prompt):
    import math
    import re
    pct = int(re.search(r"(\d+)%", prompt).group(1))
    cats = [x for x in ("Almacén", "Bebidas", "Limpieza", "Perfumería") if x in prompt]
    for p in await _tool(c, "buscar_productos"):
        if p["categoria"] in cats and not p["precio_congelado"]:
            nuevo = math.ceil(round(p["precio_lista_neto"] * 100) * (100 + pct) / 100_000) * 10
            await _tool(c, "actualizar_precio", sku=p["sku"], nuevo_precio_neto=nuevo)
    return "Listo."


@pytest.mark.parametrize("semilla", [1, 2, 3])
@pytest.mark.parametrize("familia,agente", [("stock_negativo", _agente_stock), ("devolucion", _agente_devolucion),
                                            ("lista_precios", _agente_precios)])
def test_punta_a_punta(familia, agente, semilla):
    res = asyncio.run(_episodio(familia, semilla, agente))
    assert res.reward == 1.0, res


def test_error_del_sistema_no_corta_la_sesion():
    async def agente(c, prompt):
        r = await c.call_tool("ver_cliente", {"cliente_id": 9999})
        assert r.content[0].text.startswith("ERROR")
        return ""
    res = asyncio.run(_episodio("stock_negativo", 1, agente))
    assert res.reward == 0.0


def test_plantillas_registradas():
    ids = set(E.env.templates)
    assert ids == {"facturar_pendientes", "imputar_transferencias", "doble_factura", "stock_negativo", "lista_precios",
                   "devolucion", "decision_credito", "comisiones"}
