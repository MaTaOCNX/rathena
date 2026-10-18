"""Builds the cash shop search index for the web UI.

Reads the server's item and cash shop databases (renewal + import) and writes
one JSON entry per equipment item sold in the cash shop.

Usage: python build_items.py <rathena root> <output.json>
"""
import json
import os
import re
import sys

ITEM_DBS = ["db/re/item_db_equip.yml", "db/import/item_db.yml"]
CASH_DBS = ["db/re/item_cash_db.txt", "db/import/item_cash_db.txt"]
EQUIP_TYPES = {"Armor", "Weapon", "ShadowGear"}

SCALARS = {
    "AegisName": str, "Name": str, "Type": str, "SubType": str,
    "Weight": int, "Attack": int, "MagicAttack": int, "Defense": int,
    "Slots": int, "EquipLevelMin": int, "WeaponLevel": int, "Refineable": str,
}

# rAthena location -> (category used by the filter, label shown in the UI)
LOCATIONS = {
    "Head_Top": ("headgear", "Upper Headgear"),
    "Head_Mid": ("headgear", "Middle Headgear"),
    "Head_Low": ("headgear", "Lower Headgear"),
    "Armor": ("armor", "Armor"),
    "Right_Hand": ("weapon", "Weapon"),
    "Both_Hand": ("weapon", "Two-Handed Weapon"),
    "Left_Hand": ("shield", "Shield"),
    "Garment": ("garment", "Garment"),
    "Shoes": ("shoes", "Shoes"),
    "Right_Accessory": ("accessory", "Accessory"),
    "Left_Accessory": ("accessory", "Accessory"),
    "Both_Accessory": ("accessory", "Accessory"),
    "Costume_Head_Top": ("costume", "Costume Upper"),
    "Costume_Head_Mid": ("costume", "Costume Middle"),
    "Costume_Head_Low": ("costume", "Costume Lower"),
    "Costume_Garment": ("costume", "Costume Garment"),
    "Shadow_Armor": ("shadow", "Shadow Armor"),
    "Shadow_Weapon": ("shadow", "Shadow Weapon"),
    "Shadow_Shield": ("shadow", "Shadow Shield"),
    "Shadow_Shoes": ("shadow", "Shadow Shoes"),
    "Shadow_Right_Accessory": ("shadow", "Shadow Earring"),
    "Shadow_Left_Accessory": ("shadow", "Shadow Pendant"),
}


def read_items(root):
    """Returns {id: fields}. Import entries override individual fields."""
    items = {}
    for rel in ITEM_DBS:
        path = os.path.join(root, rel)
        if not os.path.exists(path):
            continue
        cur = None
        in_locations = False
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                m = re.match(r"^  - Id: (\d+)", line)
                if m:
                    cur = items.setdefault(int(m.group(1)), {})
                    in_locations = False
                    continue
                if cur is None:
                    continue
                if in_locations:
                    m = re.match(r"^      (\w+): (true|false)", line)
                    if m:
                        if m.group(2) == "true":
                            cur["Locations"].append(m.group(1))
                        continue
                    in_locations = False
                if re.match(r"^    Locations:\s*$", line):
                    cur["Locations"] = []
                    in_locations = True
                    continue
                m = re.match(r"^    (\w+): (.*)$", line)
                if m and m.group(1) in SCALARS:
                    value = m.group(2).strip().strip('"')
                    try:
                        cur[m.group(1)] = SCALARS[m.group(1)](value)
                    except ValueError:
                        pass
    return items


def read_cash_shop(root):
    """Returns {(tab, id): price}; later files override earlier ones like the server does."""
    shop = {}
    for rel in CASH_DBS:
        path = os.path.join(root, rel)
        if not os.path.exists(path):
            continue
        with open(path, encoding="utf-8", errors="replace") as f:
            for line in f:
                line = line.split("//", 1)[0].strip()
                m = re.match(r"^(\d+),(\d+),(\d+)", line)
                if m:
                    shop[(int(m.group(1)), int(m.group(2)))] = int(m.group(3))
    return shop


def main():
    root, out = sys.argv[1], sys.argv[2]
    items = read_items(root)
    entries = []
    for (tab, item_id), price in sorted(read_cash_shop(root).items()):
        it = items.get(item_id)
        if not it or it.get("Type") not in EQUIP_TYPES or price < 1:
            continue
        locations = it.get("Locations", [])
        categories = sorted({LOCATIONS[l][0] for l in locations if l in LOCATIONS})
        labels = []
        for l in locations:
            if l in LOCATIONS and LOCATIONS[l][1] not in labels:
                labels.append(LOCATIONS[l][1])
        entries.append({
            "id": item_id,
            "aegis": it.get("AegisName", ""),
            "name": it.get("Name", it.get("AegisName", str(item_id))),
            "type": it.get("Type"),
            "subtype": it.get("SubType", ""),
            "categories": categories,
            "slot_label": " / ".join(labels),
            "slots": it.get("Slots", 0),
            "weight": it.get("Weight", 0) / 10,
            "attack": it.get("Attack", 0),
            "matk": it.get("MagicAttack", 0),
            "defense": it.get("Defense", 0),
            "level": it.get("EquipLevelMin", 0),
            "tab": tab,
            "price": price,
        })
    with open(out, "w", encoding="utf-8") as f:
        json.dump(entries, f, separators=(",", ":"))
    print(f"wrote {len(entries)} cash shop equipment entries to {out}")


if __name__ == "__main__":
    main()
