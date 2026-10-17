"""Sistema de gestión de una distribuidora mayorista (estado en memoria).

Todo importe se guarda en centavos (int). Las operaciones de escritura validan
sus entradas y quedan registradas en ``self.log`` para que el corrector pueda
detectar cambios no pedidos.
"""
from __future__ import annotations

import copy
from dataclasses import dataclass, field
from datetime import date, timedelta

CONDICIONES = {"RI": "Responsable Inscripto", "MT": "Monotributista", "CF": "Consumidor Final", "EX": "Exento"}


class ErrorSistema(ValueError):
    """Operación rechazada por el sistema (dato inválido o regla de integridad)."""


def pesos(c: int) -> str:
    s = f"{abs(c) / 100:,.2f}".replace(",", "X").replace(".", ",").replace("X", ".")
    return ("-" if c < 0 else "") + "$ " + s


def a_centavos(v) -> int:
    if isinstance(v, int):
        return v * 100
    if isinstance(v, float):
        return round(v * 100)
    t = str(v).strip().replace("$", "").replace(" ", "")
    if "," in t:
        t = t.replace(".", "").replace(",", ".")
    try:
        return round(float(t) * 100)
    except ValueError as e:
        raise ErrorSistema(f"importe inválido: {v!r}") from e


@dataclass
class Cliente:
    id: int
    razon_social: str
    cuit: str
    condicion_iva: str
    descuento_pct: float
    limite_credito: int
    vendedor: str
    zona: str
    dias_pago: int


@dataclass
class Producto:
    sku: str
    descripcion: str
    categoria: str
    precio: int          # neto sin IVA, por unidad
    alicuota: float      # 21.0 o 10.5
    stock: int
    precio_congelado: bool = False


@dataclass
class ItemPedido:
    sku: str
    cantidad: int


@dataclass
class Pedido:
    id: int
    cliente_id: int
    fecha: date
    items: list[ItemPedido]
    estado: str = "pendiente"      # pendiente | facturado | anulado


@dataclass
class ItemComprobante:
    sku: str
    cantidad: int
    precio_unitario: int           # neto con descuento aplicado
    alicuota: float

    @property
    def neto(self) -> int:
        return self.cantidad * self.precio_unitario

    @property
    def iva(self) -> int:
        return round(self.neto * self.alicuota / 100)


@dataclass
class Comprobante:
    id: int
    tipo: str                      # FA | FB | NCA | NCB
    numero: str
    cliente_id: int
    fecha: date
    vencimiento: date
    items: list[ItemComprobante]
    pedido_id: int | None = None
    asociado_a: int | None = None  # para NC: id de la factura que corrige
    motivo: str = ""

    @property
    def neto(self) -> int:
        return sum(i.neto for i in self.items)

    @property
    def iva(self) -> int:
        return sum(i.iva for i in self.items)

    @property
    def total(self) -> int:
        return self.neto + self.iva

    @property
    def es_factura(self) -> bool:
        return self.tipo in ("FA", "FB")


@dataclass
class Cobranza:
    id: int
    cliente_id: int
    fecha: date
    medio: str
    importe: int
    referencia: str


@dataclass
class Imputacion:
    cobranza_id: int
    comprobante_id: int
    importe: int


@dataclass
class MovimientoStock:
    id: int
    sku: str
    fecha: date
    tipo: str                      # ingreso | egreso | ajuste
    cantidad: int                  # con signo
    referencia: str
    anulado: bool = False


@dataclass
class LineaBanco:
    fecha: date
    concepto: str
    referencia: str
    importe: int


