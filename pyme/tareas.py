"""Familias de tareas: escenario + oráculo + corrector.

Cada familia arma un escenario a partir de una semilla, sabe resolverlo (oráculo)
y lo corrige comparando el estado final del agente con el estado esperado.
El corrector no mira cómo llegó el agente: mira el resultado en el sistema y
descuenta por cambios colaterales.
"""
from __future__ import annotations

import math
import random
import re
import unicodedata
from dataclasses import dataclass, field
from datetime import date, timedelta

from . import datos as D
from .sistema import ItemPedido, LineaBanco, Pedido, Sistema, pesos


@dataclass
class Escenario:
    familia: str
    semilla: int
    sistema: Sistema
    prompt: str
    info: dict = field(default_factory=dict)


@dataclass
class Puntaje:
    nombre: str
    valor: float
    peso: float
    detalle: str = ""


# ------------------------------------------------------------------ utilidades
def _renglones_con(texto: str, clave: str) -> str:
    """Texto normalizado de las oraciones/renglones que mencionan ``clave`` como número suelto."""
    trozos = re.split(r"[\n;]|(?<=\.)\s", texto or "")
    return _norm(" ".join(t for t in trozos if re.search(rf"(?<!\d){re.escape(clave)}(?!\d)", t)))


def _norm(t: str) -> str:
    t = unicodedata.normalize("NFKD", t or "").encode("ascii", "ignore").decode().lower()
    return re.sub(r"\s+", " ", t)


def _clave_comp(s: Sistema, c) -> tuple:
    asoc = s.comprobantes[c.asociado_a].numero if c.asociado_a else None
    return (c.tipo, c.cliente_id, c.pedido_id, asoc, tuple(sorted((i.sku, i.cantidad, i.precio_unitario) for i in c.items)))


def _nuevos(s0: Sistema, s: Sistema, coleccion: str) -> list:
    viejos = set(getattr(s0, coleccion))
    return [v for k, v in getattr(s, coleccion).items() if k not in viejos]


def _imputaciones_nuevas(s0: Sistema, s: Sistema):
    return s.imputaciones[len(s0.imputaciones):]


def colaterales(s0: Sistema, sa: Sistema, se: Sistema) -> tuple[int, list[str]]:
    """Cuenta cambios del agente que el estado esperado no tiene."""
    malos = []
    esp = [_clave_comp(se, c) for c in _nuevos(s0, se, "comprobantes")]
    for c in _nuevos(s0, sa, "comprobantes"):
        k = _clave_comp(sa, c)
        if k in esp:
            esp.remove(k)
        else:
            malos.append(f"comprobante no esperado {c.numero}")
    for sku, p in sa.productos.items():
        if p.precio != se.productos[sku].precio:
            malos.append(f"precio de {sku}")
    esp_mov = [(m.sku, m.tipo, m.cantidad, m.referencia) for m in _nuevos(s0, se, "movimientos") if m.tipo != "egreso"]
    for m in _nuevos(s0, sa, "movimientos"):
        if m.tipo == "egreso":
            continue  # los egresos los genera el sistema al facturar
        k = (m.sku, m.tipo, m.cantidad, m.referencia)
        if k in esp_mov:
            esp_mov.remove(k)
        else:
            malos.append(f"movimiento manual {m.tipo} {m.sku} {m.cantidad}")
    anul_esp = {k for k, m in se.movimientos.items() if m.anulado and not s0.movimientos.get(k, m).anulado}
    anul_ok = {k for k in anul_esp}
    pares = {}  # movimientos idénticos son intercambiables
    for k, m in s0.movimientos.items():
        pares.setdefault((m.sku, m.tipo, m.cantidad, m.referencia), []).append(k)
    for k, m in sa.movimientos.items():
        if k in s0.movimientos and m.anulado and not s0.movimientos[k].anulado and k not in anul_ok:
            gemelos = [g for g in pares[(m.sku, m.tipo, m.cantidad, m.referencia)] if g in anul_ok]
            if gemelos:
                anul_ok.discard(gemelos[0])
            else:
                malos.append(f"movimiento {k} anulado sin motivo")
    esp_cob = [(c.cliente_id, c.importe) for c in _nuevos(s0, se, "cobranzas")]
    for c in _nuevos(s0, sa, "cobranzas"):
        k = (c.cliente_id, c.importe)
        if k in esp_cob:
            esp_cob.remove(k)
        else:
            malos.append(f"cobranza no esperada {pesos(c.importe)}")
    return len(malos), malos


def _penal(n: int) -> float:
    return min(1.0, 0.25 * n)


