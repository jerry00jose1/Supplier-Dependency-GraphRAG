MATCH (:GraphSnapshot {id:$snapshot, ready:true})
MATCH (item:Item {snapshot:$snapshot, id:$item_id})<-[:ORDERS]-(line:OrderLine)
      <-[:CONTAINS]-(order:PurchaseOrder)-[:FROM_SUPPLIER]->(supplier:Supplier)
WHERE supplier.id <> $excluded_supplier
  AND ($supplier_filter = '' OR supplier.id = $supplier_filter)
  AND order.status IN ['Closed', 'Completed', 'To Bill'] AND line.received_quantity > 0
  AND (line.snapshot_date IS NULL OR date(order.order_date) <= date(line.snapshot_date))
WITH DISTINCT supplier, item
OPTIONAL MATCH (claim:Claim {snapshot:$snapshot, predicate:'USES_FACILITY', status:'accepted'})
               -[:SUBJECT]->(supplier)
WHERE (claim.valid_from IS NULL OR supplier.snapshot_date IS NULL
       OR date(claim.valid_from) <= date(supplier.snapshot_date))
  AND (properties(claim).valid_to IS NULL OR supplier.snapshot_date IS NULL
       OR date(properties(claim).valid_to) >= date(supplier.snapshot_date))
  AND (NOT EXISTS { MATCH (claim)-[:FOR_ITEM]->() }
       OR EXISTS { MATCH (claim)-[:FOR_ITEM]->(item) })
OPTIONAL MATCH (claim)-[:OBJECT]->(facility:Facility)
OPTIONAL MATCH (passage:Passage)-[:SUPPORTS]->(claim)
WITH supplier, collect(DISTINCT facility.id) AS facilities,
     collect(DISTINCT CASE WHEN claim IS NULL THEN null ELSE
       {claim_id:claim.id, facility_id:facility.id, source:passage.source,
        passage:passage.text, origin:claim.origin, status:claim.status} END) AS evidence
RETURN supplier.id AS supplier_id, supplier.name AS supplier_name, facilities, evidence,
       CASE WHEN $facility_id IN facilities THEN 'shared_affected_facility'
            WHEN size(facilities) = 0 THEN 'facility_unknown'
            ELSE 'other_facility_documented_independence_unconfirmed' END AS assessment
ORDER BY supplier_id
LIMIT $limit
