#!/usr/bin/env python3
"""Auditoría y Linter Automatizado de Bounded Context y Cohesión de Servicios.

Detecta antipatrones de "God Service" y clases multidominio en Backend y Frontend.
Inspecciona packages, clases/objetos, métodos y endpoints consumidos.
"""

from __future__ import annotations

import argparse
import json
import os
import re
import sys
from collections import defaultdict
from pathlib import Path

ROOT = Path.cwd()


class TaxonomyEngine:
    """Motor de taxonomía dinámico cargado directamente desde aurea-docs."""

    def __init__(self):
        self.sections: dict[str, dict] = {}
        self.areas: dict[str, dict] = {}
        self.page_to_section: dict[str, tuple[str, str]] = {}
        self.module_to_section: dict[str, tuple[str, str]] = {}
        self.load()

    def _find_file(self, filename: str) -> Path | None:
        candidates = [
            ROOT / "docs" / "modules-dynamic" / "taxonomy" / filename,
            ROOT / ".aurea-docs" / "docs" / "modules-dynamic" / "taxonomy" / filename,
            ROOT.parent / "aurea-docs" / "docs" / "modules-dynamic" / "taxonomy" / filename,
            Path(__file__).resolve().parent.parent.parent / "aurea-docs" / "docs" / "modules-dynamic" / "taxonomy" / filename,
        ]
        for c in candidates:
            if c.exists():
                return c
        return None

    def load(self):
        structure_file = self._find_file("structure.json")
        area_file = self._find_file("area.json")

        if structure_file:
            try:
                data = json.loads(structure_file.read_text(encoding="utf-8"))
                self.sections = data.get("sections", {})
                for sec_key, sec_val in self.sections.items():
                    for page_key, page_val in sec_val.get("pages", {}).items():
                        domain_key = f"{sec_key}.{page_key}"
                        self.page_to_section[page_key] = (sec_key, domain_key)
                        for mod_key in page_val.get("modules", []):
                            self.module_to_section[mod_key] = (sec_key, domain_key)
            except Exception as exc:
                print(f"⚠️ Error cargando structure.json: {exc}", file=sys.stderr)

        if area_file:
            try:
                data = json.loads(area_file.read_text(encoding="utf-8"))
                self.areas = data.get("areas", {})
            except Exception as exc:
                print(f"⚠️ Error cargando area.json: {exc}", file=sys.stderr)

    def resolve(self, endpoint: str) -> tuple[str, str]:
        clean_ep = endpoint.split("?")[0].strip("/")
        parts = clean_ep.split("/")

        # 1. Si la ruta pertenece al scope /auth o a un área transversal directa de area.json
        if parts and parts[0] in self.areas:
            return parts[0], parts[0]

        # 2. Si la ruta pertenece al scope /tenant (administración nuclear de core)
        if parts and parts[0] == "tenant":
            for part in reversed(parts[1:]):
                if part in self.page_to_section and self.page_to_section[part][0] == "core":
                    return self.page_to_section[part]
                if part in self.module_to_section and self.module_to_section[part][0] == "core":
                    return self.module_to_section[part]
            sub = parts[1] if len(parts) > 1 else "tenant"
            return "core", f"core.{sub}"

        # 3. Si el prefijo coincide con una sección canónica de structure.json (ej: /commerce/orders)
        if parts and parts[0] in self.sections:
            sec = parts[0]
            if len(parts) > 1 and parts[1] in self.page_to_section and self.page_to_section[parts[1]][0] == sec:
                return self.page_to_section[parts[1]]
            sub = parts[1] if len(parts) > 1 else sec
            return sec, f"{sec}.{sub}"

        # 4. Si los segmentos coinciden con una página canónica de structure.json (de izquierda a derecha para priorizar el recurso raíz)
        for part in parts:
            if part in self.page_to_section:
                return self.page_to_section[part]

        # 5. Si los segmentos coinciden con un módulo canónico de structure.json (de izquierda a derecha)
        for part in parts:
            if part in self.module_to_section:
                return self.module_to_section[part]

        # 6. Chequear áreas compuestas de area.json (ej: platform.tenants, platform.plans)
        for part in parts:
            for area_key in self.areas:
                if part in area_key.split("."):
                    return area_key.split(".")[0], area_key

        return "unknown", f"unknown({clean_ep})"


TAXONOMY = TaxonomyEngine()


def resolve_endpoint_domain(endpoint: str) -> tuple[str, str]:
    return TAXONOMY.resolve(endpoint)


class MethodInfo:
    def __init__(self, name: str, line_no: int):
        self.name = name
        self.line_no = line_no
        self.endpoints: list[tuple[str, str, str]] = []  # (http_method, path, domain)

    def add_call(self, http_method: str, path: str):
        section, domain = resolve_endpoint_domain(path)
        self.endpoints.append((http_method.upper(), path, domain))