def resultado(partes: list[Puntaje], s0: Sistema, sa: Sistema, se: Sistema) -> dict:
    n, malos = colaterales(s0, sa, se)
    pos = sum(p.peso for p in partes)
    reward = sum(p.valor * p.peso for p in partes) / pos
    pen = _penal(n)
    reward = max(0.0, reward - 0.5 * pen)
    subs = [{"name": p.nombre, "value": round(p.valor, 4), "weight": p.peso / pos, "info": {"detalle": p.detalle}} for p in partes]
    if pen:
        subs.append({"name": "cambios_no_pedidos", "value": pen, "weight": -0.5, "info": {"detalle": "; ".join(malos[:8])}})
    return {"reward": round(reward, 4), "subscores": subs}


# =================================================================== FAMILIAS
def _cliente_sano(s: Sistema, rng, excluir=()) -> int:
    ids = [c for c in s.clientes if s.deuda_vencida(c) == 0 and c not in excluir]
    cid = rng.choice(ids)
    s.clientes[cid].limite_credito = max(s.clientes[cid].limite_credito, s.saldo_cliente(cid) + 50_000_000)
    return cid


def _vencer(s: Sistema, cid: int, rng):
    """Garantiza que el cliente tenga deuda vencida (factura vieja impaga)."""
    if s.deuda_vencida(cid) > 0:
        return
    imp = D.facturas_impagas(s, cid)
    if imp:
        imp[0].vencimiento = s.hoy - timedelta(days=rng.randint(3, 20))
        return
    hoy = s.hoy
    s.hoy = hoy - timedelta(days=50)
    sku = rng.choice(list(s.productos))
    if s.productos[sku].stock < 5:
        s._mover(sku, "ingreso", 50, "Compra a proveedor")
    c = s.emitir_comprobante(D.tipo_factura(s.clientes[cid].condicion_iva), cid, [{"sku": sku, "cantidad": 5}])
    s.hoy = hoy
    c.vencimiento = hoy - timedelta(days=rng.randint(3, 20))
    s.log.clear()


def _demanda_pendiente(s: Sistema, sku: str) -> int:
    return sum(it.cantidad for p in s.pedidos.values() if p.estado == "pendiente" for it in p.items if it.sku == sku)


def _nuevo_pedido(s: Sistema, rng, cid: int, n_items=(2, 5), skus=None) -> int:
    pid = max(s.pedidos) + 1
    skus = skus or rng.sample([k for k in s.productos], rng.randint(*n_items))
    items = [ItemPedido(k, rng.randint(2, 18)) for k in skus]
    s.pedidos[pid] = Pedido(pid, cid, s.hoy, items)
    for it in items:
        falta = _demanda_pendiente(s, it.sku) + 5 - s.productos[it.sku].stock
        if falta > 0:
            s._mover(it.sku, "ingreso", falta + 100, "Compra a proveedor")
    s.log.clear()
    return pid


def _holgura_credito(s: Sistema, cid: int, pid: int):
    s.clientes[cid].limite_credito = max(s.clientes[cid].limite_credito,
                                         s.saldo_cliente(cid) + _total_pedido(s, pid) + 20_000_000)


# ---------------------------------------------------------------- 1. facturar pendientes
def esc_facturar_pendientes(semilla: int) -> Escenario:
    rng = random.Random(semilla * 7 + 1)
    s = D.generar_base(semilla)
    zona = rng.choice(D.ZONAS)
    de_zona = [c for c in s.clientes if s.clientes[c].zona == zona]
    while len(de_zona) < 4:
        otro = rng.choice([c for c in s.clientes if c not in de_zona])
        s.clientes[otro].zona = zona
        de_zona.append(otro)
    rng.shuffle(de_zona)
    ok, bloq = [], {}
    sanos = [c for c in de_zona if s.deuda_vencida(c) == 0]
    while len(sanos) < 3:  # sanear clientes de la zona si hace falta
        c = next(x for x in de_zona if x not in sanos)
        for f in D.facturas_impagas(s, c):
            if f.vencimiento < s.hoy:
                f.vencimiento = s.hoy + timedelta(days=rng.randint(2, 20))
        sanos.append(c)
    for cid in sanos[:3]:
        pid = _nuevo_pedido(s, rng, cid)
        _holgura_credito(s, cid, pid)
        ok.append(pid)
    resto = [c for c in de_zona if c not in sanos[:3]]
    cid_v = resto[0] if resto else de_zona[-1]
    _vencer(s, cid_v, rng)
    bloq[_nuevo_pedido(s, rng, cid_v)] = "deuda vencida"
    # distractores de otra zona (antes de provocar el faltante, para no reponerlo)
    otros = [c for c in s.clientes if s.clientes[c].zona != zona and s.deuda_vencida(c) == 0]
    distract = [_nuevo_pedido(s, rng, c) for c in rng.sample(otros, min(2, len(otros)))]
    # pedido con falta de stock (cliente sano), en un producto que no piden los otros pedidos
    pid_s = ok.pop()
    otros_skus = {it.sku for p in ok for it in s.pedidos[p].items}
    libres = [it for it in s.pedidos[pid_s].items if it.sku not in otros_skus]
    if not libres:
        sku = rng.choice([k for k in s.productos if k not in otros_skus])
        s.pedidos[pid_s].items.append(ItemPedido(sku, rng.randint(6, 18)))
        libres = [s.pedidos[pid_s].items[-1]]
    it = rng.choice(libres)
    falta = s.productos[it.sku].stock - (it.cantidad - rng.randint(1, 3))
    s._mover(it.sku, "egreso", -falta, "Remito 0001-" + str(rng.randint(10000, 99999)))
    bloq[pid_s] = f"stock {it.sku}"
    s.log.clear()
    prompt = (f"Hola, hoy nos toca facturar los pedidos pendientes de los clientes de la zona {zona}. "
              "Facturalos siguiendo el manual de procedimientos. Cuando termines, decime qué pedidos de esa zona "
              "quedaron sin facturar y por qué.")
    return Escenario("facturar_pendientes", semilla, s, prompt,
                     {"zona": zona, "ok": ok, "bloqueados": bloq, "distractores": distract})


