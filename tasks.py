"""Lista de tareas: 8 familias x 12 semillas = 96 tareas."""
from env import (comisiones, decision_credito, devolucion, doble_factura, facturar_pendientes, imputar_transferencias,
                 lista_precios, stock_negativo)

FAMILIAS = (facturar_pendientes, imputar_transferencias, doble_factura, stock_negativo, lista_precios, devolucion,
            decision_credito, comisiones)

tasks = [f(semilla=s) for f in FAMILIAS for s in range(1, 13)]
