from __future__ import annotations

import argparse
import json
from datetime import date
from pathlib import Path
from xml.etree.ElementTree import iterparse


def normalized(value: str) -> str:
    return " ".join(value.casefold().replace("ё", "е").split())


def read_cities(xml_files: list[Path]) -> list[dict[str, str | None]]:
    cities: dict[str, dict[str, str | None]] = {}
    for xml_file in xml_files:
        for _, element in iterparse(xml_file, events=("end",)):
            attributes = element.attrib
            if (
                attributes.get("ISACTUAL") == "1"
                and attributes.get("ISACTIVE") == "1"
                and attributes.get("TYPENAME", "").casefold() in {"г", "город"}
            ):
                name = attributes.get("NAME", "").strip()
                if name:
                    cities.setdefault(
                        normalized(name),
                        {"name": name, "fias_id": attributes.get("OBJECTGUID")},
                    )
            element.clear()
    return sorted(cities.values(), key=lambda item: normalized(str(item["name"])))


def main() -> None:
    parser = argparse.ArgumentParser(
        description="Build the bundled unique-city catalog from extracted GAR AS_ADDR_OBJ XML files."
    )
    parser.add_argument("input", type=Path, help="GAR directory or one AS_ADDR_OBJ XML file")
    parser.add_argument(
        "--output",
        type=Path,
        default=Path("src/vk_chat_bot/data/cities.json"),
    )
    args = parser.parse_args()
    xml_files = (
        sorted(args.input.rglob("AS_ADDR_OBJ_*.XML")) if args.input.is_dir() else [args.input]
    )
    if not xml_files:
        raise SystemExit("No AS_ADDR_OBJ XML files found")
    cities = read_cities(xml_files)
    payload = {
        "version": date.today().isoformat(),
        "source": "State Address Register (GAR/FIAS)",
        "upstream": "https://fias.nalog.ru/",
        "cities": cities,
    }
    args.output.parent.mkdir(parents=True, exist_ok=True)
    args.output.write_text(
        json.dumps(payload, ensure_ascii=False, indent=2) + "\n", encoding="utf-8"
    )
    print(f"Wrote {len(cities)} unique city names to {args.output}")


if __name__ == "__main__":
    main()