class EntityInfo:
    def __init__(self, name: str, kind: str, file_path: Path):
        self.name = name
        self.kind = kind  # 'class' o 'object'
        self.file_path = file_path
        self.methods: dict[str, MethodInfo] = {}

    def get_or_create_method(self, name: str, line_no: int) -> MethodInfo:
        if name not in self.methods:
            self.methods[name] = MethodInfo(name, line_no)
        return self.methods[name]

    @property
    def detected_sections(self) -> set[str]:
        sections = set()
        for m in self.methods.values():
            for _, path, _ in m.endpoints:
                sec, _ = resolve_endpoint_domain(path)
                if sec not in ("unknown", "auth"):  # auth y shared utils se omiten del conteo disruptivo
                    sections.add(sec)
        return sections


def parse_typescript_file(file_path: Path) -> list[EntityInfo]:
    content = file_path.read_text(encoding="utf-8", errors="ignore")
    lines = content.splitlines()
    entities: list[EntityInfo] = []

    # 1. Buscar clases (ej: export class FooService)
    class_match = re.search(r"export\s+class\s+([A-Za-z0-9_]+)", content)
    # 2. Buscar objetos de servicio (ej: export const tenantService = {)
    object_match = re.search(r"export\s+const\s+([A-Za-z0-9_]+)\s*=\s*\{", content)

    current_entity = None
    if class_match:
        current_entity = EntityInfo(class_match.group(1), "class", file_path)
        entities.append(current_entity)
    elif object_match:
        current_entity = EntityInfo(object_match.group(1), "object", file_path)
        entities.append(current_entity)
    else:
        # Archivo con funciones exportadas sueltas
        current_entity = EntityInfo(file_path.stem, "module", file_path)
        entities.append(current_entity)

    # Patrón para detectar llamadas api.<method>('path') o axios.<method>('path') (Frontend)
    api_call_pattern = re.compile(
        r"(?:api|client|axios|http)\.(get|post|patch|put|delete)\s*(?:<[^>]+>)?\s*\(\s*[`'\"]([^`'\"]+)[`'\"]",
        re.IGNORECASE,
    )
    # Patrón para decorador de controlador NestJS (Backend)
    controller_prefix_match = re.search(r"@Controller\s*\(\s*['\"]([^'\"]*)['\"]\s*\)", content)
    controller_prefix = controller_prefix_match.group(1).strip("/") if controller_prefix_match else ""

    # Patrón para métodos en objetos: async methodName(...) o methodName(...)
    obj_method_pattern = re.compile(r"^\s*(?:async\s+)?([A-Za-z0-9_]+)\s*\([^)]*\)\s*(?::\s*[^=\{]+)?\s*\{")
    # Patrón para métodos en clases: async methodName(...)
    class_method_pattern = re.compile(r"^\s*(?:public\s+|private\s+|protected\s+)?(?:async\s+)?([A-Za-z0-9_]+)\s*\([^)]*\)")
    # Patrón para decoradores HTTP NestJS
    http_decorator_pattern = re.compile(r"@(Get|Post|Patch|Put|Delete)\s*\(\s*(?:['\"]([^'\"]*)['\"])?\s*\)")

    current_method = current_entity.get_or_create_method("global", 1)
    pending_endpoint = None

    for idx, line in enumerate(lines, start=1):
        dec_match = http_decorator_pattern.search(line)
        if dec_match:
            verb = dec_match.group(1).upper()
            subpath = (dec_match.group(2) or "").strip("/")
            full_path = f"/{controller_prefix}/{subpath}".rstrip("/") if subpath else f"/{controller_prefix}"
            pending_endpoint = (verb, full_path)

        m_match = obj_method_pattern.search(line) or class_method_pattern.search(line)
        if m_match and not any(kw in line for kw in ("if", "while", "for", "switch", "catch")):
            candidate = m_match.group(1)
            if candidate not in ("constructor", "get", "set"):
                current_method = current_entity.get_or_create_method(candidate, idx)
                if pending_endpoint:
                    current_method.add_call(pending_endpoint[0], pending_endpoint[1])
                    pending_endpoint = None

        for match in api_call_pattern.finditer(line):
            http_verb = match.group(1)
            path = match.group(2)
            current_method.add_call(http_verb, path)

    # Filtrar métodos sin llamadas API si solo queremos mapear superficie HTTP
    active_methods = {k: v for k, v in current_entity.methods.items() if v.endpoints}
    current_entity.methods = active_methods

    return entities if any(e.methods for e in entities) else []


def is_service_or_controller_file(file_path: Path) -> bool:
    name = file_path.name.lower()
    if any(suffix in name for suffix in (".service.", ".controller.", "api.", "client.")):
        return True
    if any(p in file_path.parts for p in ("services", "controllers", "core", "api")):
        return True
    return False


