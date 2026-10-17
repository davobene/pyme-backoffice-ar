"""Genera, a partir de una semilla, una distribuidora con historia contable coherente.

La historia se simula con las mismas operaciones del sistema (avanzando la fecha),
así numeración, stock, cobranzas e imputaciones quedan consistentes.
"""
from __future__ import annotations

import random
from datetime import date, timedelta

from .sistema import Cliente, ItemPedido, Pedido, Producto, Sistema

HOY = date(2026, 9, 30)

VENDEDORES = {"Marcela Quiroga": 3.0, "Hugo Benítez": 4.0, "Lucas Ferreyra": 5.0}
ZONAS = ["Alto Valle", "Confluencia", "Valle Medio", "Zona Andina"]

_RUBROS = ["Almacén", "Autoservicio", "Kiosco", "Despensa", "Minimercado", "Supermercado", "Maxikiosco", "Distribuidora",
           "Bar", "Rotisería", "Panadería", "Hotel", "Comedor", "Club"]
_NOMBRES = ["Don Tito", "La Esquina", "Los Álamos", "El Manzano", "Santa Rita", "La Chacra", "San Cayetano", "El Puente",
            "Las Bardas", "Rincón Patagónico", "La Ruta 22", "El Galpón", "Los Perales", "Mi Barrio", "La Familia",
            "Del Canal", "El Molino", "Las Lilas", "Don Pepe", "La Cooperativa", "Las Heras", "El Faro", "Nueva Era",
            "La Golondrina", "San Martín", "Sol de Mayo", "El Trébol", "Los Hermanos", "La Huella", "El Mirador"]
_SOC = ["SRL", "SA", "", "", "SAS"]

_PRODUCTOS = [
    ("Almacén", "Yerba mate 1 kg", 21.0), ("Almacén", "Azúcar 1 kg", 21.0), ("Almacén", "Harina 000 1 kg", 10.5),
    ("Almacén", "Fideos guiseros 500 g", 21.0), ("Almacén", "Arroz largo fino 1 kg", 21.0), ("Almacén", "Aceite girasol 1,5 L", 21.0),
    ("Almacén", "Puré de tomate 520 g", 21.0), ("Almacén", "Lentejas 400 g", 10.5), ("Almacén", "Galletitas de agua 3x100 g", 21.0),
    ("Almacén", "Dulce de leche 400 g", 21.0), ("Almacén", "Café molido 500 g", 21.0), ("Almacén", "Leche en polvo 800 g", 10.5),
    ("Bebidas", "Gaseosa cola 2,25 L", 21.0), ("Bebidas", "Agua mineral 2 L", 21.0), ("Bebidas", "Jugo en polvo x18", 21.0),
    ("Bebidas", "Cerveza lata 473 cc", 21.0), ("Bebidas", "Vino tinto 1 L", 21.0), ("Bebidas", "Soda sifón 2 L", 21.0),
    ("Limpieza", "Lavandina 2 L", 21.0), ("Limpieza", "Detergente 750 ml", 21.0), ("Limpieza", "Jabón en polvo 3 kg", 21.0),
    ("Limpieza", "Desodorante de ambientes", 21.0), ("Limpieza", "Esponja doble uso x3", 21.0), ("Limpieza", "Bolsas de residuo x30", 21.0),
    ("Perfumería", "Papel higiénico x4", 21.0), ("Perfumería", "Shampoo 400 ml", 21.0), ("Perfumería", "Jabón de tocador x3", 21.0),
    ("Perfumería", "Pasta dental 90 g", 21.0), ("Perfumería", "Pañales talle G x30", 21.0), ("Perfumería", "Toallitas húmedas x50", 21.0),
    ("Ofertas", "Combo mate (yerba + bizcochos)", 21.0), ("Ofertas", "Pack limpieza hogar", 21.0), ("Ofertas", "Pack gaseosa x6", 21.0),
    ("Ofertas", "Galletitas surtidas x6", 21.0),
]
_PREFIJO = {"Almacén": "ALM", "Bebidas": "BEB", "Limpieza": "LIM", "Perfumería": "PER", "Ofertas": "OFE"}


