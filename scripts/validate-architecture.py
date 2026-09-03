#!/usr/bin/env python3
"""Validate Aurea 3-level hierarchy (Section -> Page -> Module) and FBAC/RBAC isomorphism.

Reads canonical taxonomy dynamically from `taxonomy/structure.json`.
Provides detailed failure reporting, GitHub Step Summary and automated PR commenting.
"""

from __future__ import annotations

import json
import os
import re
import sys
import urllib.error
import urllib.request
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


def post_pr_comment(report_md: str) -> None:
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    event_path = os.environ.get("GITHUB_EVENT_PATH")
    if not token or not event_path or not Path(event_path).exists():
        return

    try:
        event = json.loads(Path(event_path).read_text(encoding="utf-8"))
        pr_data = event.get("pull_request")
        if not pr_data:
            return
        pr_number = pr_data.get("number")
        repo_full_name = event.get("repository", {}).get("full_name") or os.environ.get("GITHUB_REPOSITORY")
        if not pr_number or not repo_full_name:
            return

        headers = {
            "Authorization": f"Bearer {token}",
            "Accept": "application/vnd.github.v3+json",
            "User-Agent": "aurea-ci-architecture-bot",
        }

        comments_url = f"https://api.github.com/repos/{repo_full_name}/issues/{pr_number}/comments"
        req = urllib.request.Request(comments_url, headers=headers)
        with urllib.request.urlopen(req) as resp:
            comments = json.loads(resp.read().decode("utf-8"))

        existing_id = None
        marker = "<!-- aurea-architecture-report -->"
        for c in comments:
            if marker in c.get("body", ""):
                existing_id = c["id"]
                break

        body_data = json.dumps({"body": report_md}).encode("utf-8")
        if existing_id:
            update_url = f"https://api.github.com/repos/{repo_full_name}/issues/comments/{existing_id}"
            req = urllib.request.Request(update_url, data=body_data, headers=headers, method="PATCH")
        else:
            req = urllib.request.Request(comments_url, data=body_data, headers=headers, method="POST")

        with urllib.request.urlopen(req) as resp:
            if resp.status in (200, 201):
                print(f"💬 Comentario de diagnóstico publicado/actualizado en PR #{pr_number}")
    except Exception as exc:
        print(f"⚠️ Nota: no se pudo publicar comentario en el PR (permisos o entorno): {exc}", file=sys.stderr)


def report_and_exit(problems: list[str]) -> None:
    for p in problems:
        error("architecture", p)

    lines = [
        "## ❌ Error de Validación de Arquitectura e Isomorfismo",
        "",
        "> [!CAUTION]",
        "> Se detectaron violaciones a la jerarquía canónica (**Sección → Página → Módulo**) o al principio de isomorfismo unificado.",
        "",
        "### 🔍 Fallas Detectadas:",
    ]
    for p in problems:
        lines.append(f"- 🔴 **{p}**")

    lines.extend([
        "",
        "---",
        "",
        "### 💡 ¿Cómo corregir este problema?",
        "",
        "#### 1. Si estás agregando una nueva página legítima:",
        "1. Registrá la página y sus módulos en [`docs/modules-dynamic/taxonomy/structure.json`](https://github.com/aurea-io/aurea-docs/blob/main/docs/modules-dynamic/taxonomy/structure.json) en la sección correspondiente (`commerce`, `services`, `gastronomy`, etc.).",
        "2. Creá el contrato tipado `features.ts` en Frontend y el controlador con `@FeatureDomain('<sección>.<página>')` en Backend.",
        "3. Hacé commit de ambos cambios en tu PR.",
        "",
        "#### 2. Si el archivo o decorador está mal ubicado:",
        "- **Ruta física obligatoria:** `src/tenant/sections/<sección>/<página>/`",
        "- **Backend:** Asegurate de que `@FeatureDomain` declare exactamente `'<sección>.<página>'`.",
        "- **Frontend:** Asegurate de que `features.ts` exporte claves con el prefijo `'<sección>.<página>.'`.",
        "- **Tolerancia Cero:** No uses carpetas paraguas como `restaurant/` o carpetas planas sueltas.",
        "",
        "📖 **Documentación Normativa:** [Regla 7 de Arquitectura en aurea-docs](https://github.com/aurea-io/aurea-docs/blob/main/docs/modules-dynamic/technical.md)",
        "",
        "<!-- aurea-architecture-report -->",
    ])
    report_md = "\n".join(lines)

    step_summary = os.environ.get("GITHUB_STEP_SUMMARY")
    if step_summary:
        try:
            with open(step_summary, "a", encoding="utf-8") as f:
                f.write("\n" + report_md + "\n")
        except Exception as exc:
            print(f"⚠️ No se pudo escribir en GITHUB_STEP_SUMMARY: {exc}", file=sys.stderr)

    post_pr_comment(report_md)
    print(f"\n❌ Se encontraron {len(problems)} violación(es) de arquitectura.", file=sys.stderr)
    sys.exit(1)


def main() -> int:
    print("🔍 Iniciando validación canónica de arquitectura e isomorfismo (Sección -> Página -> Módulo)...")
    canonical_sections = load_taxonomy()
    problems: list[str] = []

    be_sections = ROOT / "src" / "tenant" / "sections"
    if be_sections.exists() and any(be_sections.rglob("*.controller.ts")):
        print(f"📁 Validando Backend en {be_sections.relative_to(ROOT)}...")
        problems.extend(validate_backend(be_sections, canonical_sections))

    fe_sections = ROOT / "src" / "tenant" / "sections"
    if fe_sections.exists() and any(fe_sections.rglob("*.tsx")):
        print(f"📁 Validando Frontend en {fe_sections.relative_to(ROOT)}...")
        problems.extend(validate_frontend(fe_sections, canonical_sections))

    if problems:
        report_and_exit(problems)

    print("✅ Arquitectura e isomorfismo 100% conformes con taxonomy/structure.json.")
    return 0


if __name__ == "__main__":
    main()
