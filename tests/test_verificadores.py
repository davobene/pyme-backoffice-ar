"""Pruebas de los correctores: la solución correcta saca 1, no hacer nada y los errores típicos sacan poco.

Corre sin HUD (solo la lógica de negocio): ``python -m pytest tests/test_verificadores.py``.
"""
from __future__ import annotations

import pytest

from pyme import datos as D
from pyme import tareas as T

SEMILLAS = range(1, 13)


def _nota(e, s, resp=""):
    return T.corregir(e, s, resp)["reward"]


@pytest.mark.parametrize("familia", list(T.FAMILIAS))
@pytest.mark.parametrize("semilla", SEMILLAS)
def test_oraculo_y_no_hacer_nada(familia, semilla):
    e = T.escenario(familia, semilla)
    s_ok, resp = T.oraculo(e)
    assert _nota(e, s_ok, resp) == 1.0
    assert _nota(e, e.sistema.clon(), "") <= 0.25
    assert _nota(e, e.sistema.clon(), "Listo, ya está hecho todo lo que pediste.") <= 0.25


@pytest.mark.parametrize("familia", list(T.FAMILIAS))
def test_determinismo(familia):
    a, b = T.escenario(familia, 5), T.escenario(familia, 5)
    assert a.prompt == b.prompt and a.info == b.info
    assert {k: p.stock for k, p in a.sistema.productos.items()} == {k: p.stock for k, p in b.sistema.productos.items()}


@pytest.mark.parametrize("familia", list(T.FAMILIAS))
def test_variedad_entre_semillas(familia):
    prompts = {T.escenario(familia, s).prompt for s in SEMILLAS}
    infos = {repr(T.escenario(familia, s).info) for s in SEMILLAS}
    assert len(infos) >= 0.75 * len(SEMILLAS)
    assert len(prompts) >= 3


# ---------------------------------------------------------------- errores típicos
@pytest.mark.parametrize("semilla", SEMILLAS)
def test_facturar_ignorando_politicas(semilla):
    e = T.escenario("facturar_pendientes", semilla)
    s = e.sistema.clon()
    for pid in e.info["ok"]:
        D.facturar_pedido(s, pid)
    # factura también al cliente con deuda vencida (el de stock lo rechaza el sistema)
    for pid, motivo in e.info["bloqueados"].items():
        if motivo == "deuda vencida":
            D.facturar_pedido(s, pid)
    assert _nota(e, s, "Facturé todo.") <= 0.6


@pytest.mark.parametrize("semilla", SEMILLAS)
def test_facturar_sin_descuento(semilla):
    e = T.escenario("facturar_pendientes", semilla)
    s = e.sistema.clon()
    hubo_descuento = False
    for pid in e.info["ok"]:
        ped = s.pedidos[pid]
        cli = s.clientes[ped.cliente_id]
        hubo_descuento |= cli.descuento_pct > 0 and any(s.productos[i.sku].categoria != "Ofertas" for i in ped.items)
        s.emitir_comprobante(D.tipo_factura(cli.condicion_iva), cli.id,
                             [{"sku": i.sku, "cantidad": i.cantidad} for i in ped.items], pedido_id=pid)
    if hubo_descuento:
        assert _nota(e, s, T.oraculo(e)[1]) < 0.9


@pytest.mark.parametrize("semilla", SEMILLAS)
def test_facturar_letra_equivocada(semilla):
    e = T.escenario("facturar_pendientes", semilla)
    s = e.sistema.clon()
    for pid in e.info["ok"]:
        ped = s.pedidos[pid]
        cli = s.clientes[ped.cliente_id]
        letra = "FB" if D.tipo_factura(cli.condicion_iva) == "FA" else "FA"
        s.emitir_comprobante(letra, cli.id, [{"sku": i.sku, "cantidad": i.cantidad,
                                              "precio_unitario": D.precio_con_descuento(s, cli, i.sku) / 100}
                                             for i in ped.items], pedido_id=pid)
    assert _nota(e, s, T.oraculo(e)[1]) <= 0.3


@pytest.mark.parametrize("semilla", SEMILLAS)
def test_imputar_lifo(semilla):
    e = T.escenario("imputar_transferencias", semilla)
    s = e.sistema.clon()
    for x in e.info["esperadas"]:
        c = s.registrar_cobranza(x["cliente_id"], x["fecha"], "transferencia", x["importe"] / 100, "x")
        libre = c.importe
        for f in reversed(D.facturas_impagas(s, c.cliente_id)):  # la más nueva primero
            if libre <= 0:
                break
            a = min(libre, s.saldo_comprobante(f.id))
            s.imputar(c.id, f.id, a / 100)
            libre -= a
    assert _nota(e, s, T.oraculo(e)[1]) <= 0.7


@pytest.mark.parametrize("semilla", SEMILLAS)
def test_imputar_registra_desconocida(semilla):
    e = T.escenario("imputar_transferencias", semilla)
    s, resp = T.oraculo(e)
    otro = next(iter(s.clientes))
    s.registrar_cobranza(otro, s.hoy.isoformat(), "transferencia", e.info["desconocida"]["importe"] / 100, "x")
    assert _nota(e, s, resp) < 0.9


@pytest.mark.parametrize("semilla", SEMILLAS)
def test_doble_nc_a_la_original(semilla):
    e = T.escenario("doble_factura", semilla)
    s = e.sistema.clon()
    o = s.comprobantes[e.info["original"]]
    s.emitir_comprobante("NC" + o.tipo[-1], o.cliente_id, [{"sku": i.sku, "cantidad": i.cantidad,
                                                             "precio_unitario": i.precio_unitario / 100} for i in o.items],
                         asociado_a=o.id)
    assert _nota(e, s, "Anulé la factura repetida.") <= 0.4


