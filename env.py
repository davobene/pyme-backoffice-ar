"""Entorno HUD v6: back-office de una distribuidora mayorista argentina (en español).

El agente opera el sistema de gestión a través de herramientas MCP (no tiene SQL
ni acceso al estado interno). Cada tarea arma la empresa desde una semilla,
le pasa el pedido al agente y corrige mirando el estado final del sistema.
"""
from __future__ import annotations

import asyncio
import contextlib
import json
import socket
from datetime import date

from fastmcp import FastMCP
from hud.capabilities import Capability
from hud.environment import Environment
from hud.graders import EvaluationResult, SubScore

from pyme import tareas as T
from pyme.datos import POLITICAS
from pyme.sistema import CONDICIONES, ErrorSistema

env = Environment(name="pyme-backoffice-ar")
server = FastMCP(name="sistema-gestion")
ESTADO: dict = {"esc": None}
_task: asyncio.Task | None = None


def _s():
    if ESTADO["esc"] is None:
        raise ErrorSistema("el sistema no está inicializado")
    return ESTADO["esc"].sistema


def _m(c: int) -> float:
    return round(c / 100, 2)


def _out(x) -> str:
    return json.dumps(x, ensure_ascii=False, default=str)


def _seguro(f):
    """Convierte errores del sistema en un mensaje para el agente, sin cortar la sesión."""
    import functools

    @functools.wraps(f)
    async def w(*a, **k):
        try:
            return f(*a, **k)
        except ErrorSistema as e:
            return f"ERROR: {e}"
    return w


def _comp_dict(s, c, detalle=False) -> dict:
    d = {"id": c.id, "numero": c.numero, "tipo": c.tipo, "cliente_id": c.cliente_id, "fecha": c.fecha.isoformat(),
         "vencimiento": c.vencimiento.isoformat(), "neto": _m(c.neto), "iva": _m(c.iva), "total": _m(c.total)}
    if c.es_factura:
        d["saldo"] = _m(s.saldo_comprobante(c.id))
        d["pedido_id"] = c.pedido_id
    else:
        d["factura_asociada"] = s.comprobantes[c.asociado_a].numero
        d["motivo"] = c.motivo
    if detalle:
        d["items"] = [{"sku": i.sku, "cantidad": i.cantidad, "precio_unitario": _m(i.precio_unitario),
                       "alicuota_iva": i.alicuota, "neto": _m(i.neto)} for i in c.items]
    return d


# ------------------------------------------------------------------ herramientas de consulta
@server.tool
@_seguro
def fecha_de_hoy() -> str:
    """Fecha de trabajo del sistema."""
    return _s().hoy.isoformat()


@server.tool
@_seguro
def ver_manual() -> str:
    """Manual de procedimientos administrativos de la empresa."""
    return POLITICAS


@server.tool
@_seguro
def buscar_clientes(texto: str = "", zona: str = "", vendedor: str = "") -> str:
    """Lista clientes. Filtra por texto (razón social o CUIT), zona y/o vendedor."""
    s, t = _s(), T._norm(texto)
    r = [{"id": c.id, "razon_social": c.razon_social, "cuit": c.cuit, "condicion_iva": CONDICIONES[c.condicion_iva],
          "zona": c.zona, "vendedor": c.vendedor}
         for c in s.clientes.values()
         if (not t or t in T._norm(c.razon_social) or t.replace("-", "") in c.cuit.replace("-", ""))
         and (not zona or T._norm(zona) == T._norm(c.zona)) and (not vendedor or T._norm(vendedor) in T._norm(c.vendedor))]
    return _out(r)


@server.tool
@_seguro
def ver_cliente(cliente_id: int) -> str:
    """Ficha del cliente con condiciones comerciales y saldos."""
    s = _s()
    c = s._cliente(cliente_id)
    return _out({"id": c.id, "razon_social": c.razon_social, "cuit": c.cuit, "condicion_iva": CONDICIONES[c.condicion_iva],
                 "descuento_comercial_pct": c.descuento_pct, "limite_credito": _m(c.limite_credito), "dias_de_pago": c.dias_pago,
                 "zona": c.zona, "vendedor": c.vendedor, "saldo_deudor": _m(s.saldo_cliente(c.id)),
                 "deuda_vencida": _m(s.deuda_vencida(c.id)), "a_cuenta": _m(s.a_cuenta(c.id))})


