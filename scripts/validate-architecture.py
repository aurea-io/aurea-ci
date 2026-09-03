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


def error(path: Path | str, message: str) -> None:
    print(f"::error file={path}:{message}", file=sys.stderr)


def load_taxonomy() -> tuple[dict[str, set[str]], dict[str, str]]:
    def parse(data: dict) -> tuple[dict[str, set[str]], dict[str, str]]:
        sections_dict = data.get("sections", {})
        canonical: dict[str, set[str]] = {}
        paths: dict[str, str] = {}
        for sec_key, sec_val in sections_dict.items():
            canonical[sec_key] = set(sec_val.get("pages", {}).keys())
            for page_key, page_val in sec_val.get("pages", {}).items():
                p_path = page_val.get("path") or f"/{sec_key}/{page_key}"
                paths[f"{sec_key}.{page_key}"] = p_path
        return canonical, paths

    # 1. Look for taxonomy/structure.json in aurea-docs (workspace or .aurea-docs checkout)
    candidates = [
        ROOT / "docs" / "modules-dynamic" / "taxonomy" / "structure.json",
        ROOT / ".aurea-docs" / "docs" / "modules-dynamic" / "taxonomy" / "structure.json",
        ROOT.parent / "aurea-docs" / "docs" / "modules-dynamic" / "taxonomy" / "structure.json",
    ]
    for c in candidates:
        if c.exists():
            try:
                data = json.loads(c.read_text(encoding="utf-8"))
                canonical, paths = parse(data)
                print(f"📋 Taxonomía oficial cargada desde aurea-docs: {c}")
                return canonical, paths
            except Exception as exc:
                print(f"⚠️ Error al leer {c}: {exc}", file=sys.stderr)

    # 2. In CI: fetch live from aurea-io/aurea-docs repository via GitHub API / Raw
    token = os.environ.get("GITHUB_TOKEN") or os.environ.get("GH_TOKEN")
    url = "https://raw.githubusercontent.com/aurea-io/aurea-docs/main/docs/modules-dynamic/taxonomy/structure.json"
    try:
        req = urllib.request.Request(url)
        if token:
            req.add_header("Authorization", f"Bearer {token}")
        with urllib.request.urlopen(req, timeout=10) as resp:
            data = json.loads(resp.read().decode("utf-8"))
            canonical, paths = parse(data)
            print("📋 Taxonomía oficial descargada dinámicamente desde aurea-io/aurea-docs")
            return canonical, paths
    except Exception as exc:
        print(f"⚠️ No se pudo obtener taxonomy desde aurea-docs: {exc}", file=sys.stderr)

    print("⚠️ Usando taxonomía por defecto", file=sys.stderr)
    default_sections = {
        "commerce": {"catalog", "orders", "inventory", "pos"},
        "services": {"bookings"},
        "gastronomy": {"tables", "kitchen", "public"},
        "crm": {"clients"},
        "marketing": {"coupons", "loyalty"},
        "core": {"dashboard", "members", "theme", "billing"},
    }
    default_paths = {f"{s}.{p}": f"/{s}/{p}" for s, pages in default_sections.items() for p in pages}
    return default_sections, default_paths


def validate_backend(sections_dir: Path, canonical_sections: dict[str, set[str]]) -> list[str]:
    problems: list[str] = []

    # 1. Inspect direct subdirectories of src/tenant/sections
    for item in sections_dir.iterdir():
        if not item.is_dir() or item.name.startswith("."):
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