def cuit(rng: random.Random, persona_juridica: bool) -> str:
    tipo = rng.choice([30, 33]) if persona_juridica else rng.choice([20, 27])
    dni = rng.randint(10_000_000, 45_999_999) if not persona_juridica else rng.randint(50_000_000, 72_999_999)
    base = f"{tipo}{dni:08d}"
    pesos_ = [5, 4, 3, 2, 7, 6, 5, 4, 3, 2]
    s = sum(int(d) * p for d, p in zip(base, pesos_))
    dv = 11 - s % 11
    if dv == 11:
        dv = 0
    if dv == 10:
        return cuit(rng, persona_juridica)
    return f"{base[:2]}-{base[2:]}-{dv}"


def generar_base(semilla: int) -> Sistema:
    rng = random.Random(semilla)
    inicio = HOY - timedelta(days=75)
    s = Sistema(hoy=inicio)
    s.comisiones_vendedor = dict(VENDEDORES)

    # productos
    usados = set()
    for i, (cat, desc, alic) in enumerate(_PRODUCTOS):
        sku = f"{_PREFIJO[cat]}-{100 + i}"
        precio = rng.choice(range(800, 9000, 10)) * 100 if cat != "Ofertas" else rng.choice(range(5000, 20000, 10)) * 100
        s.productos[sku] = Producto(sku, desc, cat, precio, alic, 0, precio_congelado=(cat != "Ofertas" and rng.random() < 0.12))
        s._mover(sku, "ingreso", rng.randint(120, 600), "Stock inicial")

    # clientes
    vendedores = list(VENDEDORES)
    for cid in range(1, 27):
        while True:
            nombre = f"{rng.choice(_RUBROS)} {rng.choice(_NOMBRES)}"
            if nombre not in usados:
                usados.add(nombre)
                break
        cond = rng.choices(["RI", "MT", "CF", "EX"], weights=[45, 35, 15, 5])[0]
        juridica = cond == "RI" and rng.random() < 0.6
        rs = f"{nombre} {rng.choice(_SOC)}".strip() if juridica else nombre
        s.clientes[cid] = Cliente(cid, rs, cuit(rng, juridica), cond, rng.choice([0.0, 0.0, 3.0, 5.0, 8.0]),
                                  rng.choice(range(800, 4000, 100)) * 1000 * 100, rng.choice(vendedores),
                                  rng.choice(ZONAS), rng.choice([15, 30, 30, 45]))

    # historia: pedidos facturados y cobranzas, día por día
    pid = 1
    for d in range(70):
        s.hoy = inicio + timedelta(days=d)
        if s.hoy.weekday() >= 5:
            continue
        for _ in range(rng.randint(1, 3)):
            cli = s.clientes[rng.randint(1, 26)]
            items = [ItemPedido(sku, rng.randint(2, 24)) for sku in rng.sample(list(s.productos), rng.randint(2, 6))]
            s.pedidos[pid] = Pedido(pid, cli.id, s.hoy, items)
            facturar_pedido(s, pid, reponer=True)
            pid += 1
        # algunas cobranzas FIFO de facturas ya vencidas o por vencer
        if rng.random() < 0.6:
            cli = s.clientes[rng.randint(1, 26)]
            deuda = s.saldo_cliente(cli.id)
            if deuda > 0:
                monto = deuda if rng.random() < 0.5 else round(deuda * rng.uniform(0.3, 0.9) / 100) * 100
                cob = s.registrar_cobranza(cli.id, s.hoy.isoformat(), rng.choice(["transferencia", "efectivo", "cheque"]),
                                           monto / 100, f"Recibo {rng.randint(1000, 9999)}")
                imputar_fifo(s, cob.id)
    s.hoy = HOY
    s.log.clear()
    return s


def tipo_factura(condicion: str) -> str:
    return "FA" if condicion in ("RI", "MT") else "FB"


def precio_con_descuento(s: Sistema, cli: Cliente, sku: str) -> int:
    p = s.productos[sku]
    if p.categoria == "Ofertas" or not cli.descuento_pct:
        return p.precio
    return round(p.precio * (1 - cli.descuento_pct / 100))