@server.tool
@_seguro
def cuenta_corriente(cliente_id: int) -> str:
    """Comprobantes y cobranzas del cliente con su saldo."""
    s = _s()
    c = s._cliente(cliente_id)
    comps = sorted((x for x in s.comprobantes.values() if x.cliente_id == c.id), key=lambda x: (x.fecha, x.numero))
    cobs = [{"id": k.id, "fecha": k.fecha.isoformat(), "medio": k.medio, "importe": _m(k.importe), "referencia": k.referencia,
             "imputaciones": [{"comprobante": s.comprobantes[i.comprobante_id].numero, "importe": _m(i.importe)}
                              for i in s.imputaciones if i.cobranza_id == k.id]}
            for k in s.cobranzas.values() if k.cliente_id == c.id]
    return _out({"cliente": c.razon_social, "comprobantes": [_comp_dict(s, x) for x in comps], "cobranzas": cobs,
                 "saldo_deudor": _m(s.saldo_cliente(c.id)), "a_cuenta": _m(s.a_cuenta(c.id))})


@server.tool
@_seguro
def buscar_productos(texto: str = "", categoria: str = "") -> str:
    """Lista productos con precio de lista neto (sin IVA), alícuota, stock y si tienen precio congelado."""
    s, t = _s(), T._norm(texto)
    return _out([{"sku": p.sku, "descripcion": p.descripcion, "categoria": p.categoria, "precio_lista_neto": _m(p.precio),
                  "alicuota_iva": p.alicuota, "stock": p.stock, "precio_congelado": p.precio_congelado}
                 for p in s.productos.values()
                 if (not t or t in T._norm(p.descripcion) or t in p.sku.lower())
                 and (not categoria or T._norm(categoria) == T._norm(p.categoria))])


@server.tool
@_seguro
def movimientos_de_stock(sku: str) -> str:
    """Movimientos de stock de un producto (incluye anulados)."""
    s = _s()
    p = s._producto(sku)
    return _out([{"id": m.id, "fecha": m.fecha.isoformat(), "tipo": m.tipo, "cantidad": m.cantidad,
                  "referencia": m.referencia, "anulado": m.anulado}
                 for m in s.movimientos.values() if m.sku == p.sku])


@server.tool
@_seguro
def listar_pedidos(estado: str = "pendiente", cliente_id: int | None = None) -> str:
    """Pedidos por estado (pendiente, facturado, anulado o todos) y opcionalmente por cliente."""
    s = _s()
    return _out([{"id": p.id, "cliente_id": p.cliente_id, "cliente": s.clientes[p.cliente_id].razon_social,
                  "zona": s.clientes[p.cliente_id].zona, "fecha": p.fecha.isoformat(), "estado": p.estado,
                  "items": [{"sku": i.sku, "cantidad": i.cantidad} for i in p.items]}
                 for p in s.pedidos.values()
                 if (estado == "todos" or p.estado == estado) and (cliente_id is None or p.cliente_id == int(cliente_id))])


@server.tool
@_seguro
def listar_comprobantes(cliente_id: int | None = None, tipo: str = "", desde: str = "", hasta: str = "") -> str:
    """Comprobantes emitidos (FA, FB, NCA, NCB), filtrables por cliente, tipo y fechas AAAA-MM-DD."""
    s = _s()
    d = date.fromisoformat(desde) if desde else date.min
    h = date.fromisoformat(hasta) if hasta else date.max
    return _out([_comp_dict(s, c) for c in sorted(s.comprobantes.values(), key=lambda c: c.id)
                 if (cliente_id is None or c.cliente_id == int(cliente_id)) and (not tipo or c.tipo == tipo.upper())
                 and d <= c.fecha <= h])


@server.tool
@_seguro
def ver_comprobante(comprobante_id: int) -> str:
    """Detalle de un comprobante con sus ítems."""
    s = _s()
    return _out(_comp_dict(s, s._comprobante(comprobante_id), detalle=True))