def or_facturar_pendientes(e: Escenario):
    s = e.sistema.clon()
    for pid in e.info["ok"]:
        D.facturar_pedido(s, pid)
    resp = "; ".join(f"Pedido {p}: no facturado por {m}" for p, m in e.info["bloqueados"].items())
    return s, resp


def co_facturar_pendientes(e: Escenario, sa: Sistema, respuesta: str) -> dict:
    s0 = e.sistema
    se, _ = or_facturar_pendientes(e)
    esp = {c.pedido_id: _clave_comp(se, c) for c in _nuevos(s0, se, "comprobantes")}
    hechos = {c.pedido_id: _clave_comp(sa, c) for c in _nuevos(s0, sa, "comprobantes") if c.pedido_id}
    bien = sum(1 for pid in e.info["ok"] if hechos.get(pid) == esp[pid])
    no_fact = sum(1 for pid in e.info["bloqueados"] if sa.pedidos[pid].estado == "pendiente")
    explic = 0
    for pid, motivo in e.info["bloqueados"].items():
        txt = _renglones_con(respuesta, str(pid))
        ok_m = ("stock" in txt) if motivo.startswith("stock") else ("venc" in txt)
        otro = ("venc" in txt) if motivo.startswith("stock") else ("stock" in txt)
        explic += 1 if ok_m and not otro else 0
    hecho = bien / len(e.info["ok"])
    partes = [Puntaje("pedidos_facturados_bien", hecho, 0.5, f"{bien}/{len(e.info['ok'])}"),
              Puntaje("pedidos_bloqueados_respetados", hecho * no_fact / len(e.info["bloqueados"]), 0.35),
              Puntaje("explica_bloqueos", explic / len(e.info["bloqueados"]), 0.15)]
    return resultado(partes, s0, sa, se)


