import networkx as nx
import json
import random
from pathlib import Path

DEFAULT_GRAPH = Path(__file__).resolve().parents[1] / "examples" / "supply_chain_graph.json"

def generate_synthetic_kpis():
    """
    Simulates realistic CRM and ERP data distributions based on your domain knowledge.
    This proves you understand the metrics without violating NDAs.
    """
    return {
        "npm_sales_ytd": round(random.uniform(1000000, 50000000), 2),
        "gross_profit_margin": round(random.uniform(0.05, 0.25), 3),
        "metal_handling_charge": round(random.uniform(0.02, 0.1), 2),
        "fulfillment_rate": round(random.uniform(0.85, 0.99), 3),
        "churn_risk_score": round(random.uniform(0.0, 1.0), 2), # 0 = Safe, 1 = Leaving
        "open_opportunities": random.randint(0, 5),
        "latest_quote_status": random.choice(["Pending", "Accepted", "Negotiating"])
    }

def build_knowledge_graph():
    """
    Constructs a directed knowledge graph mapping materials to customers,
    embedding synthetic commercial KPIs into the edges.
    """
    print("Initializing Supply Chain Knowledge Graph...")
    
    # We use a MultiDiGraph because there can be multiple relationships between nodes
    G = nx.MultiDiGraph()

    # 1. Add Material Nodes
    materials = ["Platinum", "Palladium", "Copper"]
    for mat in materials:
        G.add_node(mat, type="Material", market_volatility=round(random.uniform(0.1, 0.5), 2))
        
    # 2. Add Supplier Node (Your "Company")
    supplier = "PMChem (Internal)"
    G.add_node(supplier, type="Supplier", region="Global")

    # 3. Add Customer Nodes (Mix of real public names and generic types for the PoC)
    customers = {
        "Advanced_Material": {"industry": "Specialty Materials", "tier": 1},
        "AutoCorp_Global": {"industry": "Automotive", "tier": 1},
        "TechSemi_Inc": {"industry": "Semiconductors", "tier": 2}
    }
    
    for cust, attr in customers.items():
        G.add_node(cust, type="Customer", industry=attr["industry"], tier=attr["tier"])

    # 4. Build the Relationships (Edges)
    print("Mapping relationships and injecting synthetic CRM/KPI data...")
    
    # Material -> Customer (What do they buy/consume?)
    G.add_edge("Copper", "Advanced_Material", relation="CONSUMES", dependency_weight=0.8)
    G.add_edge("Platinum", "AutoCorp_Global", relation="CONSUMES", dependency_weight=0.9)
    G.add_edge("Palladium", "TechSemi_Inc", relation="CONSUMES", dependency_weight=0.6)
    #G.add_edge("Rhodium", "TechSemi_Inc", relation="CONSUMES", dependency_weight=0.7)
    
    # Supplier -> Customer (The Commercial Relationship)
    for cust in customers.keys():
        # Generate fake, realistic KPI data for this specific customer
        kpis = generate_synthetic_kpis()
        
        G.add_edge(
            supplier, 
            cust, 
            relation="SUPPLIES",
            npm_sales=kpis["npm_sales_ytd"],
            gross_margin=kpis["gross_profit_margin"],
            metal_handling_charge=kpis["metal_handling_charge"],
            fulfillment_rate=kpis["fulfillment_rate"],
            churn_risk=kpis["churn_risk_score"],
            open_ops=kpis["open_opportunities"],
            quote_status=kpis["latest_quote_status"]
        )

    return G

def export_graph(G, filename=DEFAULT_GRAPH):
    """
    Exports the graph to a JSON format so our Multi-Agent system 
    and GNN can easily read it later.
    """
    data = nx.node_link_data(G)
    with open(filename, 'w') as f:
        json.dump(data, f, indent=4)
        
    print(f"\nSUCCESS: Knowledge Graph built with {G.number_of_nodes()} nodes and {G.number_of_edges()} edges.")
    print(f"Graph data saved locally to {filename}")

if __name__ == "__main__":
    kg = build_knowledge_graph()
    export_graph(kg)