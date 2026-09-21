"""A worked example for the "try it yourself" form.

An empty textarea is a wall. This pair is realistic — the shapes, labels and
punctuation come from the corpus — and carries ONE planted defect, so a visitor's
first click finds something instead of returning OK and looking broken.

The defect is a discharge-port change, which is the expensive one in practice: a
container that sails to the wrong port is slow and costly to unwind once the vessel
has left. The shipper is spelled with different legal-suffix punctuation on the two
documents (`SDN BHD` / `SDN. BHD.`) so the visitor can also see the system NOT
flagging something that merely looks different.
"""
from __future__ import annotations

SAMPLE = {
    "subject": "DRAFT BL CHECK - 5RFR-88214 - HAMBURG - MEGA RUBBER GLOVE SDN BHD",
    "body": (
        "Hi Ooi,\n\n"
        "Please find attached our shipping instruction and the carrier's draft "
        "bill of lading for booking 5RFR-88214.\n\n"
        "Kindly compare the two and confirm the discrepancies before the vessel "
        "cut-off on Friday.\n\n"
        "Best regards,\nWilly"
    ),
    "si_text": (
        "SHIPPING INSTRUCTION\n"
        "========================================\n"
        "Shipper: MEGA RUBBER GLOVE SDN BHD\n"
        "  LOT 5, JALAN PERUSAHAAN, 81700 PASIR GUDANG, JOHOR, MALAYSIA\n"
        "Consignee: NORDIC HEALTHCARE SERVICES GMBH\n"
        "  HAFENSTRASSE 42, 20457 HAMBURG, GERMANY\n"
        "Notify Party: SAME AS CONSIGNEE\n"
        "Port of Loading: PORT KLANG (MYPKG)\n"
        "Port of Discharge: HAMBURG, GERMANY (DEHAM)\n"
        "No. of Containers: 2 x 40'HC\n"
        "GROSS WEIGHT: 22,450.50 KGS\n"
    ),
    "bl_text": (
        "BILL OF LADING (DRAFT)\n"
        "========================================\n"
        "Shipper: MEGA RUBBER GLOVE SDN. BHD.\n"
        "  LOT 5, JALAN PERUSAHAAN, 81700 PASIR GUDANG, JOHOR, MALAYSIA\n"
        "Consignee: NORDIC HEALTHCARE SERVICES GMBH\n"
        "  HAFENSTRASSE 42, 20457 HAMBURG, GERMANY\n"
        "NOTIFY PARTY: NORDIC HEALTHCARE SERVICES GMBH\n"
        "POL: PORT KLANG (MYPKG)\n"
        "Port of Discharge: ROTTERDAM, NETHERLANDS (DEHAM)\n"
        "Container Count: 2 x 40'HC\n"
        "Gross Weight (KG): 22.450,50 KGS\n"
    ),
    "expect": [
        "port_of_discharge is a DEFECT: HAMBURG vs ROTTERDAM — and note both "
        "documents still carry the same stale code (DEHAM), which is exactly why "
        "port-code resolution is switched off.",
        "shipper MATCHES despite 'SDN BHD' vs 'SDN. BHD.' — the same legal suffix, "
        "differently punctuated.",
        "notify_party MATCHES: the SI says 'SAME AS CONSIGNEE', which is resolved "
        "against that document's own consignee and labelled as inferred.",
        "gross_weight_kg MATCHES: '22,450.50' and '22.450,50' are the same number "
        "written with US and European separators.",
    ],
}

SAMPLE2 = {
    "subject": "DRAFT BL CHK - 5RFR-88214 - RTM/HAM - MEGA RUBBER GLOVE",
    "body": (
        "Hi Team,\n\n"
        "Pls chk below SI and arrange accordingly.\n\n"
        "Old docs show RTM, but current dest should be HAM.\n"
        "Kindly use the latest SI as ref. Don't follow prev BL.\n\n"
        "Tks."
    ),
    "si_text": (
        "SI\n"
        "========================================\n"
        "Bk No: BK-260921\n\n"
        "SHP: MEGA RUBBER GLOVE SDN. BHD.\n"
        "LOT 5, JALAN PERUSAHAAN, 81700 PASIR GUDANG, JOHOR, MY\n\n"
        "CNEE: NORDIC HEALTHCARE SERVICES GMBH\n"
        "HAFENSTRASSE 42, 20457 HAMBURG, DE\n\n"
        "N/P: NORDIC HEALTHCARE SERVICES GMBH\n\n"
        "POL: PKG\n"
        "POD: HAM\n"
        "FD: HAM\n\n"
        "Ctr: 2 x 40HC\n"
        "Qty: 500,000 BOX\n"
        "G/W: 22,450.50 KGS\n"
        "N/W: 20,800.00 KGS\n"
        "Meas: 68.50 CBM\n\n"
        "Note:\n"
        "POD = HAM.\n"
        "Ignore prev doc showing RTM."
    ),
    "bl_text": (
        "B/L DRAFT\n"
        "========================================\n"
        "SHP: MEGA RUBBER GLOVE SDN. BHD.\n"
        "LOT 5, JALAN PERUSAHAAN, 81700 PASIR GUDANG, JOHOR, MY\n\n"
        "CNEE: NORDIC HEALTHCARE SERVICES GMBH\n"
        "HAFENSTRASSE 42, 20457 HAMBURG, DE\n\n"
        "N/P: NORDIC HEALTHCARE SERVICES GMBH\n\n"
        "POL: PKG\n"
        "POD: RTM\n"
        "FD: HAM\n\n"
        "Ctr: 2 x 40HC\n"
        "G/W: 22,450.50 KGS\n\n"
        "Cargo:\n"
        "NITRILE EXAM GLOVES\n"
        "500K BOX"
    ),
    "expect": [
        "This sample is packed with completely unknown, heavily abbreviated labels: "
        "SHP, CNEE, N/P, Ctr, Qty, Meas, G/W.",
        "Without the AI label mapping feature, this produces many 'CANNOT_DETERMINE' "
        "results because it cannot extract the fields.",
        "With the AI feature on, it will propose mapping SHP to shipper, CNEE to "
        "consignee, etc., allowing the system to instantly learn the new format."
    ],
}