@server.tool
@_seguro
def listar_cobranzas(desde: str = "", hasta: str = "", cliente_id: int | None = None) -> str:
    """Cobranzas registradas con sus imputaciones, filtrables por fecha (AAAA-MM-DD) y cliente."""
    s = _s()
    d = date.fromisoformat(desde) if desde else date.min
    h = date.fromisoformat(hasta) if hasta else date.max
    return _out([{"id": k.id, "cliente_id": k.cliente_id, "fecha": k.fecha.isoformat(), "medio": k.medio,
                  "importe": _m(k.importe), "referencia": k.referencia,
                  "imputaciones": [{"comprobante_id": i.comprobante_id, "comprobante": s.comprobantes[i.comprobante_id].numero,
                                    "importe": _m(i.importe)} for i in s.imputaciones if i.cobranza_id == k.id]}
                 for k in s.cobranzas.values() if d <= k.fecha <= h and (cliente_id is None or k.cliente_id == int(cliente_id))])


@server.tool
@_seguro
def extracto_bancario() -> str:
    """Movimientos recientes de la cuenta bancaria de la empresa (créditos positivos, débitos negativos)."""
    return _out([{"fecha": l.fecha.isoformat(), "concepto": l.concepto, "referencia": l.referencia, "importe": _m(l.importe)}
                 for l in _s().banco])


# ------------------------------------------------------------------ herramientas de escritura
@server.tool
@_seguro
def emitir_comprobante(tipo: str, cliente_id: int, items: list[dict], pedido_id: int | None = None,
                       factura_asociada_id: int | None = None, motivo: str = "") -> str:
    """Emite FA/FB/NCA/NCB. items: [{"sku", "cantidad", "precio_unitario"}] con precio unitario neto (sin IVA).
    Si se omite precio_unitario se usa el de lista. Para notas de crédito indicar factura_asociada_id."""
    s = _s()
    c = s.emitir_comprobante(tipo, cliente_id, items, pedido_id=pedido_id, asociado_a=factura_asociada_id, motivo=motivo)
    return _out({"ok": True, **_comp_dict(s, c, detalle=True)})


@server.tool
@_seguro
def registrar_cobranza(cliente_id: int, fecha: str, medio: str, importe: float, referencia: str = "") -> str:
    """Registra una cobranza (transferencia, efectivo o cheque). Queda a cuenta hasta imputarla."""
    c = _s().registrar_cobranza(cliente_id, fecha, medio, importe, referencia)
    return _out({"ok": True, "cobranza_id": c.id, "importe": _m(c.importe)})


@server.tool
@_seguro
def imputar_cobranza(cobranza_id: int, comprobante_id: int, importe: float) -> str:
    """Aplica parte o todo de una cobranza a una factura del mismo cliente."""
    s = _s()
    i = s.imputar(cobranza_id, comprobante_id, importe)
    return _out({"ok": True, "comprobante": s.comprobantes[i.comprobante_id].numero, "importe": _m(i.importe),
                 "saldo_restante_factura": _m(s.saldo_comprobante(i.comprobante_id))})


@server.tool
@_seguro
def registrar_movimiento_stock(sku: str, tipo: str, cantidad: int, referencia: str) -> str:
    """Movimiento manual de stock: ingreso (cantidad positiva), egreso (negativa) o ajuste."""
    m = _s().registrar_movimiento(sku, tipo, cantidad, referencia)
    return _out({"ok": True, "movimiento_id": m.id, "stock_actual": _s().productos[m.sku].stock})


@server.tool
@_seguro
def anular_movimiento_stock(movimiento_id: int) -> str:
    """Anula un movimiento de stock existente (revierte su efecto)."""
    m = _s().anular_movimiento(movimiento_id)
    return _out({"ok": True, "movimiento_id": m.id, "stock_actual": _s().productos[m.sku].stock})


@server.tool
@_seguro
def actualizar_precio(sku: str, nuevo_precio_neto: float) -> str:
    """Cambia el precio de lista neto (sin IVA) de un producto."""
    p = _s().actualizar_precio(sku, nuevo_precio_neto)
    return _out({"ok": True, "sku": p.sku, "precio_lista_neto": _m(p.precio)})


