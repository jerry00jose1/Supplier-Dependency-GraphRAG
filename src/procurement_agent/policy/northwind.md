# Northwind Components purchasing policy

## Direct graph ordering

An explicit complete purchase request creates an order directly in Neo4j. There are no drafts or approval buttons in this test application. Ask for missing supplier, item, quantity or delivery details. Recording an order does not send it to a supplier. Neo4j is authoritative; ERPNext is not used.

## Recorded prices

A price copied from an earlier purchase order is a historical estimate. It is not a current quotation, and it does not reserve stock or promise a delivery date.

## Invoice match

A supplier invoice must name the purchase order it bills. The billed quantity must match the quantity received. The billed unit price must match the purchase-order price. A difference is an exception. Finding an exception does not create a new purchase order.

## Supplier eligibility

Use a supplier that has already sold the requested item. A company with no purchase history for that item is not eligible. When the buyer excludes a supplier, remove that supplier before any recommendation.
