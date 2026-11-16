MATCH (:GraphSnapshot {id:$snapshot, ready:true})
MATCH (item:Item {snapshot:$snapshot, id:$item_id})<-[:ORDERS]-(line:OrderLine)
      <-[:CONTAINS]-(order:PurchaseOrder)-[:FROM_SUPPLIER]->(supplier:Supplier)
WHERE supplier.id <> $excluded_supplier
  AND order.status = 'Closed' AND line.received_quantity > 0
  AND date(order.order_date) <= date(line.snapshot_date)
WITH supplier, order, line ORDER BY supplier.id, line.id
RETURN supplier.id AS supplier_id, supplier.name AS supplier_name,
       supplier.origin AS supplier_origin, count(DISTINCT line) AS historical_lines,
       collect(DISTINCT {order_id:order.id, line_id:line.id, source:line.source,
                         origin:line.origin, currency:order.currency,
                         unit_price:line.unit_price, uom:line.uom}) AS history,
       'historical_supplier_capacity_and_qualification_unconfirmed' AS assessment
ORDER BY supplier_id
LIMIT $limit
