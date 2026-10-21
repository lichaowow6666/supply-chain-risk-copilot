# =====================================================================
# stress_test_graph.py
# Network Graph Contagion Engine
# =====================================================================

import json
import os
from pathlib import Path

DEFAULT_GRAPH = Path(__file__).resolve().parents[2] / "examples" / "supply_chain_graph.json"
from typing import Dict, Any

def run_stress_test(target_commodity: str, shock_pct: float, json_file: str = str(DEFAULT_GRAPH)) -> str:
    """
    Executes a network contagion stress test on the Knowledge Graph.
    
    :param target_commodity: Commodity symbol (e.g., 'Copper', 'Palladium', 'Platinum')
    :param shock_pct: Price shock in decimal form (e.g., 0.20 for 20%)
    :param json_file: Path to the JSON graph representation
    """
    if not os.path.exists(json_file):
        return f"[GraphAgent Error] Supply chain graph file '{json_file}' not found. Run python -m scripts.build_graph first."

    with open(json_file, 'r') as f:
        graph_data = json.load(f)

    nodes = {node["id"]: node for node in graph_data.get("nodes", [])}
    edges = graph_data.get("edges", [])

    report = []
    report.append(f"=== KNOWLEDGE GRAPH CONTAGION SIMULATION ===")
    report.append(f"Initial Shock Trigger: {target_commodity.upper()} Price Increase of +{round(shock_pct * 100, 2)}%\n")

    impacted_entities = []

    for edge in edges:
        source_id = edge.get("source")
        target_id = edge.get("target")
        relation = edge.get("relation", "")
        mhc_pct = edge.get("metal_handling_charge_pct", 0.10) # 10% percentage charge as updated
        dependency_weight = edge.get("dependency_weight", 0.50)

        # Check if the edge connects to our target commodity
        if source_id.lower() == target_commodity.lower() or target_id.lower() == target_commodity.lower():
            affected_company_id = target_id if source_id.lower() == target_commodity.lower() else source_id
            company_node = nodes.get(affected_company_id, {"label": affected_company_id, "type": "Company"})
            
            # Mathematical Contagion Calculation:
            # Margin Impact = Shock % * Dependency Weight * (1 + Metal Handling Charge %)
            effective_cost_increase = shock_pct * dependency_weight * (1.0 + mhc_pct)
            ebitda_margin_compression = round(effective_cost_increase * 100, 2)

            impacted_entities.append({
                "company": company_node.get("label", affected_company_id),
                "dependency_weight": dependency_weight,
                "mhc_pct": mhc_pct,
                "margin_compression": ebitda_margin_compression
            })

    if impacted_entities:
        report.append("--- DIRECT SUPPLY CHAIN IMPACTS ---")
        for item in impacted_entities:
            report.append(f"• Company: {item['company']}")
            report.append(f"  - Commodity Dependency Weight: {item['dependency_weight']}")
            report.append(f"  - Metal Handling Charge (MHC): {int(item['mhc_pct'] * 100)}% of charge")
            report.append(f"  - Projected EBITDA Margin Compression: -{item['margin_compression']}%\n")
    else:
        report.append(f"No direct graph edges found connecting '{target_commodity}' to downstream companies in {json_file}.")

    report.append("=========================================================")
    return "\n".join(report)

if __name__ == "__main__":
    # Standalone Test
    print(run_stress_test("Copper", 0.20))