@pytest.mark.parametrize("semilla", SEMILLAS)
def test_doble_sin_corregir_stock(semilla):
    e = T.escenario("doble_factura", semilla)
    s = e.sistema.clon()
    d = s.comprobantes[e.info["duplicada"]]
    s.emitir_comprobante("NC" + d.tipo[-1], d.cliente_id, [{"sku": i.sku, "cantidad": i.cantidad,
                                                             "precio_unitario": i.precio_unitario / 100} for i in d.items],
                         asociado_a=d.id)
    assert 0.5 <= _nota(e, s, "NC emitida.") <= 0.75


@pytest.mark.parametrize("semilla", SEMILLAS)
def test_stock_ajuste_manual(semilla):
    e = T.escenario("stock_negativo", semilla)
    s = e.sistema.clon()
    p = s.productos[e.info["sku"]]
    s.registrar_movimiento(p.sku, "ajuste", -p.stock + 5, "Ajuste por diferencia")
    assert _nota(e, s, "Hice un ajuste.") <= 0.2


@pytest.mark.parametrize("semilla", SEMILLAS)
def test_stock_anula_movimiento_equivocado(semilla):
    e = T.escenario("stock_negativo", semilla)
    s = e.sistema.clon()
    otro = next(m for m in s.movimientos.values() if m.sku == e.info["sku"] and m.referencia == "Stock inicial")
    s.anular_movimiento(otro.id)
    assert _nota(e, s, "Estaba duplicado.") <= 0.2


@pytest.mark.parametrize("semilla", SEMILLAS)
def test_precios_redondeo_al_mas_cercano(semilla):
    e = T.escenario("lista_precios", semilla)
    s = e.sistema.clon()
    for p in s.productos.values():
        if p.categoria in e.info["cats"] and not p.precio_congelado:
            s.actualizar_precio(p.sku, round(p.precio * (100 + e.info["pct"]) / 100_000) * 10)
    assert _nota(e, s) < 0.75


@pytest.mark.parametrize("semilla", SEMILLAS)
def test_precios_toca_congelados(semilla):
    e = T.escenario("lista_precios", semilla)
    s = e.sistema.clon()
    for p in s.productos.values():
        if p.categoria in e.info["cats"]:
            s.actualizar_precio(p.sku, T._ceil10(p.precio, e.info["pct"]) / 100)
    assert _nota(e, s) <= 0.75


@pytest.mark.parametrize("semilla", SEMILLAS)
def test_devolucion_a_precio_de_lista(semilla):
    e = T.escenario("devolucion", semilla)
    s = e.sistema.clon()
    f = s.comprobantes[e.info["factura"]]
    nc = s.emitir_comprobante("NC" + f.tipo[-1], f.cliente_id, [{"sku": e.info["sku"], "cantidad": e.info["n"]}],
                              asociado_a=f.id)
    if e.info["buen_estado"]:
        s.registrar_movimiento(e.info["sku"], "ingreso", e.info["n"], nc.numero)
    if s.productos[e.info["sku"]].precio != e.info["pu"]:
        assert _nota(e, s) <= 0.4


@pytest.mark.parametrize("semilla", SEMILLAS)
def test_devolucion_stock_al_reves(semilla):
    e = T.escenario("devolucion", semilla)
    s = e.sistema.clon()
    f = s.comprobantes[e.info["factura"]]
    nc = s.emitir_comprobante("NC" + f.tipo[-1], f.cliente_id, [{"sku": e.info["sku"], "cantidad": e.info["n"],
                                                                 "precio_unitario": e.info["pu"] / 100}], asociado_a=f.id)
    if not e.info["buen_estado"]:
        s.registrar_movimiento(e.info["sku"], "ingreso", e.info["n"], nc.numero)
    assert _nota(e, s) <= 0.6


@pytest.mark.parametrize("semilla", SEMILLAS)
def test_credito_siempre_factura(semilla):
    e = T.escenario("decision_credito", semilla)
    s = e.sistema.clon()
    D.facturar_pedido(s, e.info["pedido"])
    esperado = 1.0 if e.info["variante"] == "ok" else 0.0
    assert _nota(e, s, "Facturado.") == esperado


@pytest.mark.parametrize("semilla", SEMILLAS)
def test_credito_rechaza_con_los_dos_motivos(semilla):
    e = T.escenario("decision_credito", semilla)
    s = e.sistema.clon()
    assert _nota(e, s, "No se factura: tiene deuda vencida y además supera el límite de crédito.") <= 0.25


@pytest.mark.parametrize("semilla", SEMILLAS)
def test_comisiones_sobre_total_con_iva(semilla):
    e = T.escenario("comisiones", semilla)
    from datetime import date
    s = e.sistema
    out = {v: 0.0 for v in s.comisiones_vendedor}
    for i in s.imputaciones:
        cob = s.cobranzas[i.cobranza_id]
        if date.fromisoformat(e.info["desde"]) <= cob.fecha <= date.fromisoformat(e.info["hasta"]):
            v = s.clientes[s.comprobantes[i.comprobante_id].cliente_id].vendedor
            out[v] += i.importe * s.comisiones_vendedor[v] / 100
    resp = "\n".join(f"{v}: {x / 100:.2f}" for v, x in out.items())
    assert _nota(e, s.clon(), resp) <= 0.34