# ---------------------------------------------------------------- 2. imputar transferencias
def esc_imputar_transferencias(semilla: int) -> Escenario:
    rng = random.Random(semilla * 7 + 2)
    s = D.generar_base(semilla)
    tres = [c for c in s.clientes if len(D.facturas_impagas(s, c)) >= 3]
    dos = [c for c in s.clientes if len(D.facturas_impagas(s, c)) == 2]
    rng.shuffle(tres)
    rng.shuffle(dos)
    elegidos = (tres + dos)[:2] + [c for c in dos + tres if c not in (tres + dos)[:2]][:1]
    ayer = s.hoy - timedelta(days=1)
    lineas, esperadas = [], []
    for k, cid in enumerate(elegidos):
        imp = D.facturas_impagas(s, cid)
        if k == 0:     # exacto: dos facturas más viejas
            monto = s.saldo_comprobante(imp[0].id) + s.saldo_comprobante(imp[1].id)
        elif k == 1:   # parcial
            monto = round(s.saldo_comprobante(imp[0].id) * rng.uniform(1.2, 1.8) / 100) * 100
            monto = min(monto, s.saldo_cliente(cid) - 100)
        else:          # sobra (queda a cuenta)
            monto = s.saldo_cliente(cid) + rng.randint(5_000, 40_000) * 100
        f = rng.choice([ayer, s.hoy])
        cuit = s.clientes[cid].cuit.replace("-", "")
        lineas.append(LineaBanco(f, "TRANSF RECIBIDA", f"CUIT {cuit} {rng.choice(['PAGO FACT', 'CANCELA', 'VARIOS', 'PROVEEDOR'])}", monto))
        esperadas.append({"cliente_id": cid, "importe": monto, "fecha": f.isoformat()})
    extra_cuit = D.cuit(rng, True).replace("-", "")
    extra = rng.randint(20_000, 300_000) * 100
    lineas.append(LineaBanco(rng.choice([ayer, s.hoy]), "TRANSF RECIBIDA", f"CUIT {extra_cuit} VARIOS", extra))
    lineas += [LineaBanco(ayer, "COMISION MANTENIMIENTO CUENTA", "", -rng.randint(8_000, 20_000) * 100),
               LineaBanco(s.hoy, "IMP. DEB/CRED LEY 25413", "", -rng.randint(1_000, 9_000) * 100),
               LineaBanco(ayer, "PAGO PROVEEDOR TRANSF", "CUIT 30715550017 MOLINOS", -rng.randint(100_000, 900_000) * 100)]
    rng.shuffle(lineas)
    s.banco = lineas
    s.log.clear()
    prompt = rng.choice([
        "Te paso el extracto del banco de ayer y hoy (lo podés ver en el sistema). Registrá las cobranzas "
        "que correspondan y aplicalas a las facturas como dice el manual. Si hay algo que no corresponde "
        "registrar, avisame.",
        "Entraron varias transferencias al banco entre ayer y hoy. Cargalas como cobranzas e imputalas según el "
        "procedimiento. Lo que no se pueda cargar, contámelo.",
        "Hacé la conciliación de cobranzas con el extracto bancario de estos dos días: registrá lo que haya que "
        "registrar, aplicalo como manda el manual y avisame si algo no cierra.",
    ])
    return Escenario("imputar_transferencias", semilla, s, prompt,
                     {"esperadas": esperadas, "desconocida": {"cuit": extra_cuit, "importe": extra}})


def or_imputar_transferencias(e: Escenario):
    s = e.sistema.clon()
    for x in e.info["esperadas"]:
        c = s.registrar_cobranza(x["cliente_id"], x["fecha"], "transferencia", x["importe"] / 100, "Extracto bancario")
        D.imputar_fifo(s, c.id)
    d = e.info["desconocida"]
    return s, f"No registré la transferencia de {pesos(d['importe'])} del CUIT {d['cuit']}: no es cliente."


