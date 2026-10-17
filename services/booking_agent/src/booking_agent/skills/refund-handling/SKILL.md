---
name: refund-handling
description: Procedimiento para gestionar una solicitud de reembolso de un pedido del cliente.
---
# Gestión de reembolsos

1. Identifica el pedido. Si el cliente no da el ID, usa list_my_orders y confirma cuál es.
2. Comprueba el pedido con get_order: solo los pedidos en estado `paid` pueden reembolsarse.
3. Si el cliente solo pregunta si "se puede", explica la política (search_knowledge con
   doc_type=policy) y NO solicites el reembolso.
4. Si lo pide de forma explícita, llama a request_refund con un motivo de una frase.
5. Comunica la decisión tal cual la devuelve la tool (`approved`, `amount`, `decision_reason`).
   No prometas nada distinto ni sugieras vías para saltarse la política.
   Si devuelve `approval_required`, la solicitud queda pendiente de revisión por una persona:
   dilo así, da la referencia y no la repitas ni anticipes el resultado.
6. Si request_refund devuelve un resultado incierto (timeout/unavailable), no lo repitas: indica
   que se verificará y que recibirá confirmación.