@dataclass
class Sistema:
    hoy: date
    clientes: dict[int, Cliente] = field(default_factory=dict)
    productos: dict[str, Producto] = field(default_factory=dict)
    pedidos: dict[int, Pedido] = field(default_factory=dict)
    comprobantes: dict[int, Comprobante] = field(default_factory=dict)
    cobranzas: dict[int, Cobranza] = field(default_factory=dict)
    imputaciones: list[Imputacion] = field(default_factory=list)
    movimientos: dict[int, MovimientoStock] = field(default_factory=dict)
    banco: list[LineaBanco] = field(default_factory=list)
    comisiones_vendedor: dict[str, float] = field(default_factory=dict)
    log: list[tuple] = field(default_factory=list)
    _sec: dict[str, int] = field(default_factory=lambda: {"comp": 1, "cob": 1, "mov": 1, "FA": 1, "FB": 1, "NCA": 1, "NCB": 1})

    # ---------- utilidades internas ----------
    def clon(self) -> "Sistema":
        return copy.deepcopy(self)

    def _id(self, clave: str) -> int:
        n = self._sec[clave]
        self._sec[clave] = n + 1
        return n

    def _cliente(self, cliente_id) -> Cliente:
        try:
            return self.clientes[int(cliente_id)]
        except (KeyError, ValueError, TypeError) as e:
            raise ErrorSistema(f"cliente inexistente: {cliente_id}") from e

    def _producto(self, sku) -> Producto:
        p = self.productos.get(str(sku).strip().upper())
        if not p:
            raise ErrorSistema(f"producto inexistente: {sku}")
        return p

    def _comprobante(self, comp_id) -> Comprobante:
        try:
            return self.comprobantes[int(comp_id)]
        except (KeyError, ValueError, TypeError) as e:
            raise ErrorSistema(f"comprobante inexistente: {comp_id}") from e

    # ---------- consultas ----------
    def aplicado(self, comp_id: int) -> int:
        """Importe cancelado de un comprobante (cobranzas imputadas + NC asociadas)."""
        c = self.comprobantes[comp_id]
        if not c.es_factura:
            return 0
        cob = sum(i.importe for i in self.imputaciones if i.comprobante_id == comp_id)
        nc = sum(n.total for n in self.comprobantes.values() if n.asociado_a == comp_id and not n.es_factura)
        return cob + nc

    def saldo_comprobante(self, comp_id: int) -> int:
        c = self.comprobantes[comp_id]
        return c.total - self.aplicado(comp_id) if c.es_factura else 0

    def a_cuenta(self, cliente_id: int) -> int:
        """Cobranzas del cliente no imputadas a ningún comprobante."""
        tot = sum(c.importe for c in self.cobranzas.values() if c.cliente_id == cliente_id)
        imp = sum(i.importe for i in self.imputaciones if self.cobranzas[i.cobranza_id].cliente_id == cliente_id)
        return tot - imp

    def saldo_cliente(self, cliente_id: int) -> int:
        deuda = sum(self.saldo_comprobante(c.id) for c in self.comprobantes.values()
                    if c.cliente_id == cliente_id and c.es_factura)
        return deuda - self.a_cuenta(cliente_id)

    def deuda_vencida(self, cliente_id: int) -> int:
        return sum(self.saldo_comprobante(c.id) for c in self.comprobantes.values()
                   if c.cliente_id == cliente_id and c.es_factura and c.vencimiento < self.hoy)

    # ---------- escrituras ----------
    def emitir_comprobante(self, tipo: str, cliente_id, items: list[dict], pedido_id=None,
                           asociado_a=None, motivo: str = "") -> Comprobante:
        tipo = str(tipo).strip().upper()
        if tipo not in ("FA", "FB", "NCA", "NCB"):
            raise ErrorSistema("tipo debe ser FA, FB, NCA o NCB")
        cli = self._cliente(cliente_id)
        if not items:
            raise ErrorSistema("el comprobante no tiene ítems")
        es_nc = tipo.startswith("NC")
        if es_nc:
            if asociado_a is None:
                raise ErrorSistema("una nota de crédito debe indicar la factura que corrige")
            orig = self._comprobante(asociado_a)
            if not orig.es_factura or orig.cliente_id != cli.id:
                raise ErrorSistema("la factura asociada no corresponde a ese cliente")
            if orig.tipo[-1] != tipo[-1]:
                raise ErrorSistema("la letra de la nota de crédito debe coincidir con la de la factura")
        pedido = None
        if pedido_id is not None:
            pedido = self.pedidos.get(int(pedido_id))
            if not pedido or pedido.cliente_id != cli.id:
                raise ErrorSistema("el pedido no existe o no es de ese cliente")
            if pedido.estado != "pendiente":
                raise ErrorSistema(f"el pedido {pedido.id} no está pendiente")
        lineas = []
        for it in items:
            p = self._producto(it.get("sku"))
            cant = int(it.get("cantidad", 0))
            if cant <= 0:
                raise ErrorSistema("cantidad debe ser positiva")
            pu = a_centavos(it["precio_unitario"]) if it.get("precio_unitario") is not None else p.precio
            if pu <= 0:
                raise ErrorSistema("precio unitario inválido")
            if not es_nc and p.stock < cant:
                raise ErrorSistema(f"stock insuficiente de {p.sku}: hay {p.stock}")
            lineas.append(ItemComprobante(p.sku, cant, pu, p.alicuota))
        cid = self._id("comp")
        numero = f"{tipo} 0003-{self._id(tipo):08d}"
        comp = Comprobante(cid, tipo, numero, cli.id, self.hoy, self.hoy + timedelta(days=cli.dias_pago if not es_nc else 0),
                           lineas, pedido.id if pedido else None, int(asociado_a) if es_nc else None, motivo)
        self.comprobantes[cid] = comp
        if not es_nc:
            for l in lineas:
                self._mover(l.sku, "egreso", -l.cantidad, comp.numero)
        if pedido:
            pedido.estado = "facturado"
        self.log.append(("emitir_comprobante", cid))
        return comp

    def _mover(self, sku: str, tipo: str, cantidad: int, referencia: str) -> MovimientoStock:
        p = self._producto(sku)
        mid = self._id("mov")
        m = MovimientoStock(mid, p.sku, self.hoy, tipo, cantidad, referencia)
        self.movimientos[mid] = m
        p.stock += cantidad
        return m

    def registrar_movimiento(self, sku, tipo: str, cantidad: int, referencia: str) -> MovimientoStock:
        tipo = str(tipo).strip().lower()
        if tipo not in ("ingreso", "egreso", "ajuste"):
            raise ErrorSistema("tipo debe ser ingreso, egreso o ajuste")
        cantidad = int(cantidad)
        if tipo == "ingreso" and cantidad <= 0 or tipo == "egreso" and cantidad >= 0 or cantidad == 0:
            raise ErrorSistema("ingreso va con cantidad positiva y egreso con cantidad negativa")
        m = self._mover(sku, tipo, cantidad, str(referencia))
        self.log.append(("registrar_movimiento", m.id))
        return m

    def anular_movimiento(self, mov_id) -> MovimientoStock:
        m = self.movimientos.get(int(mov_id))
        if not m:
            raise ErrorSistema(f"movimiento inexistente: {mov_id}")
        if m.anulado:
            raise ErrorSistema("el movimiento ya está anulado")
        m.anulado = True
        self.productos[m.sku].stock -= m.cantidad
        self.log.append(("anular_movimiento", m.id))
        return m

    def registrar_cobranza(self, cliente_id, fecha: str, medio: str, importe, referencia: str) -> Cobranza:
        cli = self._cliente(cliente_id)
        medio = str(medio).strip().lower()
        if medio not in ("transferencia", "efectivo", "cheque"):
            raise ErrorSistema("medio debe ser transferencia, efectivo o cheque")
        imp = a_centavos(importe)
        if imp <= 0:
            raise ErrorSistema("el importe debe ser positivo")
        try:
            f = date.fromisoformat(str(fecha))
        except ValueError as e:
            raise ErrorSistema("fecha en formato AAAA-MM-DD") from e
        cid = self._id("cob")
        c = Cobranza(cid, cli.id, f, medio, imp, str(referencia))
        self.cobranzas[cid] = c
        self.log.append(("registrar_cobranza", cid))
        return c

    def imputar(self, cobranza_id, comprobante_id, importe) -> Imputacion:
        try:
            cob = self.cobranzas[int(cobranza_id)]
        except (KeyError, ValueError, TypeError) as e:
            raise ErrorSistema(f"cobranza inexistente: {cobranza_id}") from e
        comp = self._comprobante(comprobante_id)
        if not comp.es_factura or comp.cliente_id != cob.cliente_id:
            raise ErrorSistema("solo se imputa a facturas del mismo cliente")
        imp = a_centavos(importe)
        libre = cob.importe - sum(i.importe for i in self.imputaciones if i.cobranza_id == cob.id)
        if imp <= 0 or imp > libre:
            raise ErrorSistema(f"importe a imputar inválido; disponible en la cobranza: {pesos(libre)}")
        if imp > self.saldo_comprobante(comp.id):
            raise ErrorSistema(f"supera el saldo del comprobante ({pesos(self.saldo_comprobante(comp.id))})")
        i = Imputacion(cob.id, comp.id, imp)
        self.imputaciones.append(i)
        self.log.append(("imputar", cob.id, comp.id))
        return i

    def actualizar_precio(self, sku, nuevo_precio) -> Producto:
        p = self._producto(sku)
        v = a_centavos(nuevo_precio)
        if v <= 0:
            raise ErrorSistema("precio inválido")
        p.precio = v
        self.log.append(("actualizar_precio", p.sku))
        return p