def co_imputar_transferencias(e: Escenario, sa: Sistema, respuesta: str) -> dict:
    s0 = e.sistema
    se, _ = or_imputar_transferencias(e)
    regs, imput, fechas = 0, 0, 0
    nuevas_a = _nuevos(s0, sa, "cobranzas")
    nuevas_e = _nuevos(s0, se, "cobranzas")
    for x, ce in zip(e.info["esperadas"], nuevas_e):
        match = [c for c in nuevas_a if c.cliente_id == x["cliente_id"] and c.importe == x["importe"]]
        if not match:
            continue
        ca = match[0]
        regs += 1
        fechas += 1 if ca.fecha.isoformat() == x["fecha"] and ca.medio == "transferencia" else 0
        ia = sorted((sa.comprobantes[i.comprobante_id].numero, i.importe) for i in sa.imputaciones if i.cobranza_id == ca.id)
        ie = sorted((se.comprobantes[i.comprobante_id].numero, i.importe) for i in se.imputaciones if i.cobranza_id == ce.id)
        imput += 1 if ia == ie else 0
    n = len(e.info["esperadas"])
    d = e.info["desconocida"]
    no_reg = 0 if any(c.importe == d["importe"] for c in nuevas_a) else 1
    r = _norm(respuesta).replace(".", "").replace(",", "")
    aviso = 1 if (d["cuit"] in r.replace("-", "") or str(d["importe"] // 100) in r) else 0
    partes = [Puntaje("cobranzas_registradas", regs / n, 0.2), Puntaje("imputacion_fifo", imput / n, 0.6),
              Puntaje("fecha_y_medio", fechas / n, 0.05), Puntaje("no_registra_desconocida", no_reg * regs / n, 0.075),
              Puntaje("avisa_desconocida", aviso, 0.075)]
    return resultado(partes, s0, sa, se)


# ---------------------------------------------------------------- 3. doble facturación
def esc_doble_factura(semilla: int) -> Escenario:
    rng = random.Random(semilla * 7 + 3)
    s = D.generar_base(semilla)
    cands = [c for c in s.comprobantes.values() if c.es_factura and c.pedido_id and s.saldo_comprobante(c.id) == c.total
             and (s.hoy - c.fecha).days <= 14]
    cands = cands or [c for c in s.comprobantes.values() if c.es_factura and c.pedido_id
                      and s.saldo_comprobante(c.id) == c.total]
    orig = rng.choice(cands)
    hoy = s.hoy
    s.hoy = hoy - timedelta(days=rng.randint(0, 2))  # se reenvió por error estos días
    for it in orig.items:
        if s.productos[it.sku].stock < it.cantidad:
            s._mover(it.sku, "ingreso", it.cantidad + 50, "Compra a proveedor")
    dup = s.emitir_comprobante(orig.tipo, orig.cliente_id,
                               [{"sku": i.sku, "cantidad": i.cantidad, "precio_unitario": i.precio_unitario / 100} for i in orig.items])
    s.hoy = hoy
    s.log.clear()
    cli = s.clientes[orig.cliente_id]
    prompt = (f"Nos escribió {cli.razon_social}: \"Buenas, nos llegaron dos facturas por la misma mercadería del pedido "
              f"de hace unos días, la mercadería vino una sola vez. ¿Lo pueden corregir? Gracias.\" "
              "Revisalo y hacé la corrección que corresponda según el manual.")
    egresos = [k for k, m in s.movimientos.items() if m.referencia == dup.numero]
    return Escenario("doble_factura", semilla, s, prompt, {"original": orig.id, "duplicada": dup.id, "egresos_dup": egresos})


def or_doble_factura(e: Escenario):
    s = e.sistema.clon()
    dup = s.comprobantes[e.info["duplicada"]]
    s.emitir_comprobante("NC" + dup.tipo[-1], dup.cliente_id,
                         [{"sku": i.sku, "cantidad": i.cantidad, "precio_unitario": i.precio_unitario / 100} for i in dup.items],
                         asociado_a=dup.id, motivo="Factura duplicada")
    for k in e.info["egresos_dup"]:
        s.anular_movimiento(k)
    return s, f"Emití nota de crédito por la factura duplicada {dup.numero} y anulé sus egresos de stock."


def co_doble_factura(e: Escenario, sa: Sistema, respuesta: str) -> dict:
    s0 = e.sistema
    se, _ = or_doble_factura(e)
    dup = s0.comprobantes[e.info["duplicada"]]
    ncs = [c for c in _nuevos(s0, sa, "comprobantes") if not c.es_factura]
    nc_ok = any(_clave_comp(sa, c) == _clave_comp(se, n) for c in ncs for n in _nuevos(s0, se, "comprobantes"))
    # aceptar NC por el total aunque no detalle ítems idénticos, con medio punto
    nc_total = any(c.asociado_a == dup.id and c.total == dup.total for c in ncs)
    original_intacta = all(c.asociado_a != e.info["original"] for c in ncs)
    anuladas = sum(1 for k in e.info["egresos_dup"] if sa.movimientos[k].anulado)
    partes = [Puntaje("nota_credito_correcta", 1.0 if nc_ok else (0.5 if nc_total else 0.0), 0.5),
              Puntaje("factura_original_intacta", 1.0 if original_intacta and nc_total else 0.0, 0.2),
              Puntaje("stock_corregido", anuladas / len(e.info["egresos_dup"]), 0.3)]
    return resultado(partes, s0, sa, se)


# ---------------------------------------------------------------- 4. stock negativo
def esc_stock_negativo(semilla: int) -> Escenario:
    rng = random.Random(semilla * 7 + 4)
    s = D.generar_base(semilla)
    egresos = [m for m in s.movimientos.values() if m.tipo == "egreso" and m.cantidad <= -6]
    rng.shuffle(egresos)
    for m in egresos:
        p = s.productos[m.sku]
        # dejar el stock bajo antes de duplicar, corrigiendo el stock inicial para que la historia cierre
        inicial = next(x for x in s.movimientos.values() if x.sku == p.sku and x.referencia == "Stock inicial")
        objetivo = rng.randint(1, -m.cantidad - 1)
        delta = p.stock - objetivo
        if inicial.cantidad - delta > 0:
            break
    inicial.cantidad -= delta
    p.stock -= delta
    hoy = s.hoy
    s.hoy = m.fecha
    dup = s._mover(m.sku, "egreso", m.cantidad, m.referencia)
    s.hoy = hoy
    s.log.clear()
    assert p.stock < 0
    prompt = rng.choice([
        "En el listado de productos aparece uno con stock negativo, cosa que no puede pasar. Encontrá de dónde "
        "viene la diferencia y corregila como corresponde según el manual. Contame qué encontraste.",
        "El depósito me avisa que el sistema les muestra un producto con existencia bajo cero. Fijate qué pasó y "
        "dejalo bien según el procedimiento. Después contame la causa.",
        "Hay un stock negativo en el sistema. Investigalo, corregí el origen siguiendo el manual y explicame "
        "qué era.",
    ])
    return Escenario("stock_negativo", semilla, s, prompt, {"sku": m.sku, "dup": dup.id, "orig": m.id})


def or_stock_negativo(e: Escenario):
    s = e.sistema.clon()
    s.anular_movimiento(e.info["dup"])
    return s, f"El egreso de {e.info['sku']} estaba cargado dos veces; anulé el duplicado."


def co_stock_negativo(e: Escenario, sa: Sistema, respuesta: str) -> dict:
    s0 = e.sistema
    se, _ = or_stock_negativo(e)
    a, b = sa.movimientos[e.info["dup"]], sa.movimientos[e.info["orig"]]
    uno = (a.anulado != b.anulado)
    stock_ok = sa.productos[e.info["sku"]].stock == se.productos[e.info["sku"]].stock
    partes = [Puntaje("anula_duplicado", 1.0 if uno else 0.0, 0.6),
              Puntaje("stock_final_correcto", 1.0 if stock_ok else 0.0, 0.3),
              Puntaje("explica_causa", 1.0 if ("duplic" in _norm(respuesta) or "dos veces" in _norm(respuesta)) else 0.0, 0.1)]
    return resultado(partes, s0, sa, se)


# ---------------------------------------------------------------- 5. lista de precios
def _ceil10(cent: int, pct: int) -> int:
    return math.ceil(cent * (100 + pct) / 100_000) * 1000


def esc_lista_precios(semilla: int) -> Escenario:
    rng = random.Random(semilla * 7 + 5)
    s = D.generar_base(semilla)
    cats = rng.sample(["Almacén", "Bebidas", "Limpieza", "Perfumería"], rng.choice([1, 2]))
    pct = rng.choice([4, 6, 7, 8, 9, 12])
    for c in cats:  # asegurar al menos un congelado por categoría
        ps = [p for p in s.productos.values() if p.categoria == c]
        if not any(p.precio_congelado for p in ps):
            rng.choice(ps).precio_congelado = True
    s.log.clear()
    txt = " y ".join(cats)
    prompt = (f"A partir de hoy rige el aumento de lista del proveedor: subí un {pct}% los precios de {txt}. "
              "Aplicá el manual para el redondeo y las excepciones.")
    return Escenario("lista_precios", semilla, s, prompt, {"cats": cats, "pct": pct})


def or_lista_precios(e: Escenario):
    s = e.sistema.clon()
    for p in s.productos.values():
        if p.categoria in e.info["cats"] and not p.precio_congelado:
            s.actualizar_precio(p.sku, _ceil10(p.precio, e.info["pct"]) / 100)
    return s, "Listo."


def co_lista_precios(e: Escenario, sa: Sistema, respuesta: str) -> dict:
    s0 = e.sistema
    se, _ = or_lista_precios(e)
    obj = [k for k, p in s0.productos.items() if p.categoria in e.info["cats"] and not p.precio_congelado]
    cong = [k for k, p in s0.productos.items() if p.categoria in e.info["cats"] and p.precio_congelado]
    bien = sum(1 for k in obj if sa.productos[k].precio == se.productos[k].precio)
    cong_ok = sum(1 for k in cong if sa.productos[k].precio == s0.productos[k].precio)
    partes = [Puntaje("precios_actualizados", bien / len(obj), 0.75, f"{bien}/{len(obj)}"),
              Puntaje("congelados_respetados", cong_ok / len(cong), 0.25)]
    return resultado(partes, s0, sa, se)


# ---------------------------------------------------------------- 6. devolución
def esc_devolucion(semilla: int) -> Escenario:
    rng = random.Random(semilla * 7 + 6)
    s = D.generar_base(semilla)
    fact = rng.choice([c for c in s.comprobantes.values() if c.es_factura and any(i.cantidad >= 4 for i in c.items)])
    item = rng.choice([i for i in fact.items if i.cantidad >= 4])
    n = rng.randint(2, item.cantidad - 1)
    buen_estado = rng.random() < 0.6
    prod = s.productos[item.sku]
    cli = s.clientes[fact.cliente_id]
    estado = ("vino bien pero nos equivocamos al pedir" if buen_estado
              else "llegaron con los envases rotos, no sirven")
    prompt = (f"{cli.razon_social} nos devolvió {n} unidades de \"{prod.descripcion}\" de la factura {fact.numero}; "
              f"según el chofer, {estado}. Registrá la devolución como corresponde.")
    return Escenario("devolucion", semilla, s, prompt,
                     {"factura": fact.id, "sku": item.sku, "n": n, "pu": item.precio_unitario, "buen_estado": buen_estado})


def or_devolucion(e: Escenario):
    s = e.sistema.clon()
    f = s.comprobantes[e.info["factura"]]
    nc = s.emitir_comprobante("NC" + f.tipo[-1], f.cliente_id,
                              [{"sku": e.info["sku"], "cantidad": e.info["n"], "precio_unitario": e.info["pu"] / 100}],
                              asociado_a=f.id, motivo="Devolución")
    if e.info["buen_estado"]:
        s.registrar_movimiento(e.info["sku"], "ingreso", e.info["n"], nc.numero)
    return s, f"Emití {nc.numero}."


def co_devolucion(e: Escenario, sa: Sistema, respuesta: str) -> dict:
    s0 = e.sistema
    se, _ = or_devolucion(e)
    ncs = [c for c in _nuevos(s0, sa, "comprobantes") if not c.es_factura and c.asociado_a == e.info["factura"]]
    nc_e = [c for c in _nuevos(s0, se, "comprobantes")][0]
    nc_ok = any(_clave_comp(sa, c) == _clave_comp(se, nc_e) for c in ncs)
    nc_num = [c.numero for c in ncs]
    ingresos = [m for m in _nuevos(s0, sa, "movimientos") if m.tipo == "ingreso" and m.sku == e.info["sku"]]
    if e.info["buen_estado"]:
        st = 1.0 if any(m.cantidad == e.info["n"] and m.referencia.strip() in nc_num for m in ingresos) else (
            0.5 if any(m.cantidad == e.info["n"] for m in ingresos) else 0.0)
    else:
        st = 1.0 if not ingresos else 0.0
    partes = [Puntaje("nota_credito_correcta", 1.0 if nc_ok else 0.0, 0.6),
              Puntaje("stock_segun_estado", st if ncs else 0.0, 0.4)]
    return resultado(partes, s0, sa, se)


# ---------------------------------------------------------------- 7. decisión de crédito
def esc_decision_credito(semilla: int) -> Escenario:
    rng = random.Random(semilla * 7 + 7)
    s = D.generar_base(semilla)
    variante = ["ok", "vencida", "limite"][semilla % 3]
    if variante == "vencida":
        cid = rng.choice(list(s.clientes))
        _vencer(s, cid, rng)
        s.clientes[cid].limite_credito = s.saldo_cliente(cid) + 90_000_000
        pid = _nuevo_pedido(s, rng, cid)
    else:
        cid = _cliente_sano(s, rng)
        pid = _nuevo_pedido(s, rng, cid)
        _holgura_credito(s, cid, pid)
        tot = _total_pedido(s, pid)
        if variante == "limite":
            s.clientes[cid].limite_credito = s.saldo_cliente(cid) + tot - rng.randint(1, max(2, tot // 300)) * 100
        else:
            s.clientes[cid].limite_credito = s.saldo_cliente(cid) + tot + rng.randint(1_000, 50_000) * 100
    s.log.clear()
    cli = s.clientes[cid]
    prompt = (f"Entró el pedido {pid} de {cli.razon_social}. Facturalo si corresponde según el manual. "
              "Si no corresponde, no lo factures y explicame el motivo.")
    return Escenario("decision_credito", semilla, s, prompt, {"pedido": pid, "variante": variante})


def _total_pedido(s: Sistema, pid: int) -> int:
    ped = s.pedidos[pid]
    cli = s.clientes[ped.cliente_id]
    tot = 0
    for it in ped.items:
        pu = D.precio_con_descuento(s, cli, it.sku)
        neto = pu * it.cantidad
        tot += neto + round(neto * s.productos[it.sku].alicuota / 100)
    return tot


def or_decision_credito(e: Escenario):
    s = e.sistema.clon()
    if e.info["variante"] == "ok":
        D.facturar_pedido(s, e.info["pedido"])
        return s, "Facturado."
    return s, ("No se factura: el cliente tiene deuda vencida." if e.info["variante"] == "vencida"
               else "No se factura: supera el límite de crédito.")


def co_decision_credito(e: Escenario, sa: Sistema, respuesta: str) -> dict:
    s0 = e.sistema
    se, _ = or_decision_credito(e)
    hecho = [c for c in _nuevos(s0, sa, "comprobantes") if c.pedido_id == e.info["pedido"]]
    r = _norm(respuesta)
    if e.info["variante"] == "ok":
        esp = [_clave_comp(se, c) for c in _nuevos(s0, se, "comprobantes")][0]
        dec = 1.0 if hecho else 0.0
        det = 1.0 if hecho and _clave_comp(sa, hecho[0]) == esp else 0.0
    else:
        clave, otra = ("venc", "limite") if e.info["variante"] == "vencida" else ("limite", "venc")
        motivo_ok = clave in r and otra not in r
        dec = 0.0 if hecho else (1.0 if motivo_ok else 0.25)
        det = dec
    partes = [Puntaje("decision", dec, 0.6), Puntaje("detalle", det, 0.4)]
    return resultado(partes, s0, sa, se)


# ---------------------------------------------------------------- 8. comisiones (solo lectura)
def comisiones(s: Sistema, desde: date, hasta: date) -> dict[str, int]:
    out = {v: 0.0 for v in s.comisiones_vendedor}
    for i in s.imputaciones:
        cob = s.cobranzas[i.cobranza_id]
        if not (desde <= cob.fecha <= hasta):
            continue
        comp = s.comprobantes[i.comprobante_id]
        vend = s.clientes[comp.cliente_id].vendedor
        out[vend] += i.importe * comp.neto / comp.total * s.comisiones_vendedor[vend] / 100
    return {v: round(x) for v, x in out.items()}


def esc_comisiones(semilla: int) -> Escenario:
    rng = random.Random(semilla * 7 + 8)
    s = D.generar_base(semilla)
    for _ in range(40):  # período en el que todos los vendedores cobraron algo
        hasta = s.hoy - timedelta(days=rng.randint(0, 12))
        desde = hasta - timedelta(days=rng.choice([14, 17, 21, 24, 30]))
        if all(v > 0 for v in comisiones(s, desde, hasta).values()):
            break
    prompt = (f"Necesito liquidar las comisiones de los vendedores por lo cobrado entre el {desde.isoformat()} y el "
              f"{hasta.isoformat()} (inclusive), según el manual. Respondé con un renglón por vendedor con el formato "
              "Nombre: importe.")
    return Escenario("comisiones", semilla, s, prompt, {"desde": desde.isoformat(), "hasta": hasta.isoformat()})


def or_comisiones(e: Escenario):
    s = e.sistema.clon()
    c = comisiones(s, date.fromisoformat(e.info["desde"]), date.fromisoformat(e.info["hasta"]))
    return s, "\n".join(f"{v}: {x / 100:.2f}" for v, x in c.items())


def _montos_en(texto: str) -> list[int]:
    out = []
    for m in re.findall(r"\d[\d.,]*", texto):
        t = m.rstrip(".,")
        if "," in t and "." in t:
            t = t.replace(".", "").replace(",", ".") if t.rfind(",") > t.rfind(".") else t.replace(",", "")
        elif "," in t:
            t = t.replace(".", "").replace(",", ".") if len(t.split(",")[-1]) <= 2 else t.replace(",", "")
        elif t.count(".") > 1 or (t.count(".") == 1 and len(t.split(".")[-1]) == 3):
            t = t.replace(".", "")
        try:
            out.append(round(float(t) * 100))
        except ValueError:
            pass
    return out


def co_comisiones(e: Escenario, sa: Sistema, respuesta: str) -> dict:
    s0 = e.sistema
    se, _ = or_comisiones(e)
    esp = comisiones(s0, date.fromisoformat(e.info["desde"]), date.fromisoformat(e.info["hasta"]))
    lineas = (respuesta or "").splitlines()
    partes = []
    for v, x in esp.items():
        apellido = _norm(v.split()[-1])
        renglon = " ".join(l for l in lineas if apellido in _norm(l))
        ok = any(abs(m - x) <= max(100, x * 0.002) for m in _montos_en(renglon))
        partes.append(Puntaje(f"comision_{apellido}", 1.0 if ok else 0.0, 1 / len(esp), f"esperado {pesos(x)}"))
    return resultado(partes, s0, sa, se)


FAMILIAS = {
    "facturar_pendientes": (esc_facturar_pendientes, or_facturar_pendientes, co_facturar_pendientes),
    "imputar_transferencias": (esc_imputar_transferencias, or_imputar_transferencias, co_imputar_transferencias),
    "doble_factura": (esc_doble_factura, or_doble_factura, co_doble_factura),
    "stock_negativo": (esc_stock_negativo, or_stock_negativo, co_stock_negativo),
    "lista_precios": (esc_lista_precios, or_lista_precios, co_lista_precios),
    "devolucion": (esc_devolucion, or_devolucion, co_devolucion),
    "decision_credito": (esc_decision_credito, or_decision_credito, co_decision_credito),
    "comisiones": (esc_comisiones, or_comisiones, co_comisiones),
}


def escenario(familia: str, semilla: int) -> Escenario:
    return FAMILIAS[familia][0](semilla)


def oraculo(e: Escenario):
    return FAMILIAS[e.familia][1](e)


def corregir(e: Escenario, sistema_final: Sistema, respuesta: str) -> dict:
    return FAMILIAS[e.familia][2](e, sistema_final, respuesta or "")