def scan_directory(target_dir: Path, scan_all: bool = False) -> list[EntityInfo]:
    all_entities: list[EntityInfo] = []
    for ext in ("*.ts", "*.tsx", "*.js"):
        for file_path in target_dir.rglob(ext):
            if any(part in file_path.parts for part in ("node_modules", ".git", "dist", "build", ".next", ".spec.ts", ".test.ts")):
                continue
            if not scan_all and not is_service_or_controller_file(file_path):
                continue
            entities = parse_typescript_file(file_path)
            all_entities.extend(entities)
    return all_entities


def print_listing(entities: list[EntityInfo]):
    print("\n📦 REPORTE EXHAUSTIVO DE PACKAGES, CLASES Y MÉTODOS ANALIZADOS:\n" + "=" * 80)
    by_package: dict[str, list[EntityInfo]] = defaultdict(list)
    for e in entities:
        rel_dir = str(e.file_path.parent)
        by_package[rel_dir].append(e)

    for pkg, entity_list in sorted(by_package.items()):
        print(f"\n📂 Package / Directorio: {pkg}")
        for entity in entity_list:
            sections_badge = f"[{', '.join(sorted(entity.detected_sections))}]" if entity.detected_sections else "[sin llamadas externas]"
            print(f"  ├── 🏷️  {entity.kind.upper()}: {entity.name} (Archivo: {entity.file_path.name}) -> Secciones: {sections_badge}")
            for m_name, m_info in sorted(entity.methods.items()):
                endpoints_str = ", ".join(f"{verb} {path} ({dom})" for verb, path, dom in m_info.endpoints)
                print(f"  │    └── ⚡ Método: {m_name}() [Línea {m_info.line_no}] -> {endpoints_str}")


def check_violations(entities: list[EntityInfo]) -> list[tuple[EntityInfo, set[str]]]:
    violations: list[tuple[EntityInfo, set[str]]] = []
    for entity in entities:
        sections = entity.detected_sections
        # Un servicio no puede manejar más de 1 sección de negocio
        if len(sections) > 1:
            violations.append((entity, sections))
    return violations


def main() -> int:
    parser = argparse.ArgumentParser(description="Validar cohesión y detectar God Services multidominio.")
    parser.add_argument("--dir", default=".", help="Directorio raíz a analizar (por defecto: .)")
    parser.add_argument("--list", action="store_true", help="Listar exhaustivamente todos los packages, clases y métodos.")
    parser.add_argument("--json", action="store_true", help="Salida en formato JSON estructurado.")
    parser.add_argument("--all", action="store_true", help="Escanear todos los archivos ts/tsx/js además de servicios y controladores.")
    args = parser.parse_args()

    target_path = Path(args.dir).resolve()
    if not target_path.exists():
        print(f"❌ Error: El directorio '{target_path}' no existe.", file=sys.stderr)
        return 2

    entities = scan_directory(target_path, scan_all=args.all)

    if args.list:
        print_listing(entities)

    violations = check_violations(entities)

    if args.json:
        result = {
            "total_entities_scanned": len(entities),
            "total_violations": len(violations),
            "violations": [
                {
                    "file": str(v[0].file_path),
                    "entity": v[0].name,
                    "kind": v[0].kind,
                    "sections_involved": sorted(v[1]),
                    "methods_count": len(v[0].methods),
                }
                for v in violations
            ],
        }
        print(json.dumps(result, indent=2))
        return 1 if violations else 0

    print("\n" + "=" * 80)
    print(f"🔍 Auditoría de Cohesión Arquitectónica: {len(entities)} entidades analizadas en '{target_path}'.")

    if not violations:
        print("✅ APROBADO: Todas las clases y servicios cumplen con el principio de Bounded Context (0 God Services detectados).")
        print("=" * 80 + "\n")
        return 0

    print(f"❌ FALLA: Se detectaron {len(violations)} entidades que violan la regla de Bounded Context (God Services):\n")
    for entity, sections in violations:
        print(f"🔴 DESVÍO CRÍTICO: '{entity.name}' ({entity.kind}) en {entity.file_path}")
        print(f"   Concentra {len(sections)} secciones de negocio disjuntas: {sorted(sections)}")
        print("   Métodos infractores detectados:")
        for m_name, m_info in entity.methods.items():
            for verb, path, dom in m_info.endpoints:
                print(f"     * {m_name}() [Línea {m_info.line_no}]: {verb} {path} -> Dominio: {dom}")
        print()

    print("::error::Se detectaron God Services violando la Regla 6 y 7 de aurea-docs.")
    print("=" * 80 + "\n")
    return 1


if __name__ == "__main__":
    sys.exit(main())