def facturar_pedido(s: Sistema, pedido_id: int, reponer: bool = False):
    """Factura un pedido aplicando las políticas (usado por la historia y por los oráculos)."""
    ped = s.pedidos[pedido_id]
    cli = s.clientes[ped.cliente_id]
    items = [{"sku": it.sku, "cantidad": it.cantidad, "precio_unitario": precio_con_descuento(s, cli, it.sku) / 100}
             for it in ped.items]
    for it in ped.items:  # solo la historia repone stock
        if reponer and s.productos[it.sku].stock < it.cantidad:
            s._mover(it.sku, "ingreso", it.cantidad + 200, "Compra a proveedor")
    return s.emitir_comprobante(tipo_factura(cli.condicion_iva), cli.id, items, pedido_id=pedido_id)


def facturas_impagas(s: Sistema, cliente_id: int):
    return sorted((c for c in s.comprobantes.values()
                   if c.cliente_id == cliente_id and c.es_factura and s.saldo_comprobante(c.id) > 0),
                  key=lambda c: (c.fecha, c.numero))


def imputar_fifo(s: Sistema, cobranza_id: int):
    cob = s.cobranzas[cobranza_id]
    libre = cob.importe - sum(i.importe for i in s.imputaciones if i.cobranza_id == cob.id)
    for f in facturas_impagas(s, cob.cliente_id):
        if libre <= 0:
            break
        a = min(libre, s.saldo_comprobante(f.id))
        s.imputar(cob.id, f.id, a / 100)
        libre -= a


POLITICAS = """MANUAL DE PROCEDIMIENTOS ADMINISTRATIVOS — Distribuidora del Valle
(versión vigente; ante dudas, este manual manda)

1. Tipo de comprobante
   - Clientes Responsables Inscriptos y Monotributistas: Factura A (FA).
   - Consumidores Finales y Exentos: Factura B (FB).
   - Las notas de crédito llevan la misma letra que la factura que corrigen (NCA o NCB).

2. Precios y descuentos
   - Se factura al precio de lista vigente (neto, sin IVA) con el descuento comercial del cliente.
   - El descuento se aplica por unidad y se redondea al centavo.
   - Los productos de la categoría "Ofertas" nunca llevan descuento comercial.

3. Facturación de pedidos
   - Se factura el pedido completo. Si falta stock de cualquier ítem, el pedido NO se factura
     (queda pendiente) y se informa qué producto falta.
   - No se factura a clientes que tengan deuda vencida (facturas vencidas con saldo) ni cuando
     el saldo deudor del cliente más el total del nuevo comprobante supere su límite de crédito.

4. Cobranzas
   - Las transferencias se identifican por el CUIT que figura en la referencia bancaria.
   - Toda cobranza se imputa a las facturas impagas más antiguas primero (por fecha de emisión;
     a igual fecha, por número). Lo que sobre queda a cuenta, sin imputar.
   - Transferencias de un CUIT que no es cliente: no se registran; se informan a administración.
   - La fecha de la cobranza es la fecha de acreditación en el banco.

5. Correcciones
   - Un error en un comprobante se corrige con nota de crédito asociada a ese comprobante.
   - Un error en un movimiento de stock se corrige anulando ese movimiento.
   - No se hacen ajustes manuales de stock ni comprobantes "compensatorios" para tapar diferencias.

6. Devoluciones de mercadería
   - Se emite nota de crédito asociada a la factura original, por las unidades devueltas y al
     mismo precio unitario neto que figura en esa factura.
   - La mercadería devuelta en buen estado vuelve a stock con un movimiento de ingreso cuya
     referencia es el número de la nota de crédito.

7. Lista de precios
   - Los aumentos se aplican sobre el precio neto y se redondean hacia arriba al múltiplo de $10.
   - Los productos con precio congelado por acuerdo comercial no se modifican.

8. Comisiones de vendedores
   - Cada vendedor cobra su porcentaje sobre lo COBRADO en el período a facturas de sus clientes,
     calculado sobre el importe neto de IVA: por cada imputación, importe imputado multiplicado por
     (neto de la factura / total de la factura).
   - Se toman las imputaciones de cobranzas cuya fecha cae dentro del período.
"""
