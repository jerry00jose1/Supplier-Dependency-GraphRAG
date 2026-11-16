// All potentially exposed open lines; outside-period lines remain uncertain.
MATCH (:GraphSnapshot {id:$snapshot, ready:true})
MATCH (notice:GraphEntity:Claim {snapshot:$snapshot, id:$notice_id,
       predicate:'DISRUPTION', status:'accepted'})-[:SUBJECT]->(facility:Facility)
MATCH (noticePassage:Passage)-[:SUPPORTS]->(notice)
MATCH (claim:Claim {snapshot:$snapshot, predicate:'USES_FACILITY', status:'accepted'})
      -[:OBJECT]->(facility)
MATCH (claim)-[:SUBJECT]->(supplier:Supplier)
MATCH (passage:Passage)-[:SUPPORTS]->(claim)
MATCH (order:PurchaseOrder {snapshot:$snapshot, status:'Open'})-[:FROM_SUPPLIER]->(supplier)
MATCH (order)-[:CONTAINS]->(line:OrderLine)-[:ORDERS]->(item:Item)
OPTIONAL MATCH (line)-[:CHARGED_TO]->(centre:CostCentre)
WHERE line.quantity > line.received_quantity
  AND ($supplier_filter = '' OR supplier.id = $supplier_filter)
  AND ($item_filter = '' OR item.id = $item_filter)
  AND (claim.valid_from IS NULL OR date(claim.valid_from) <= date(notice.end))
  AND (properties(claim).valid_to IS NULL
       OR date(properties(claim).valid_to) >= date(notice.start))
  AND (NOT EXISTS { MATCH (claim)-[:FOR_ITEM]->() }
       OR EXISTS { MATCH (claim)-[:FOR_ITEM]->(item) })
WITH *, date(line.need_by) >= date(notice.start)
        AND date(line.need_by) <= date(notice.end) AS within_period
RETURN line.id AS line_id, order.id AS order_id, supplier.id AS supplier_id,
       item.id AS item_id, coalesce(centre.name, line.warehouse, 'Stores - NC') AS cost_centre,
       line.quantity-line.received_quantity AS outstanding_quantity,
       line.uom AS uom, line.need_by AS need_by, within_period,
       CASE WHEN within_period THEN 'potential_exposure_in_notice_period'
            ELSE 'outside_notice_period_impact_unconfirmed' END AS assessment,
       [order.id, line.id, item.id, supplier.id, claim.id, facility.id, notice.id] AS evidence_ids,
       line.source AS record_source, line.origin AS order_origin,
       passage.source AS dependency_source, passage.text AS dependency_passage,
       noticePassage.source AS notice_source, noticePassage.text AS notice_passage,
       coalesce(line.snapshot_date, order.order_date) AS snapshot_date
ORDER BY within_period DESC, need_by, line_id
LIMIT $limit