@server.tool
@_seguro
def entregar_informe(texto: str) -> str:
    """Registra la respuesta final para administración (lo encontrado, lo hecho y lo que quedó pendiente)."""
    _s()
    if not str(texto or "").strip():
        raise ErrorSistema("el informe está vacío")
    ESTADO["informe"] = str(texto)
    return "Informe recibido."


# ------------------------------------------------------------------ ciclo de vida
async def _servir(port: int) -> None:
    with contextlib.suppress(asyncio.CancelledError):
        await server.run_async(transport="http", host="127.0.0.1", port=port, show_banner=False,
                               log_level="warning")


@env.initialize
async def _iniciar() -> None:
    global _task
    if _task is None:
        so = socket.socket()
        so.bind(("", 0))
        port = so.getsockname()[1]
        so.close()
        _task = asyncio.create_task(_servir(port))
        await asyncio.sleep(0.3)
        env.add_capability(Capability.mcp(name="sistema", url=f"http://127.0.0.1:{port}/mcp"))


@env.shutdown
async def _cerrar() -> None:
    global _task
    if _task is not None:
        _task.cancel()
        with contextlib.suppress(Exception):
            await _task
        _task = None


def _evaluacion(res: dict) -> EvaluationResult:
    subs = [SubScore(name=x["name"], value=x["value"], weight=x["weight"], info=x.get("info")) for x in res["subscores"]]
    return EvaluationResult(reward=res["reward"], subscores=subs)


CIERRE = ("\n\nCuando termines, registrá tu respuesta final para administración con la herramienta "
          "entregar_informe.")


async def _correr(familia: str, semilla: int):
    esc = T.escenario(familia, semilla)
    ESTADO["esc"] = esc
    ESTADO["informe"] = None
    inicial = esc.sistema.clon()
    respuesta = yield esc.prompt + CIERRE
    # No todos los agentes devuelven su mensaje final al corrector: se usa el informe entregado
    # por herramienta y, si no hay, el mensaje final.
    texto = ESTADO["informe"] or (respuesta if isinstance(respuesta, str) else str(respuesta or ""))
    final = esc.sistema
    esc.sistema = inicial
    yield _evaluacion(T.corregir(esc, final, texto))


# ------------------------------------------------------------------ plantillas de tareas
@env.template(id="facturar_pendientes", description="Facturar pedidos de una zona respetando crédito y stock")
async def facturar_pendientes(semilla: int = 1):
    async for x in _correr("facturar_pendientes", semilla):
        yield x


@env.template(id="imputar_transferencias", description="Registrar transferencias del extracto e imputar FIFO")
async def imputar_transferencias(semilla: int = 1):
    async for x in _correr("imputar_transferencias", semilla):
        yield x


@env.template(id="doble_factura", description="Detectar una factura duplicada y corregirla con NC y stock")
async def doble_factura(semilla: int = 1):
    async for x in _correr("doble_factura", semilla):
        yield x


@env.template(id="stock_negativo", description="Encontrar la causa de un stock negativo y corregirla")
async def stock_negativo(semilla: int = 1):
    async for x in _correr("stock_negativo", semilla):
        yield x


@env.template(id="lista_precios", description="Aplicar un aumento de lista con redondeo y excepciones")
async def lista_precios(semilla: int = 1):
    async for x in _correr("lista_precios", semilla):
        yield x


@env.template(id="devolucion", description="Registrar una devolución de mercadería con NC y stock")
async def devolucion(semilla: int = 1):
    async for x in _correr("devolucion", semilla):
        yield x


@env.template(id="decision_credito", description="Decidir si un pedido se factura según la política de crédito")
async def decision_credito(semilla: int = 1):
    async for x in _correr("decision_credito", semilla):
        yield x


@env.template(id="comisiones", description="Calcular comisiones de vendedores sobre lo cobrado (solo lectura)")
async def comisiones(semilla: int = 1):
    async for x in _correr("comisiones", semilla):
        yield x
