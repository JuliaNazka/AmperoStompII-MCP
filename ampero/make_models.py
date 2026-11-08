"""Gera models.json (enxuto) a partir do banco do editor oficial Ampero II, que precisa estar instalado.

uso: python make_models.py [caminho do v1.0.8_alg_data.json]
O models.json não vai para o repositório: os dados são da Hotone.
"""
import json
import os
import sys

SRC = r"C:\Program Files\Hotone\Ampero II\data\flutter_assets\assets\data\v1.0.8_alg_data.json"
src = sys.argv[1] if len(sys.argv) > 1 else SRC
dst = os.path.join(os.path.dirname(os.path.abspath(__file__)), "models.json")
cat = json.load(open(src, encoding="utf-8"))["PluginProperties"]["Modules"]["Catalog"]
modules, models = [], []
for c in cat:
    mi = int(c["Index"])
    modules.append({"index": mi, "name": c["Name"]})
    for a in c.get("Alg", []):
        ws = a.get("Widget", [])
        ws = [ws] if isinstance(ws, dict) else ws
        models.append({
            "module": mi, "name": a["Name"], "code": int(a["Code"]), "classify": a.get("classify", ""),
            "params": [{"name": w["Name"], "id": int(w["ID"]), "min": float(w["DisplayMin"]),
                        "max": float(w["DisplayMax"]), "type": int(w["Type"]), "default": float(w["DefaultValue"])}
                       for w in ws if isinstance(w, dict)],
        })
json.dump({"source": f"Ampero II editor, {os.path.basename(src)}", "modules": modules, "models": models},
          open(dst, "w", encoding="utf-8"), ensure_ascii=False)
print(len(modules), "módulos", len(models), "modelos ->", dst)
