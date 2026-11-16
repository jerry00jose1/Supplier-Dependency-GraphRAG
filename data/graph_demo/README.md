# Synthetic dependency scenario

Every file in this directory is authored demo data, dated 2026-11-15. The loader
adds it to a projection of the Mizan HDPE-pellet (`M01-01`) purchasing history.
Mizan itself is synthetic data from findi360; retain its CC BY 4.0 attribution.

| Role | Identifier | Data origin |
|---|---|---|
| A: affected supplier | SUP0065 | Mizan identity/history; synthetic facility claim |
| B: another documented facility | SUP0066 | Mizan identity/history; synthetic facility claim |
| C: shared affected facility | SUP0067 | Mizan identity/history; synthetic facility claim |
| D: unknown facility | DEMO-SUP-D | Entirely synthetic identity/history |
| Facilities X and Y | FAC-X, FAC-Y | Synthetic |
| Open purchase | DEMO-PO-001 | Synthetic, two lines of HDPE in kg |
| Disruption | shutdown-x | Synthetic November 16–30 shutdown |

The synthetic open order and D's historical order are declared in the demo builder
alongside the parameters in `scenario.json`. They use EUR 12/kg for illustration;
this is not a quotation or a currency conversion of Mizan prices. Mizan's raw
prices retain their USD denomination and closed status. Only the selected item's
lines are projected, so historical order nodes may represent partial orders.

Profiles and notice text are mapped manually to accepted fixture claims, each
linked to its full supporting passage. No automated document extraction or
external verification has occurred. D deliberately has no facility evidence.

The demo's snapshot date is fixed, not the machine's current date. Facilities and
open orders have not been written into ERPNext. See [run instructions](../../docs/graph-demo.md).
