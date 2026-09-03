#!/usr/bin/env python3
"""Validate Aurea 3-level hierarchy (Section -> Page -> Module) and FBAC/RBAC isomorphism.

Reads canonical taxonomy dynamically from `taxonomy/structure.json`.
"""

from __future__ import annotations

import json
import os
import re
import sys
from pathlib import Path

ROOT = Path.cwd()
LEGACY_SHIMS = {"restaurant", "appointments", "inventory", "pos", "clients"}


def error(path: Path | str, message: str) -> None:
    print(f"::error file={path}:{message}", file=sys.stderr)


def load_taxonomy() -> dict[str, set[str]]:
    candidates = [
        ROOT / "docs" / "modules-dynamic" / "taxonomy" / "structure.json",
        Path(__file__).parent / "taxonomy" / "structure.json",
        ROOT.parent / "aurea-docs" / "docs" / "modules-dynamic" / "taxonomy" / "structure.json",
        ROOT / "docs" / "modules-dynamic" / "taxonomy.json",
        Path(__file__).parent / "taxonomy.json",
    ]
    for c in candidates:
        if c.exists():
            try:
                data = json.loads(c.read_text(encoding="utf-8"))
                sections_dict = data.get("sections", {})
                canonical: dict[str, set[str]] = {}
                for sec_key, sec_val in sections_dict.items():
                    pages = set(sec_val.get("pages", {}).keys())
                    canonical[sec_key] = pages
                print(f"📋 Taxonomía oficial cargada desde: {c}")
                return canonical
            except Exception as exc:
                print(f"⚠️ Error al leer {c}: {exc}", file=sys.stderr)

    print("⚠️ Usando taxonomía por defecto", file=sys.stderr)
    return {
        "commerce": {"catalog", "orders", "inventory", "pos"},
        "services": {"bookings"},
        "gastronomy": {"tables", "kitchen", "public"},
        "crm": {"clients"},
        "marketing": {"coupons", "loyalty"},
        "core": {"dashboard", "members", "theme", "billing"},
    }


def validate_backend(sections_dir: Path, canonical_sections: dict[str, set[str]]) -> list[str]:
    problems: list[str] = []

    # 1. Inspect direct subdirectories of src/tenant/sections
    for item in sections_dir.iterdir():
        if not item.is_dir() or item.name.startswith("."):
            continue
        if item.name in LEGACY_SHIMS:
            # Tolerated legacy shim if it re-exports
            continue
        if item.name not in canonical_sections:
            problems.append(
                f"Carpeta de sección '{item.name}' no está registrada en taxonomy/structure.json. "
                f"Secciones válidas: {sorted(canonical_sections.keys())}"
            )
            continue

        allowed_pages = canonical_sections[item.name]
        for page_dir in item.iterdir():
            if not page_dir.is_dir() or page_dir.name.startswith((".", "dto", "contracts", "manifests")):
                continue
            if page_dir.name not in allowed_pages:
                problems.append(
                    f"Página '{page_dir.name}' en sección '{item.name}' no está registrada en taxonomy/structure.json. "
                    f"Páginas autorizadas para '{item.name}': {sorted(allowed_pages)}"
                )

    # 2. Check FeatureDomain isomorphism in controllers
    domain_pattern = re.compile(r"@FeatureDomain\s*\(\s*['\"]([^'\"]+)['\"]\s*\)")

    for controller_file in sections_dir.rglob("*.controller.ts"):
        rel = controller_file.relative_to(sections_dir)
        parts = rel.parts
        if len(parts) >= 2 and parts[0] in canonical_sections:
            section = parts[0]
            page = parts[1]
            content = controller_file.read_text(encoding="utf-8")
            match = domain_pattern.search(content)
            if match:
                domain = match.group(1)
                valid_domains = {f"{section}.{page}", page, f"public.{page}", f"{section}.{page}.public"}
                if domain not in valid_domains and not domain.startswith(f"{section}.{page}."):
                    problems.append(
                        f"Isomorfismo roto en '{rel}': declara @FeatureDomain('{domain}'), "
                        f"pero debe coincidir con '{section}.{page}' o '{page}'."
                    )

    return problems


def validate_frontend(sections_dir: Path, canonical_sections: dict[str, set[str]]) -> list[str]:
    problems: list[str] = []

    for item in sections_dir.iterdir():
        if not item.is_dir() or item.name.startswith("."):
            continue
        if item.name not in canonical_sections:
            problems.append(
                f"Carpeta de sección '{item.name}' no está registrada en taxonomy/structure.json en Frontend. "
                f"Secciones válidas: {sorted(canonical_sections.keys())}"
            )
            continue

        allowed_pages = canonical_sections[item.name]
        for page_dir in item.iterdir():
            if not page_dir.is_dir() or page_dir.name.startswith((".", "components")):
                continue
            if page_dir.name not in allowed_pages:
                problems.append(
                    f"Página '{page_dir.name}' en sección '{item.name}' no está registrada en taxonomy/structure.json. "
                    f"Páginas autorizadas para '{item.name}': {sorted(allowed_pages)}"
                )

    # Check features.ts isomorphism
    feature_pattern = re.compile(r"['\"]([a-zA-Z0-9_-]+\.[a-zA-Z0-9_.-]+)['\"]")
    for features_file in sections_dir.rglob("features.ts"):
        rel = features_file.relative_to(sections_dir)
        parts = rel.parts
        if len(parts) >= 2 and parts[0] in canonical_sections:
            section = parts[0]
            page = parts[1]
            content = features_file.read_text(encoding="utf-8")
            for match in feature_pattern.finditer(content):
                key = match.group(1)
                valid_prefixes = (f"{section}.{page}.", f"{page}.", f"{section}.{page}")
                if not any(key.startswith(p) for p in valid_prefixes):
                    problems.append(
                        f"Isomorfismo roto en '{rel}': feature key '{key}' "
                        f"no pertenece al namespace de '{section}.{page}'."
                    )

    return problems


def main() -> int:
    print("🔍 Iniciando validación canónica de arquitectura e isomorfismo (Sección -> Página -> Módulo)...")
    canonical_sections = load_taxonomy()
    problems: list[str] = []

    # Check for backend sections
    be_sections = ROOT / "src" / "tenant" / "sections"
    if be_sections.exists() and any(be_sections.rglob("*.controller.ts")):
        print(f"📁 Validando Backend en {be_sections.relative_to(ROOT)}...")
        problems.extend(validate_backend(be_sections, canonical_sections))

    # Check for frontend sections
    fe_sections = ROOT / "src" / "tenant" / "sections"
    if fe_sections.exists() and any(fe_sections.rglob("*.tsx")):
        print(f"📁 Validando Frontend en {fe_sections.relative_to(ROOT)}...")
        problems.extend(validate_frontend(fe_sections, canonical_sections))

    if problems:
        print(f"\n❌ Se encontraron {len(problems)} violación(es) de arquitectura:", file=sys.stderr)
        for p in problems:
            error("architecture", p)
        return 1

    print("✅ Arquitectura e isomorfismo 100% conformes con taxonomy/structure.json.")
    return 0


if __name__ == "__main__":
    raise SystemExit(main())