def validate_page_routes(root: Path, canonical_sections: dict[str, set[str]], canonical_paths: dict[str, str]) -> list[str]:
    problems: list[str] = []

    # Mapping of known page keys to section
    page_to_section: dict[str, str] = {}
    for sec, pages in canonical_sections.items():
        for p in pages:
            page_to_section[p] = sec

    dirs_to_check = [root]
    for sub in ["business-backend", "business-frontend", "admin-backend", "admin-frontend"]:
        p = root / sub
        if p.exists() and p.is_dir():
            dirs_to_check.append(p)

    for d in dirs_to_check:
        # 1. Inspect Backend tenant.service.ts
        tenant_service = d / "src" / "tenant" / "core" / "tenant.service.ts"
        if tenant_service.exists():
            content = tenant_service.read_text(encoding="utf-8")
            legacy_flat_tokens = [
                "'/appointments'",
                '"/appointments"',
                "'/restaurant'",
                '"/restaurant"',
                "`/${pageKey}`",
                "'/${pageKey}'",
                '"/${pageKey}"',
            ]
            for token in legacy_flat_tokens:
                if token in content:
                    try:
                        rel_file = tenant_service.relative_to(ROOT)
                    except ValueError:
                        rel_file = tenant_service
                    problems.append(
                        f"Ruta de página plana o no canónica detectada en {rel_file}: {token}. "
                        f"Toda ruta de página debe tener como prefijo su sección canónica '/${{sectionKey}}/${{pageKey}}' según Regla 4.5 de aurea-docs."
                    )

        # 2. Inspect Frontend App.tsx
        app_tsx = d / "src" / "App.tsx"
        if app_tsx.exists():
            content = app_tsx.read_text(encoding="utf-8")
            route_pattern = re.compile(r'<Route\s+path=["\']([^"\']+)["\']')
            try:
                rel_app = app_tsx.relative_to(ROOT)
            except ValueError:
                rel_app = app_tsx

            for match in route_pattern.finditer(content):
                path_val = match.group(1).strip("/")
                if not path_val or path_val in ("login", "register", "auth/magic", "auth/google/callback", "auth/forgot-password", "auth/reset-password") or path_val.startswith(("public/", "superadmin", "preview/")):
                    continue

                parts = path_val.split("/")
                # If path has only 1 part (flat route like "bookings", "catalog", "orders", etc.)
                if len(parts) == 1:
                    page_key = parts[0]
                    if page_key in page_to_section:
                        sec = page_to_section[page_key]
                        canonical_path = canonical_paths.get(f"{sec}.{page_key}", f"/{sec}/{page_key}")
                        problems.append(
                            f"Ruta de página plana sin prefijo de sección en {rel_app}: path='{match.group(1)}'. "
                            f"Debe incluir su sección canónica: path='{canonical_path.lstrip('/')}' (o '{canonical_path}') según Regla 4.5 de aurea-docs."
                        )
                elif len(parts) >= 2:
                    sec, page = parts[0], parts[1]
                    if sec in canonical_sections and page not in canonical_sections[sec]:
                        problems.append(
                            f"Ruta no canónica en {rel_app}: la página '{page}' en ruta '{match.group(1)}' "
                            f"no pertenece a la sección '{sec}' registrada en taxonomy/structure.json."
                        )

        # 3. Inspect manifests with path property
        for manifest_file in d.rglob("*manifest*.ts"):
            if any(ign in manifest_file.parts for ign in ("node_modules", "dist", ".git")):
                continue
            try:
                content = manifest_file.read_text(encoding="utf-8")
                path_match = re.search(r'path:\s*["\']([^"\']+)["\']', content)
                if path_match:
                    p_val = path_match.group(1)
                    p_parts = p_val.strip("/").split("/")
                    if len(p_parts) == 1 and p_parts[0] in page_to_section:
                        sec = page_to_section[p_parts[0]]
                        try:
                            rel_m = manifest_file.relative_to(ROOT)
                        except ValueError:
                            rel_m = manifest_file
                        problems.append(
                            f"Manifiesto {rel_m} declara ruta plana '{p_val}'. "
                            f"Debe incluir la sección canónica '/{sec}/{p_parts[0]}' según Regla 4.5."
                        )
            except Exception:
                pass

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


scripts_dir = Path(__file__).resolve().parent
if str(scripts_dir) not in sys.path:
    sys.path.insert(0, str(scripts_dir))

try:
    from validate_services_cohesion import scan_directory, check_violations
    has_cohesion_checker = True
except ImportError:
    has_cohesion_checker = False


def main() -> int:
    print("🔍 Iniciando validación canónica de arquitectura e isomorfismo (Sección -> Página -> Módulo)...")
    canonical_sections, canonical_paths = load_taxonomy()
    problems: list[str] = []

    be_sections = ROOT / "src" / "tenant" / "sections"
    if be_sections.exists() and any(be_sections.rglob("*.controller.ts")):
        print(f"📁 Validando Backend en {be_sections.relative_to(ROOT)}...")
        problems.extend(validate_backend(be_sections, canonical_sections))

    fe_sections = ROOT / "src" / "tenant" / "sections"
    if fe_sections.exists() and any(fe_sections.rglob("*.tsx")):
        print(f"📁 Validando Frontend en {fe_sections.relative_to(ROOT)}...")
        problems.extend(validate_frontend(fe_sections, canonical_sections))

    print("📁 Validando rutas canónicas jerárquicas (/<sección>/<página>)...")
    problems.extend(validate_page_routes(ROOT, canonical_sections, canonical_paths))

    src_dir = ROOT / "src"
    if has_cohesion_checker and src_dir.exists():
        print(f"📁 Validando cohesión de servicios y detección de God Services en {src_dir.relative_to(ROOT)}...")
        entities = scan_directory(src_dir)
        violations = check_violations(entities)
        for ent, sections in violations:
            try:
                rel_file = ent.file_path.relative_to(ROOT)
            except ValueError:
                rel_file = ent.file_path
            problems.append(
                f"God Service detectado en '{rel_file}' ({ent.name}): "
                f"concentra {len(sections)} dominios de negocio disjuntos {sorted(sections)}. "
                f"Debe desacoplarse en servicios por Bounded Context según la Regla 6 y 7."
            )

    if problems:
        report_and_exit(problems)

    print("✅ Arquitectura e isomorfismo 100% conformes con taxonomy/structure.json.")
    return 0


if __name__ == "__main__":
    main()
